"""Explicit bounded execution producer; stored commands remain inert."""

from __future__ import annotations

from copy import deepcopy
import ctypes
from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Final, Protocol

if __package__:
    from tools import feature_program as core
    from tools import feature_program_inputs as inputs
    from tools import feature_program_verification as verification
else:
    import feature_program as core
    import feature_program_inputs as inputs
    import feature_program_verification as verification

Json = core.Json
Record = core.Record
DEFAULT_TIMEOUT: Final = 300.0
DEFAULT_STREAM_LIMIT: Final = 1024 * 1024
MAX_STREAM_LIMIT: Final = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES: Final = 8 * 1024 * 1024
MAX_ARTIFACT_TOTAL: Final = 32 * 1024 * 1024
MAX_ARTIFACTS: Final = 16
MAX_EXECUTABLE_BYTES: Final = 128 * 1024 * 1024
MAX_CAPTURE_BYTES: Final = 8 * 1024 * 1024
SAFE_ENVIRONMENT_KEYS: Final = (
    "PATH",
    "PATHEXT",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "LANG",
    "LC_ALL",
    "TZ",
)


class ExecutionError(core.ProgramError):
    """Preparation/capture failed without granting acceptance or replay authority."""


class ProgramAccess(Protocol):
    """Existing host capabilities, deliberately excluding canonical mutations."""

    repo: Path
    directory: Path

    def path(self, relative: str) -> Path: ...
    def require_current_location(self) -> None: ...
    def task(self, state: Record, identifier: str) -> Record: ...
    def current_attempt(self, task: Record) -> Record: ...
    def verify_refs(self, refs: list[Json], budget: core.ReadBudget | None = None) -> None: ...
    def source_snapshot(
        self, scopes: list[str], budget: core.ReadBudget | None = None
    ) -> Record: ...


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """One reserved invocation; an exclusive execution marker prevents replay."""

    program: ProgramAccess
    invocation: Record
    binding: Record
    request: Record
    manifest: Record
    environment: Record
    before: Record
    directory: str
    start_ref: Record


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ExecutionError(reason)


def _hash_executable(path: Path) -> str:
    checksum = hashlib.sha256()
    length = 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            length += len(chunk)
            _require(length <= MAX_EXECUTABLE_BYTES, "Executable exceeds fingerprint budget")
            checksum.update(chunk)
    return checksum.hexdigest()


def _argv(value: Json) -> list[str]:
    parts = core.sequence(value, "live argv")
    _require(bool(parts) and len(parts) <= 1024, "Live argv requires 1..1024 arguments")
    result: list[str] = []
    for index, part in enumerate(parts):
        _require(isinstance(part, str), "Every live argv argument must be text")
        _require("\0" not in part and (index > 0 or bool(part.strip())), "Invalid live argv")
        result.append(part)
    _require(sum(len(part.encode("utf-8")) for part in result) <= 65536, "Live argv too large")
    return result


def _executable(program: ProgramAccess, argv: list[str]) -> Path:
    supplied = Path(argv[0])
    if supplied.is_absolute():
        resolved = supplied.resolve()
    elif supplied.parent != Path("."):
        resolved = core.safe_path(program.repo, supplied.as_posix()).resolve()
    else:
        found = shutil.which(argv[0])
        _require(found is not None, "Explicit executable could not be resolved")
        resolved = Path(found).resolve()
    _require(resolved.is_file(), "Explicit executable must be a regular file")
    _require(
        resolved.suffix.casefold() not in {".bat", ".cmd", ".ps1"},
        "Implicit shell wrappers are unsupported; supply an explicit interpreter argv",
    )
    return resolved


def observe_environment(
    program: ProgramAccess, live_argv: Json, declared: Record | None = None
) -> Record:
    """Hash explicit executable/safe environment identities without invoking a probe."""
    executable = _executable(program, _argv(live_argv))
    supplied = (
        deepcopy(declared)
        if declared is not None
        else {"facts": {}, "unknowns": ["ambient_environment"]}
    )
    _require(set(supplied) <= {"facts", "unknowns", "label"}, "Invalid environment fields")
    facts = deepcopy(core.record(supplied.get("facts", {}), "environment facts"))
    for name, fact in facts.items():
        _require(isinstance(fact, str), f"Environment fact {name} must be text")
    unknowns = core.strings(supplied.get("unknowns", []), "environment unknowns")
    facts.update(
        {
            "executable_path": str(executable),
            "executable_sha256": _hash_executable(executable),
            "runner_python": sys.version,
            "platform": sys.platform,
        }
    )
    for name in SAFE_ENVIRONMENT_KEYS:
        value = os.environ.get(name)
        facts["env_" + name] = core.sha(value.encode("utf-8")) if value is not None else "missing"
    result: Record = {"facts": facts, "unknowns": unknowns}
    if "label" in supplied:
        result["label"] = core.string(supplied["label"], "environment label")
    return result


def _write_new(program: ProgramAccess, name: str, content: bytes) -> Record:
    _require(len(content) <= MAX_CAPTURE_BYTES, "Execution evidence exceeds file budget")
    path = program.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with program.path(name).open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": name, "sha256": core.sha(content)}


def _write_json(program: ProgramAccess, name: str, value: Record) -> Record:
    return _write_new(program, name, core.canonical(value))


def _request(state: Record, binding: Record) -> Record:
    extension = core.record(state.get("verification"), "verification")
    records = [
        core.record(item, "verification record")
        for item in core.sequence(extension.get("records"), "verification records")
    ]
    identifier = core.string(binding.get("request_id"), "request_id")
    matches = [item for item in records if item["input"]["id"] == identifier]
    _require(len(matches) == 1, "Exact adopted verification request required")
    request = matches[0]
    value = core.record(request["input"], "request input")
    _require(value["kind"] == "request", "Execution binding must name a request event")
    _require(value["task_id"] == binding["task_id"], "Execution request task mismatch")
    _require(request["request_version"] == binding["request_version"], "Request version changed")
    lane_requests = [
        item
        for item in records
        if item["input"]["kind"] == "request"
        and item["input"]["task_id"] == binding["task_id"]
        and item["input"]["lane"] == value["lane"]
    ]
    _require(lane_requests[-1]["request_id"] == identifier, "Execution request was superseded")
    return request


def prepare_run(
    program: ProgramAccess, state: Record, invocation: Record, request_binding: Record
) -> PreparedRun:
    """Guard live argv and reserve only its task-local durable start evidence."""
    program.require_current_location()
    binding = deepcopy(request_binding)
    _require(
        binding.get("expected_revision") == core.phases.revision(state),
        "Stale execution registry revision",
    )
    task_id = core.string(binding.get("task_id"), "task_id")
    actor = core.string(binding.get("actor_id"), "actor_id")
    core.string(binding.get("reason"), "execution reason")
    core.string(binding.get("next_action"), "execution next_action")
    _require(type(binding.get("request_version")) is int, "Request version must be integer")
    request = _request(state, binding)
    task = program.task(state, task_id)
    verification._authority(state, task, {**binding, "kind": "finished"}, request)
    program.verify_refs(core.sequence(request["evidence"], "request evidence"))
    context = core.record(request["context"], "request context")
    attempt = program.current_attempt(task)
    _require(context["attempt"] == attempt["number"], "Execution request attempt changed")
    _require(task.get("disposition") not in {"split", "retired"}, "Disposed execution task")
    _require(
        set(invocation)
        <= {
            "argv",
            "cwd",
            "execution_kind",
            "timeout_seconds",
            "stream_limit_bytes",
            "capture_dir",
            "artifacts",
        },
        "Unknown live invocation fields",
    )
    argv = _argv(invocation.get("argv"))
    executable = _executable(program, argv)
    cwd_name = core.string(invocation.get("cwd", "."), "cwd")
    cwd = program.repo if cwd_name == "." else core.safe_path(program.repo, cwd_name)
    _require(cwd.is_dir(), "Execution cwd must be an existing repository directory")
    kind = core.string(invocation.get("execution_kind", "test"), "execution_kind")
    _require(kind in {"native", "test", "static", "compile"}, "Unsupported command execution kind")
    timeout = invocation.get("timeout_seconds", DEFAULT_TIMEOUT)
    _require(
        type(timeout) in {int, float} and math.isfinite(timeout) and 0 < timeout <= 3600,
        "Execution timeout must be finite and within (0, 3600] seconds",
    )
    stream_limit = invocation.get("stream_limit_bytes", DEFAULT_STREAM_LIMIT)
    _require(
        type(stream_limit) is int and 0 < stream_limit <= MAX_STREAM_LIMIT,
        "Stream limit must be within 1..8388608 bytes",
    )
    artifacts = core.strings(invocation.get("artifacts", []), "selected artifacts")
    _require(len(artifacts) <= MAX_ARTIFACTS, "At most 16 selected artifacts are supported")
    for artifact in artifacts:
        core.safe_path(program.repo, artifact)
    directory = core.safe_relative(core.string(invocation.get("capture_dir"), "capture_dir"))
    prefix = f"evidence/{task_id}/attempt-{attempt['number']}/"
    _require(directory.startswith(prefix), "Execution capture must be task/attempt-local")
    run_id = core.string(binding.get("run_id", Path(directory).name), "run_id")
    _require(core.portable_task_id(run_id), "Execution run_id must be portable")
    _require(not program.path(directory).exists(), "Execution capture directory already exists")
    manifest = inputs.normalize_manifest(context.get("input_manifest"))
    expected = core.record(context.get("input_capture"), "frozen expected input capture")
    environment = observe_environment(
        program, argv, core.record(expected.get("environment"), "expected environment")
    )
    method: Record = {"argv": argv, "cwd": str(cwd), "execution_kind": kind}
    before = inputs.capture_inputs(program, manifest, environment=environment, method=method)
    _require(
        before["input_digest"] == context["input_digest"],
        "Actual execution inputs/environment/method differ from adopted request",
    )
    frozen_invocation: Record = {
        "argv": argv,
        "executable": str(executable),
        "cwd": str(cwd),
        "execution_kind": kind,
        "timeout_seconds": timeout,
        "stream_limit_bytes": stream_limit,
        "artifacts": artifacts,
    }
    binding.update({"actor_id": actor, "run_id": run_id, "attempt": attempt["number"]})
    start: Record = {
        "schema_version": 1,
        "producer": "feature_program_execution",
        "run_id": run_id,
        "task_id": task_id,
        "attempt": attempt["number"],
        "actor_id": actor,
        "request_id": binding["request_id"],
        "request_version": binding["request_version"],
        "origin_expected_revision": binding["expected_revision"],
        "request_receipt": request["receipt"],
        "request_context_digest": core.digest(context),
        "invocation": frozen_invocation,
        "method": method,
        "environment": environment,
        "prepared_at": core.utc(),
        "phase": "prepared",
        "supervisor_pid": os.getpid(),
        "process": {
            "outcome": "not_started",
            "exit_code": None,
            "started_at": None,
            "ended_at": None,
        },
        "next_action": binding["next_action"],
    }
    program.path(directory).parent.mkdir(parents=True, exist_ok=True)
    program.path(directory).mkdir()
    start_ref = _write_json(program, directory + "/start.json", start)
    return PreparedRun(
        program,
        frozen_invocation,
        binding,
        request,
        manifest,
        environment,
        before,
        directory,
        start_ref,
    )


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.c_uint32),
        ("scheduling", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("usage", ctypes.c_uint32),
        ("thread_id", ctypes.c_uint32),
        ("process_id", ctypes.c_uint32),
        ("base_priority", ctypes.c_int32),
        ("delta_priority", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
    ]


class _JobAccounting(ctypes.Structure):
    _fields_ = [
        ("total_user", ctypes.c_int64),
        ("total_kernel", ctypes.c_int64),
        ("period_user", ctypes.c_int64),
        ("period_kernel", ctypes.c_int64),
        ("page_faults", ctypes.c_uint32),
        ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32),
        ("terminated_processes", ctypes.c_uint32),
    ]


class _WindowsJob:
    """Attach a suspended child to a kill-on-close job before child code can run."""

    def __init__(self) -> None:
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
            "SetInformationJobObject": (
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
                ctypes.c_int,
            ),
            "OpenProcess": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
            "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            "TerminateJobObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
            "CreateToolhelp32Snapshot": ([ctypes.c_uint32, ctypes.c_uint32], ctypes.c_void_p),
            "Thread32First": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            "Thread32Next": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            "OpenThread": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
            "ResumeThread": ([ctypes.c_void_p], ctypes.c_uint32),
            "QueryInformationJobObject": (
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
                ctypes.c_int,
            ),
            "WaitForSingleObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
        }
        for name, (arguments, returns) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = returns
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, pid: int) -> None:
        process_handle = self.api.OpenProcess(0x0101, False, pid)
        if not process_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process_handle)
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            present = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.process_id == pid:
                    thread_handle = self.api.OpenThread(0x0002, False, entry.thread_id)
                    if not thread_handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread_handle) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread_handle)
                    return
                present = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise ExecutionError("Suspended child thread could not be found")
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self) -> None:
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 5
        while True:
            accounting = _JobAccounting()
            if not self.api.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.active_processes == 0:
                return
            _require(time.monotonic() < deadline, "Windows descendant cleanup did not finish")
            threading.Event().wait(0.01)

    def close(self) -> None:
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


@dataclass(slots=True)
class _StreamCapture:
    """One thread owns this bounded stream accumulator until its completion event."""

    source: BinaryIO
    destination: Path
    limit: int
    done: threading.Event
    seen: int = 0
    retained: int = 0
    error: str | None = None

    def drain(self) -> None:
        try:
            with self.source, self.destination.open("xb") as output:
                while chunk := os.read(self.source.fileno(), 65536):
                    self.seen += len(chunk)
                    selected = chunk[: max(0, self.limit - self.retained)]
                    output.write(selected)
                    output.flush()
                    self.retained += len(selected)
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.done.set()


def _kill_group(pid: int) -> None:
    _require(
        hasattr(os, "killpg") and hasattr(signal, "SIGKILL"),
        "This platform cannot supervise an owned process group",
    )
    getattr(os, "killpg")(pid, getattr(signal, "SIGKILL"))


def _terminate(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    if job is not None:
        job.terminate()
    else:
        try:
            _kill_group(process.pid)
        except ProcessLookupError:
            pass  # Exact original process group already exited; never fall back to another PID.
    if process.poll() is None:
        process.kill()  # Also covers an attributed suspended child before job assignment.
    process.wait(timeout=5)


def _capture_artifacts(prepared: PreparedRun) -> Record:
    requested = core.strings(prepared.invocation["artifacts"], "artifacts")
    captured: list[Json] = []
    missing: list[Json] = []
    remaining = MAX_ARTIFACT_TOTAL
    for index, name in enumerate(requested):
        try:
            path = core.safe_path(prepared.program.repo, name)
            _require(path.is_file(), "Selected artifact is missing or is not a regular file")
            content = core.bounded_bytes(path, min(MAX_ARTIFACT_BYTES, remaining))
            remaining -= len(content)
            _require(
                core.bounded_bytes(core.safe_path(prepared.program.repo, name), MAX_ARTIFACT_BYTES)
                == content,
                "Selected artifact changed during capture",
            )
            ref = _write_new(
                prepared.program, f"{prepared.directory}/artifact-{index + 1:03d}", content
            )
            captured.append({"source_path": name, **ref, "bytes": len(content)})
        except (OSError, core.ProgramError) as exc:
            missing.append({"path": name, "reason": str(exc)})
    return {"requested": requested, "captured": captured, "missing": missing}


def _popen(prepared: PreparedRun) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        core.sequence(prepared.invocation["argv"], "live argv"),
        executable=core.string(prepared.invocation["executable"], "executable"),
        cwd=core.string(prepared.invocation["cwd"], "cwd"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        creationflags=(0x0004 | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


class _LaunchPending(ExecutionError):
    """Native launch has not yielded an attributable child; cleanup remains unresolved."""


@dataclass(slots=True)
class _WindowsSpawn:
    """Locked ownership handoff for a suspended native launch that may return late."""

    ready: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    aborted: bool = False
    process: subprocess.Popen[bytes] | None = None
    error: OSError | core.ProgramError | None = None

    def launch(self, prepared: PreparedRun) -> None:
        try:
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.SetThreadErrorMode.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            api.SetThreadErrorMode.restype = ctypes.c_int
            if not api.SetThreadErrorMode(0x8003, None):
                raise ctypes.WinError(ctypes.get_last_error())
            candidate = _popen(prepared)
            with self.lock:
                if not self.aborted:
                    self.process = candidate
                    self.ready.set()
                    return  # The supervising thread now exclusively owns this suspended child.
            diagnostics: Record = {
                "schema_version": 1,
                "run_id": prepared.binding["run_id"],
                "pid": candidate.pid,
                "resolved_at": core.utc(),
                "outcome": "terminated_before_resume",
                "cleanup_status": "complete",
                "start_ref": prepared.start_ref,
            }
            try:
                candidate.kill()
                candidate.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                diagnostics.update(cleanup_status="unresolved", reason=str(exc))
            finally:
                if candidate.stdout is not None:
                    candidate.stdout.close()
                if candidate.stderr is not None:
                    candidate.stderr.close()
            _write_json(
                prepared.program, prepared.directory + "/launch-resolution.json", diagnostics
            )
        except (OSError, core.ProgramError) as exc:
            with self.lock:
                self.error = exc
                self.ready.set()


def _windows_spawn(
    prepared: PreparedRun, began: float, cancel: threading.Event | None
) -> subprocess.Popen[bytes]:
    handoff = _WindowsSpawn()
    threading.Thread(target=handoff.launch, args=(prepared,), daemon=True).start()
    try:
        while not handoff.ready.wait(0.025):
            if (
                cancel is not None and cancel.is_set()
            ) or time.monotonic() - began >= prepared.invocation["timeout_seconds"]:
                with handoff.lock:
                    if handoff.ready.is_set():
                        break
                    handoff.aborted = True
                raise _LaunchPending(
                    "Native Windows launch has not returned; outcome/cleanup remain unknown. "
                    "A late suspended child will not be resumed; inspect exact owned launch "
                    "and any immutable launch-resolution diagnostic before record-only recovery."
                )
    except KeyboardInterrupt:
        with handoff.lock:
            handoff.aborted = True
            candidate = handoff.process
        if candidate is not None:
            candidate.kill()
            candidate.wait(timeout=5)
            if candidate.stdout is not None:
                candidate.stdout.close()
            if candidate.stderr is not None:
                candidate.stderr.close()
        raise _LaunchPending("Native Windows launch interrupted; no child was resumed") from None
    if handoff.error is not None:
        raise handoff.error
    _require(handoff.process is not None, "Native launch completed without a process handle")
    if (cancel is not None and cancel.is_set()) or time.monotonic() - began >= prepared.invocation[
        "timeout_seconds"
    ]:
        candidate = handoff.process
        candidate.kill()
        candidate.wait(timeout=5)
        if candidate.stdout is not None:
            candidate.stdout.close()
        if candidate.stderr is not None:
            candidate.stderr.close()
        _write_json(
            prepared.program,
            prepared.directory + "/launch-resolution.json",
            {
                "schema_version": 1,
                "run_id": prepared.binding["run_id"],
                "pid": candidate.pid,
                "resolved_at": core.utc(),
                "outcome": "terminated_before_resume",
                "cleanup_status": "complete",
                "start_ref": prepared.start_ref,
            },
        )
        raise _LaunchPending(
            "Native Windows launch returned after cancellation/deadline; "
            "the exact suspended child was terminated before resume"
        )
    return handoff.process


def execute_run(prepared: PreparedRun, *, cancel: threading.Event | None = None) -> Record:
    """Execute one explicit prepared argv without a canonical lock or automatic retry."""
    program = prepared.program
    directory = prepared.directory
    program.verify_refs([prepared.start_ref])
    start = core.read_json(program.path(str(prepared.start_ref["path"])))
    _require(
        start["invocation"] == prepared.invocation,
        "Live invocation differs from its immutable preflight reservation",
    )
    program.verify_refs(core.sequence(prepared.request["evidence"], "request evidence"))
    _write_json(
        program,
        directory + "/invocation.json",
        {
            "schema_version": 1,
            "run_id": prepared.binding["run_id"],
            "invocation_authorized_at": core.utc(),
            "start_ref": prepared.start_ref,
        },
    )
    before_ref: Record | None = None
    after_ref: Record | None = None
    comparison: Record = {"status": "not_assessed", "reasons": ["Capture unavailable"]}
    reasons: list[str] = []
    outcome = "not_started"
    exit_code: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    elapsed: float | None = None
    cleanup_status = "not_started"
    streams: dict[str, _StreamCapture] = {}
    process: subprocess.Popen[bytes] | None = None
    job: _WindowsJob | None = None
    method: Record = {
        "argv": prepared.invocation["argv"],
        "cwd": prepared.invocation["cwd"],
        "execution_kind": prepared.invocation["execution_kind"],
    }
    try:
        program.require_current_location()
        environment = observe_environment(
            program, prepared.invocation["argv"], prepared.environment
        )
        before = inputs.capture_inputs(
            program, prepared.manifest, environment=environment, method=method
        )
        before_ref = _write_json(program, directory + "/inputs-before.json", before)
        _require(
            before["input_digest"] == prepared.before["input_digest"],
            "Execution inputs changed after preflight; no command launched",
        )
        if cancel is not None and cancel.is_set():
            outcome = "canceled"
            reasons.append("Cancellation requested before launch")
        else:
            if os.name == "nt":
                job = _WindowsJob()
            began = time.monotonic()
            launch_time = core.utc()
            try:
                process = (
                    _windows_spawn(prepared, began, cancel) if os.name == "nt" else _popen(prepared)
                )
                cleanup_status = "unresolved"
                started_at = launch_time
                _write_json(
                    program,
                    directory + "/spawn.json",
                    {
                        "schema_version": 1,
                        "pid": process.pid,
                        "started_at": started_at,
                        "clock_id": "runner-utc",
                        "start_ref": prepared.start_ref,
                    },
                )
                if job is not None:
                    job.attach_and_resume(process.pid)
                _require(
                    process.stdout is not None and process.stderr is not None,
                    "Subprocess output pipes unavailable",
                )
                for name, source in (("stdout", process.stdout), ("stderr", process.stderr)):
                    stream = _StreamCapture(
                        source,
                        program.path(f"{directory}/{name}.bin"),
                        prepared.invocation["stream_limit_bytes"],
                        threading.Event(),
                    )
                    streams[name] = stream
                    threading.Thread(target=stream.drain, daemon=True).start()
                outcome = "exited"
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        outcome = "canceled"
                        _terminate(process, job)
                        break
                    if time.monotonic() - began >= prepared.invocation["timeout_seconds"]:
                        outcome = "timed_out"
                        _terminate(process, job)
                        break
                    try:
                        process.wait(timeout=0.025)
                    except subprocess.TimeoutExpired:
                        continue
                exit_code = process.returncode
                cleanup_status = "complete"
            except _LaunchPending as exc:
                outcome = "outcome_unknown"
                cleanup_status = "unresolved"
                reasons.append(str(exc))
            except KeyboardInterrupt:
                outcome = "interrupted"
                if process is not None:
                    _terminate(process, job)
                    exit_code = process.returncode
                    cleanup_status = "complete"
            except (OSError, core.ProgramError, subprocess.TimeoutExpired) as exc:
                outcome = "spawn_failed" if process is None else "interrupted"
                reasons.append(f"{type(exc).__name__}: {exc}")
                if process is not None:
                    try:
                        _terminate(process, job)
                        exit_code = process.returncode
                        cleanup_status = "complete"
                    except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                        cleanup_status = "unresolved"
                        reasons.append(f"Child cleanup: {cleanup_error}")
            finally:
                if job is not None:
                    try:
                        job.terminate()
                    except (OSError, core.ProgramError) as exc:
                        cleanup_status = "unresolved"
                        reasons.append(f"Job termination: {exc}")
                    try:
                        job.close()
                    except OSError as exc:
                        cleanup_status = "unresolved"
                        reasons.append(f"Job close: {exc}")
                elif process is not None:
                    try:
                        _kill_group(process.pid)
                    except ProcessLookupError:
                        pass  # Exact original process group no longer exists.
                ended_at = core.utc()
                elapsed = time.monotonic() - began
                for name, stream in streams.items():
                    if not stream.done.wait(timeout=5):
                        cleanup_status = "unresolved"
                        reasons.append(f"{name} output drain did not finish")
                    elif stream.error is not None:
                        reasons.append(f"{name} capture: {stream.error}")
                if process is not None and not streams:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
    except KeyboardInterrupt:
        outcome = "interrupted"
        reasons.append("Execution interrupted before launch or during input capture")
    except (OSError, core.ProgramError) as exc:
        reasons.append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            environment_after = observe_environment(
                program, prepared.invocation["argv"], prepared.environment
            )
            after = inputs.capture_inputs(
                program, prepared.manifest, environment=environment_after, method=method
            )
            after_ref = _write_json(program, directory + "/inputs-after.json", after)
            if before_ref is not None:
                before_saved = core.read_json(program.path(str(before_ref["path"])))
                comparison = inputs.compare_inputs(before_saved, after)
                if comparison["status"] != "unchanged":
                    reasons.extend(core.strings(comparison.get("reasons", []), "input reasons"))
                    reasons.append("Executed inputs are changed or not fully assessed")
        except (OSError, core.ProgramError) as exc:
            reasons.append(f"After-input capture unavailable: {exc}")
    outputs: Record = {}
    for name in ("stdout", "stderr"):
        path = program.path(f"{directory}/{name}.bin")
        stream = streams.get(name)
        if stream is not None and not stream.done.is_set():
            outputs[name] = {
                "path": None,
                "sha256": None,
                "bytes_seen": None,
                "bytes_retained": None,
                "truncated": None,
                "complete": False,
            }
            continue
        if not path.exists():
            _write_new(program, f"{directory}/{name}.bin", b"")
        content = core.bounded_bytes(path, MAX_STREAM_LIMIT)
        seen = stream.seen if stream is not None else 0
        outputs[name] = {
            "path": f"{directory}/{name}.bin",
            "sha256": core.sha(content),
            "bytes_seen": seen,
            "bytes_retained": len(content),
            "truncated": seen > len(content),
            "complete": stream is None or stream.error is None,
        }
        if seen > len(content):
            reasons.append(f"{name} output exceeded its retained-byte limit")
    artifacts = _capture_artifacts(prepared)
    if artifacts["missing"]:
        reasons.append("One or more selected artifacts could not be captured")
    if cleanup_status == "unresolved":
        reasons.append("Descendant cleanup or output finalization remains unresolved")
    if comparison["status"] != "unchanged" and not reasons:
        reasons.append("Input comparison is not complete and unchanged")
    completeness = (
        "complete"
        if not reasons
        and before_ref is not None
        and after_ref is not None
        and comparison["status"] == "unchanged"
        and outcome == "exited"
        else "incomplete"
    )
    capture: Record = {
        "schema_version": 1,
        "producer": "feature_program_execution",
        "run_id": prepared.binding["run_id"],
        "task_id": prepared.binding["task_id"],
        "attempt": prepared.binding["attempt"],
        "actor_id": prepared.binding["actor_id"],
        "request_id": prepared.binding["request_id"],
        "request_version": prepared.binding["request_version"],
        "origin_expected_revision": prepared.binding["expected_revision"],
        "request_receipt": prepared.request["receipt"],
        "request_context_digest": core.digest(prepared.request["context"]),
        "start_ref": prepared.start_ref,
        "invocation": prepared.invocation,
        "method": method,
        "environment": prepared.environment,
        "input_capture_before": before_ref,
        "input_capture_after": after_ref,
        "process": {
            "outcome": outcome,
            "exit_code": exit_code,
            "started_at": started_at,
            "ended_at": ended_at,
            "clock_id": "runner-utc",
            "elapsed_seconds": elapsed,
            "cleanup_status": cleanup_status,
        },
        "comparison": comparison,
        "outputs": outputs,
        "artifacts": artifacts,
        "completeness": {"status": completeness, "reasons": list(dict.fromkeys(reasons))},
        "publication": {"status": "pending"},
        "next_action": prepared.binding["next_action"],
    }
    capture_ref = _write_json(program, directory + "/capture.json", capture)
    return {
        "schema_version": 1,
        "status": "captured",
        "capture_ref": capture_ref,
        "capture": capture,
        "next_action": prepared.binding["next_action"],
    }


def summarize_capture(capture: Record) -> Record:
    """Return safe process/capture facts; process success never grants task acceptance."""
    process = core.record(capture.get("process"), "captured process")
    completeness = core.record(capture.get("completeness"), "capture completeness")
    comparison = core.record(capture.get("comparison"), "input comparison")
    outcome = process.get("outcome")
    passing = (
        outcome == "exited"
        and process.get("exit_code") == 0
        and completeness.get("status") == "complete"
        and comparison.get("status") == "unchanged"
    )
    result = "passed" if passing else "unknown"
    if outcome in {"not_started", "spawn_failed", "timed_out", "canceled", "interrupted"} or (
        outcome == "exited" and process.get("exit_code") != 0
    ):
        result = "failed"
    artifacts = core.record(capture.get("artifacts"), "captured artifacts")
    outputs = core.record(capture.get("outputs"), "captured outputs")
    counts: Record = {}
    for name in ("stdout", "stderr"):
        stream = core.record(outputs[name], "captured stream")
        counts[name] = {
            key: stream.get(key) for key in ("bytes_seen", "bytes_retained", "truncated")
        }
    safe_reasons: list[str] = []
    reason_codes: list[str] = []
    classifications = (
        (outcome == "outcome_unknown", "launch_unknown", "Execution outcome is unknown"),
        (outcome == "timed_out", "timed_out", "Process exceeded its timeout"),
        (outcome == "canceled", "canceled", "Execution was canceled"),
        (outcome == "spawn_failed", "spawn_failed", "Process could not start"),
        (outcome == "interrupted", "interrupted", "Execution was interrupted"),
        (comparison.get("status") == "changed", "input_changed", "Checked inputs changed"),
        (comparison.get("status") == "not_assessed", "input_unknown", "Input closure is unknown"),
        (bool(artifacts["missing"]), "artifact_missing", "Required artifacts are unavailable"),
        (
            any(
                core.record(stream, "captured stream").get("truncated") is True
                for stream in outputs.values()
            ),
            "output_truncated",
            "Output capture was truncated",
        ),
        (
            process.get("cleanup_status") == "unresolved",
            "cleanup_unresolved",
            "Process cleanup remains unresolved",
        ),
    )
    for applies, code, label in classifications:
        if applies:
            reason_codes.append(code)
            safe_reasons.append(label)
    if completeness.get("status") != "complete" and not safe_reasons:
        reason_codes.append("capture_incomplete")
        safe_reasons.append("Execution evidence is incomplete")
    return {
        "schema_version": 1,
        "run_id": capture.get("run_id"),
        "phase": "interrupted" if outcome == "outcome_unknown" else "finished",
        "process_result": outcome,
        "reported_check_result": result,
        "capture_status": completeness.get("status"),
        "comparison": comparison.get("status"),
        "artifacts": {
            "expected": len(core.sequence(artifacts["requested"], "requested artifacts")),
            "captured": len(core.sequence(artifacts["captured"], "captured artifacts")),
            "missing": len(core.sequence(artifacts["missing"], "missing artifacts")),
        },
        "output_counts": counts,
        "publication": capture.get("publication"),
        "status": "captured",
        "started_at": process.get("started_at"),
        "ended_at": process.get("ended_at"),
        "reused": False,
        "next_action": "Inspect captured execution facts before recording verification",
        "reasons": safe_reasons,
        "reason_codes": reason_codes,
    }


@dataclass(frozen=True, slots=True)
class _LoadedCapture:
    """One checked sealed image and inputs from one artifact observation epoch."""

    capture: Record
    start: Record
    before: Record | None
    after: Record | None


def _capture_json(program: ProgramAccess, ref: Record, budget: core.ReadBudget) -> Record:
    name = core.string(ref["path"], "capture path")
    captured = budget.capture(program.directory, name, MAX_CAPTURE_BYTES)
    core.require(captured.sha256 == ref["sha256"], f"Frozen evidence changed: {name}")
    return budget.json(captured)


def _read_capture(
    program: ProgramAccess, capture_ref: Record, budget: core.ReadBudget | None = None
) -> Record:
    """Validate an independently selected sealed original; no metadata authority is inferred."""
    return _load_capture(program, capture_ref, budget).capture


def _load_capture(
    program: ProgramAccess, capture_ref: Record, budget: core.ReadBudget | None = None
) -> _LoadedCapture:
    """Resolve one sealed producer and its existing immutable transitive references."""
    _require(set(capture_ref) == {"path", "sha256"}, "Capture reference requires exact path/hash")
    verification._ref(capture_ref)
    capture_budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    capture = _capture_json(program, capture_ref, capture_budget)
    _require(
        capture.get("schema_version") == 1
        and capture.get("producer") == "feature_program_execution",
        "Unsupported actual run capture",
    )
    prefix = f"evidence/{capture['task_id']}/attempt-{capture['attempt']}/"
    _require(
        str(capture_ref["path"]).startswith(prefix), "Selected capture must retain origin scope"
    )
    before_ref = capture.get("input_capture_before")
    after_ref = capture.get("input_capture_after")
    refs: list[Json] = [core.record(capture["start_ref"], "start ref")]
    for actual_ref in (before_ref, after_ref):
        if actual_ref is not None:
            refs.append(core.record(actual_ref, "actual input capture ref"))
    for stream in core.record(capture["outputs"], "capture outputs").values():
        output = core.record(stream, "captured stream")
        if output.get("path") is not None:
            refs.append({"path": output["path"], "sha256": output["sha256"]})
    for item in core.sequence(capture["artifacts"]["captured"], "captured artifacts"):
        artifact = core.record(item, "captured artifact")
        refs.append({"path": artifact["path"], "sha256": artifact["sha256"]})
    for ref in refs:
        verification._ref(ref)
        _require(str(ref["path"]).startswith(prefix), "Captured output escaped original task scope")
    # Standalone admission historically budgets the sealed image separately from its refs.
    budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    program.verify_refs(refs, budget)
    start = _capture_json(program, capture["start_ref"], budget)
    for key in (
        "run_id",
        "task_id",
        "attempt",
        "request_id",
        "request_version",
        "actor_id",
        "request_receipt",
        "request_context_digest",
        "origin_expected_revision",
        "method",
    ):
        _require(capture.get(key) == start.get(key), f"Sealed execution {key} differs from start")
    captured_inputs: list[Record | None] = []
    for ref in (before_ref, after_ref):
        actual = None
        if ref is not None:
            actual = inputs.validate_capture(_capture_json(program, ref, budget))
            _require(actual["method"] == capture["method"], "Sealed input method changed")
        captured_inputs.append(actual)
    return _LoadedCapture(capture, start, captured_inputs[0], captured_inputs[1])


def prepare_observation(
    program: ProgramAccess,
    state: Record,
    capture_ref: Record,
    request_binding: Record,
    *,
    budget: core.ReadBudget | None = None,
) -> Record:
    """Prepare an inert existing verification event from exact sealed execution evidence."""
    program.require_current_location()
    loaded = _load_capture(program, capture_ref, budget)
    binding = deepcopy(request_binding)
    request = _request(state, binding)
    return _prepare_observation_loaded(
        program,
        state,
        capture_ref,
        binding,
        loaded,
        request,
        core.phases.revision(state),
        core.digest(request["context"]),
    )


def _prepare_observation_loaded(
    program: ProgramAccess,
    state: Record,
    capture_ref: Record,
    binding: Record,
    loaded: _LoadedCapture,
    request: Record,
    revision: str,
    request_digest: str,
) -> Record:
    """Prepare from same-epoch private facts while retaining current authority checks."""
    program.require_current_location()
    capture = loaded.capture
    for key in ("task_id", "request_id", "request_version", "actor_id"):
        _require(capture.get(key) == binding.get(key), f"Captured {key} binding changed")
    _require(
        capture["request_receipt"] == request["receipt"]
        and capture["request_context_digest"] == request_digest,
        "Original request binding changed",
    )
    task = program.task(state, str(binding["task_id"]))
    _require(
        program.current_attempt(task)["number"] == capture["attempt"],
        "Captured task attempt changed",
    )
    verification._authority(state, task, {**binding, "kind": "finished"}, request)
    before_ref = capture.get("input_capture_before")
    after_ref = capture.get("input_capture_after")
    summary = summarize_capture(capture)
    process = core.record(capture["process"], "captured process")
    identifier = binding.get("observation_id", str(capture["run_id"]) + "-finished")
    _require(core.portable_task_id(str(identifier)), "Observation ID must be portable")
    event: Record = {
        "schema_version": 1,
        "operation": "verification-record",
        "id": identifier,
        "kind": "finished",
        "task_id": capture["task_id"],
        "actor_id": capture["actor_id"],
        "expected_revision": revision,
        "reason": core.string(binding.get("reason"), "observation reason"),
        "request_id": capture["request_id"],
        "request_version": capture["request_version"],
        "result": summary["reported_check_result"],
        "method": capture["method"],
        "environment": core.digest(capture["environment"]),
        "run_capture_ref": deepcopy(capture_ref),
        "artifacts": [capture_ref["path"]],
    }
    if before_ref is not None and after_ref is not None:
        before, after = loaded.before, loaded.after
        _require(before is not None and after is not None, "Sealed input captures unavailable")
        event.update(
            {
                "input_capture_before": before_ref,
                "input_capture_after": after_ref,
                "input_before": before["input_digest"],
                "input_after": after["input_digest"],
            }
        )
    if "reporter_id" in binding:
        event["reporter_id"] = binding["reporter_id"]
    if process["ended_at"] is not None:
        event.update({"observed_at": process["ended_at"], "clock_id": process["clock_id"]})
    if process["exit_code"] is not None:
        event["exit_code"] = process["exit_code"]
    return event


def project_capture(
    program: ProgramAccess,
    state: Record,
    capture_ref: Record,
    *,
    budget: core.ReadBudget | None = None,
) -> Record:
    """Project one explicitly selected sealed capture; never scan, execute or publish."""
    program.require_current_location()
    reads = verification._reads(budget)
    loaded = _load_capture(program, capture_ref, reads.artifacts)
    capture = loaded.capture
    reads.index(state)
    request_id = core.string(capture.get("request_id"), "captured request_id")
    request = reads.record_index.get(request_id)
    _require(
        request is not None and request["input"]["kind"] == "request",
        "Selected execution has no exact adopted origin request",
    )
    request_digest = core.digest(request["context"])
    _require(
        request["input"]["task_id"] == capture["task_id"]
        and request["request_version"] == capture["request_version"]
        and request["context"]["attempt"] == capture["attempt"]
        and request["receipt"] == capture["request_receipt"]
        and request_digest == capture["request_context_digest"],
        "Selected execution origin task/request binding is invalid",
    )
    program.verify_refs(
        core.sequence(request["evidence"], "origin request evidence"), reads.artifacts
    )
    current, _details = verification._applicable(program, state, request, reads)
    reasons: list[str] = []
    if not current:
        reasons.append("Captured verification inputs or task context are no longer current")
    latest = reads.latest_event.get(request_id)
    newer_fact = False
    if (
        latest is not None
        and latest["input"].get("run_capture_ref") == capture_ref
        and latest["input"]["kind"] == "finished"
        and latest["input"].get("result") != "passed"
    ):
        newer_fact = True
    if (
        latest is not None
        and latest["input"]["kind"] != "request"
        and latest["input"].get("run_capture_ref") != capture_ref
    ):
        start = loaded.start
        reserved_at = verification._time(start.get("prepared_at"), "capture reservation")
        recorded_at = verification._time(latest.get("recorded_at"), "verification publication")
        if reserved_at is None or recorded_at is None or recorded_at >= reserved_at:
            newer_fact = True
    if newer_fact:
        current = False
        reasons.append("A newer recorded verification event supersedes this execution")
    binding: Record = {
        "task_id": capture["task_id"],
        "attempt": capture["attempt"],
        "request_id": request_id,
        "request_version": capture["request_version"],
        "lane_id": request["input"]["lane"],
    }
    draft_status = "unavailable"
    draft_reasons = ["Observation preparation is unavailable for the current request"]
    try:
        _require(not newer_fact, "A newer recorded fact prevents old observation preparation")
        _require(
            reads.latest_lane[(str(capture["task_id"]), str(request["input"]["lane"]))][
                "request_id"
            ]
            == request_id,
            "Execution request was superseded",
        )
        revision = core.phases.revision(state)
        _prepare_observation_loaded(
            program,
            state,
            capture_ref,
            {
                "task_id": capture["task_id"],
                "actor_id": capture["actor_id"],
                "request_id": request_id,
                "request_version": capture["request_version"],
                "expected_revision": revision,
                "reason": "Prepare the explicitly selected actual execution observation",
            },
            loaded,
            request,
            revision,
            request_digest,
        )
        draft_status = "prepared"
        draft_reasons = []
    except (OSError, core.ProgramError):
        current = False
        reasons.append("Current observation authority or request binding is unavailable")
    reads.recheck(program)
    summary = summarize_capture(capture)
    return {
        "schema_version": 1,
        "binding": binding,
        "task_id": binding["task_id"],
        "attempt": binding["attempt"],
        "lane": binding["lane_id"],
        "request_id": request_id,
        "request_version": binding["request_version"],
        "current": current,
        "currentness": "current" if current else "stale",
        "summary": summary,
        "observation_draft": {"status": draft_status, "reasons": draft_reasons},
        "reference": deepcopy(capture_ref),
        "reasons": list(dict.fromkeys(reasons)),
    }
