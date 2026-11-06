"""Run the real FastMCP surface independently of the legacy module stubs."""

from tests.timing import watchdog_timeout

import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

_OUTPUT_TAIL_LIMIT = 4096
_HTTP_WORKERS = 4


@contextmanager
def _http_coverage(pytestconfig, directory: Path):
    """Join only this child's data into its owning parent coverage worker."""
    if not pytestconfig.pluginmanager.hasplugin("_cov"):
        yield [], {}
        return

    # Normal focused tests must also work without coverage or pytest-cov installed.
    from coverage import Coverage, CoverageData

    parent = Coverage.current()
    if parent is None or pytestconfig.getoption("no_cov", default=False):
        yield [], {}
        return

    directory.mkdir()
    data_file = directory / ".coverage"
    sources = parent.get_option("run:source")
    args = [f"--cov={source}" for source in sources] if sources else ["--cov="]
    args.extend(
        [
            "--cov-config",
            pytestconfig.getoption("cov_config", default=".coveragerc"),
            "--cov-report=",
            "--cov-fail-under=0",
        ]
    )
    if parent.get_option("run:branch"):
        args.append("--cov-branch")
    if context := pytestconfig.getoption("cov_context", default=None):
        args.append(f"--cov-context={context}")
    try:
        yield args, {"COVERAGE_FILE": str(data_file)}
    finally:
        # The nested controller combines its own xdist files. Update this worker's
        # in-memory data rather than combining/erasing the outer workers' files.
        # A killed or failed child may not have produced data; retain its diagnostics.
        if data_file.is_file():
            child = CoverageData(basename=str(data_file))
            child.read()
            parent.get_data().update(child)


def _windows_job():
    """Create an initially empty job; the gated bootstrap cannot spawn before assignment."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, job


def _run_http_subprocess(args, *, timeout, **kwargs):
    """Own the controller tree until its pipes have drained or cleanup finishes."""
    capture_output = kwargs.pop("capture_output", False)
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    kernel = job = process = None
    try:
        if os.name == "nt":
            import ctypes

            kernel, job = _windows_job()
            # The bootstrap waits for assignment before spawning the actual controller.
            bootstrap = (
                "import subprocess,sys; sys.stdin.buffer.read(1); "
                "p=subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL); sys.exit(p.wait())"
            )
            process = subprocess.Popen(
                [sys._base_executable, "-c", bootstrap, *args], stdin=subprocess.PIPE, **kwargs
            )
            if not kernel.AssignProcessToJobObject(job, int(getattr(process, "_handle"))):
                raise ctypes.WinError(ctypes.get_last_error())
            release = "x" if kwargs.get("text") else b"x"
        else:
            process = subprocess.Popen(args, start_new_session=True, **kwargs)
            release = None
        stdout, stderr = process.communicate(input=release, timeout=timeout)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except BaseException as error:
        try:
            if job is not None:
                if not kernel.TerminateJobObject(job, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
            elif process is not None:
                try:
                    getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
                except ProcessLookupError:
                    pass
        except OSError as cleanup_error:
            error.add_note(f"Owned HTTP process tree termination failed: {cleanup_error}")
        try:
            if process is not None:
                # Assignment can fail before the bootstrap has joined the job.
                if process.poll() is None:
                    process.kill()
                stdout, stderr = process.communicate(timeout=10)
                if isinstance(error, subprocess.TimeoutExpired):
                    if stdout is not None:
                        error.output = stdout
                    if stderr is not None:
                        error.stderr = stderr
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            error.add_note(f"Owned HTTP process cleanup failed: {cleanup_error}")
        raise
    finally:
        if job is not None:
            kernel.CloseHandle(job)


def _output_tail(output: bytes | str | None) -> str:
    if output is None:
        return "<none>"
    if isinstance(output, bytes):
        return output[-_OUTPUT_TAIL_LIMIT:].decode("utf-8", errors="replace")
    return output[-_OUTPUT_TAIL_LIMIT:]


def _worker_progress(directory: Path) -> str:
    rows = []
    for worker in range(_HTTP_WORKERS):
        name = f"gw{worker}"
        try:
            with (directory / f"{name}.json").open(encoding="utf-8") as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("Oversized worker phase record")
            state = json.loads(raw)
            phase, nodeid = state["phase"], state["nodeid"]
            if not isinstance(phase, str) or not isinstance(nodeid, str):
                raise ValueError("Invalid worker phase record")
            suffix = "..." if len(nodeid) > 1024 else ""
            rows.append(f"{name}: {phase[:80]}: {nodeid[:1024]}{suffix}")
        except FileNotFoundError:
            rows.append(f"{name}: no phase recorded")
        except (OSError, ValueError, KeyError, TypeError) as error:
            rows.append(f"{name}: phase unavailable ({type(error).__name__})")
    return "\n".join(rows)


def _failure_message(
    reason: str, stdout: bytes | str | None, stderr: bytes | str | None, progress_dir: Path
) -> str:
    return (
        f"{reason}\nworker phases:\n{_worker_progress(progress_dir)}"
        f"\nstdout tail:\n{_output_tail(stdout)}\nstderr tail:\n{_output_tail(stderr)}"
    )


def test_local_http_security_regressions(tmp_path, pytestconfig):
    server_root = Path(__file__).resolve().parents[1]
    progress_dir = tmp_path / "http-progress"
    env = {
        **os.environ,
        "UNITY_MCP_RUN_HTTP_TESTS": "1",
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
        "UNITY_MCP_HTTP_TEST_PROGRESS_DIR": str(progress_dir),
        "PYTHONPATH": os.pathsep.join(
            filter(None, (str(server_root.parent / "tools"), os.environ.get("PYTHONPATH")))
        ),
    }
    report_args = []
    if xmlpath := pytestconfig.getoption("xmlpath", default=None):
        report = Path(xmlpath).resolve()
        report_args = ["--junitxml", str(report.with_stem(f"{report.stem}-http"))]
    try:
        with _http_coverage(pytestconfig, tmp_path / "http-coverage") as (
            coverage_args,
            coverage_env,
        ):
            result = _run_http_subprocess(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/http",
                    "-n",
                    str(_HTTP_WORKERS),
                    "--dist=loadfile",
                    "--max-worker-restart=0",
                    "-p",
                    "pytest_ci_schedule",
                    "--no-loadscope-reorder",
                    "--ci-first-file=http/test_client_assurance_wire.py",
                    "-vv",
                    "--tb=short",
                    "-W",
                    "error",
                    "--durations=20",
                    "--basetemp",
                    str(tmp_path / "http-tmp"),
                    "-o",
                    f"cache_dir={tmp_path / 'http-cache'}",
                    *report_args,
                    *coverage_args,
                ],
                cwd=server_root,
                env={**env, **coverage_env},
                capture_output=True,
                text=True,
                # Start the expensive real-client file before short, highly parametrized files.
                # Keep whole files and their fixture lifetimes on the same four workers.
                # The suite watchdog is distinct from the request deadlines asserted inside.
                # Operators can increase the shared multiplier for slow/oversubscribed hosts.
                timeout=watchdog_timeout(900),
            )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            _failure_message(
                f"HTTP test subprocess timed out after {error.timeout:g}s",
                error.output,
                error.stderr,
                progress_dir,
            )
        ) from error
    if result.returncode != 0:
        raise AssertionError(
            _failure_message(
                f"HTTP test subprocess exited with status {result.returncode}",
                result.stdout,
                result.stderr,
                progress_dir,
            )
        )
