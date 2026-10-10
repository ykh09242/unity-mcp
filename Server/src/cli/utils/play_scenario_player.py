"""Launch one explicitly selected standalone Player and verify its actual final report."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
from threading import Thread
import time
from typing import Annotated, Final, Literal
from uuid import uuid4

from models.play_scenarios import (
    JobId,
    PlayScenario,
    PlayScenarioCommand,
    RepeatCount,
    RunTimeout,
    ScenarioName,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)
from cli.utils.play_scenario_player_payload import (
    PAYLOAD_MANIFEST_LIMIT,
    PayloadFile,
    PlayerPayload,
    PlayerPayloadError,
    check_deadline,
    verify_player_payload,
)
from cli.utils.play_scenario_player_progress import ProgressIdentity, ProgressObserver
from cli.utils.play_scenario_iterations import validate_iteration_results

MANIFEST_LIMIT: Final = PAYLOAD_MANIFEST_LIMIT
REPORT_LIMIT: Final = 2097152
PROCESS_LOG_LIMIT: Final = 1048576


class PlayerLaunchError(RuntimeError):
    """A bundle, process or final report could not establish a truthful Player outcome."""

    def __init__(self, message: str, directory: Path | None = None) -> None:
        super().__init__(message)
        self.directory = directory


def _native_definition(value: JsonValue) -> JsonValue:
    """Omit native serialized null step options without weakening authored schema validation."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    optional = {
        "scene",
        "target",
        "target_id",
        "reset_ids",
        "click_mode",
        "count",
        "active",
        "component",
        "property",
        "stable_for_ms",
        "state_id",
        "state_equals",
    }
    for field in ("steps", "setup_steps", "cleanup_steps"):
        entries = normalized.get(field)
        if isinstance(entries, list):
            normalized[field] = [
                {
                    key: item
                    for key, item in entry.items()
                    if item is not None or key not in optional
                }
                if isinstance(entry, dict)
                else entry
                for entry in entries
            ]
    return normalized


class PlayerBundle(BaseModel):
    """Versioned local build manifest, including the exact canonical hash input."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=2)]
    scenario_name: ScenarioName
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    executable: Literal["MCPScenarioPlayer.exe"]
    scene_paths: Annotated[
        list[Annotated[str, Field(strict=True, max_length=4096)]],
        Field(min_length=1, max_length=64),
    ]
    definition: PlayScenario
    definition_json: Annotated[str, Field(strict=True, max_length=65536)]
    unity_version: Annotated[str, Field(strict=True, max_length=128)]
    package_version: Annotated[str, Field(strict=True, max_length=128)]

    build_id: JobId | None = None
    build_source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    payload_inventory: Annotated[list[PayloadFile], Field(min_length=1, max_length=1024)] | None = (
        None
    )
    payload_inventory_json: (
        Annotated[str, Field(strict=True, max_length=PAYLOAD_MANIFEST_LIMIT)] | None
    ) = None
    payload_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = None

    @property
    def payload(self) -> PlayerPayload | None:
        if self.schema_version == 1:
            return None
        return PlayerPayload.model_validate(
            {
                name: getattr(self, name)
                for name in (
                    "build_id",
                    "build_source_revision",
                    "payload_inventory",
                    "payload_inventory_json",
                    "payload_hash",
                )
            }
        )

    @field_validator("definition", mode="before")
    @classmethod
    def parse_native_definition(cls, value: JsonValue) -> JsonValue:
        return _native_definition(value)

    @model_validator(mode="before")
    @classmethod
    def check_manifest_definition(cls, value: JsonValue) -> JsonValue:
        """Require the convenience object to equal the exact hashed canonical JSON."""
        if isinstance(value, dict) and isinstance(value.get("definition_json"), str):
            if json.loads(value["definition_json"]) != value.get("definition"):
                raise PlayerLaunchError("Player bundle canonical definition mismatch")
        return value

    @model_validator(mode="after")
    def check_integrity(self) -> "PlayerBundle":
        """Verify canonical bytes and supported capabilities before a process is started."""
        v2_fields = {
            "build_id",
            "build_source_revision",
            "payload_inventory",
            "payload_inventory_json",
            "payload_hash",
        }
        if self.schema_version == 1 and self.model_fields_set & v2_fields:
            raise PlayerLaunchError("Legacy Player manifest cannot claim verified payload metadata")
        if self.schema_version == 2 and not v2_fields <= self.model_fields_set:
            raise PlayerLaunchError("Player v2 manifest requires all payload identity fields")
        if self.schema_version == 2:
            self.payload
        if sha256(self.definition_json.encode("utf-8")).hexdigest() != self.definition_hash:
            raise PlayerLaunchError("Player bundle definition hash mismatch")
        canonical = PlayScenario.model_validate(
            _native_definition(json.loads(self.definition_json))
        )
        if canonical != self.definition or self.scenario_name != self.definition.name:
            raise PlayerLaunchError("Player bundle definition identity mismatch")
        if len(set(self.scene_paths)) != len(self.scene_paths):
            raise PlayerLaunchError("Player bundle contains duplicate scenes")
        steps = self.definition.setup_steps + self.definition.steps + self.definition.cleanup_steps
        required = {step.scene for step in steps if step.scene is not None}
        if set(self.scene_paths) != required:
            raise PlayerLaunchError("Player bundle scenes do not match the frozen definition")
        if (
            self.definition.resources.enabled
            or self.definition.metrics.enabled
            or self.definition.diagnostics.screenshot_on_failure
            or any(step.property is not None for step in steps)
        ):
            raise PlayerLaunchError("Player bundle requires unsupported runtime capabilities")
        return self


class PlayerFinalReport(BaseModel):
    """Required native identity, terminal state and process exit proof."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    job_id: JobId
    scenario: PlayScenario
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]
    execution_environment: Literal["player"]
    finalization_state: Literal["completed"]
    exit_code: Annotated[int, Field(strict=True, ge=0, le=2)]
    repeat_count: RepeatCount
    timeout_seconds: RunTimeout
    reproduction: dict[str, JsonValue]
    runner_resources_released: Annotated[bool, Field(strict=True)]
    player_schema_version: Annotated[int, Field(strict=True, ge=1, le=2)] = 1
    progress_error: Annotated[str, Field(strict=True, max_length=2048)] | None = None
    report_error: str | None = None
    started_unix_ms: Annotated[int, Field(strict=True, ge=0)]
    finished_unix_ms: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="before")
    @classmethod
    def require_iteration_contract(cls, value: JsonValue) -> JsonValue:
        """Admit legacy reports or a complete terminal native repetition ledger."""
        if isinstance(value, dict) and type(value.get("repeat_count")) is int:
            validate_iteration_results(value, value["repeat_count"], terminal=True)
        return value

    @model_validator(mode="after")
    def require_release_proof(self) -> "PlayerFinalReport":
        if not self.runner_resources_released:
            raise PlayerLaunchError("Player final report lacks native resource release proof")
        return self

    @field_validator("scenario", mode="before")
    @classmethod
    def parse_native_definition(cls, value: JsonValue) -> JsonValue:
        return _native_definition(value)


@dataclass(frozen=True, slots=True)
class PlayerRunOptions:
    """Caller-selected launch and bounded cancellation policy."""

    build_directory: Path
    output_directory: Path
    repeat_count: int = 1
    timeout_seconds: int = 300
    source_revision: str | None = None
    cleanup_wait_seconds: float = 360
    poll_interval_seconds: float = 0.1
    deadline_monotonic: float | None = None
    expected_build_revision: str | None = None
    expected_build_id: str | None = None
    require_verified_payload: bool = False
    player_job_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.cleanup_wait_seconds)
            or not 0.05 <= self.cleanup_wait_seconds <= 600
        ):
            raise PlayerLaunchError(
                "Player cleanup wait must be bounded between 0.05 and 600 seconds"
            )
        if (
            not math.isfinite(self.poll_interval_seconds)
            or not 0.05 <= self.poll_interval_seconds <= 10
        ):
            raise PlayerLaunchError(
                "Player polling interval must be bounded between 0.05 and 10 seconds"
            )


@dataclass(frozen=True, slots=True)
class PlayerOutcome:
    """Actual native report and owned artifact directory with truthful CLI exit code."""

    report: dict[str, JsonValue]
    directory: Path
    exit_code: int
    client_error: str | None = None
    diagnostics: dict[str, JsonValue] = field(default_factory=dict)


def _read_json(path: Path, limit: int) -> dict[str, JsonValue]:
    """Read a regular bounded UTF-8 JSON document without following file links."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise PlayerLaunchError("Player artifact is missing, linked or exceeds its byte bound")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise PlayerLaunchError("Player artifact exceeds its byte bound")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise PlayerLaunchError("Player artifact must be a JSON object")
    return value


class _LogCapture:
    """Owned thread accumulator records bounded log bytes and propagates stream failures."""

    def __init__(self, process: subprocess.Popen, path: Path) -> None:
        self.process = process
        self.path = path
        self.error: OSError | None = None

    def drain(self) -> None:
        """Drain the child pipe even after the retained prefix reaches its byte limit."""
        written = 0
        if self.process.stdout is None:
            return
        try:
            with self.process.stdout, self.path.open("xb") as stream:
                while chunk := self.process.stdout.read(8192):
                    retained = chunk[: max(0, PROCESS_LOG_LIMIT - written)]
                    stream.write(retained)
                    written += len(retained)
        except OSError as exc:
            self.error = exc


def _stop_owned(process: subprocess.Popen) -> None:
    """Terminate only this owned child and require a confirmed process exit."""
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise PlayerLaunchError("Owned Player did not exit after termination") from exc


def _write_diagnostics(directory: Path, diagnostics: dict[str, JsonValue]) -> None:
    """Write launcher evidence separately without changing or inventing native run.json."""
    encoded = json.dumps(diagnostics, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65536:
        raise PlayerLaunchError("Player launcher diagnostics exceed the byte bound", directory)
    (directory / "diagnostics.json").write_bytes(encoded + b"\n")


def run_player(options: PlayerRunOptions) -> PlayerOutcome:
    """Run foreground, request cancellation once, and require fresh native finalization evidence."""
    check_deadline(options.deadline_monotonic)
    root = options.build_directory.resolve(strict=True)
    manifest = root / "scenario-bundle.json"
    if manifest.resolve(strict=True).parent != root:
        raise PlayerLaunchError("Player manifest leaves the selected build directory")
    bundle = PlayerBundle.model_validate(_read_json(manifest, MANIFEST_LIMIT))
    payload = bundle.payload
    if options.require_verified_payload and payload is None:
        raise PlayerLaunchError("This command requires a verified v2 Player payload")
    if options.expected_build_revision is not None:
        PlayScenarioCommand.check_revision(options.expected_build_revision)
        if payload is None or payload.build_source_revision != options.expected_build_revision:
            raise PlayerLaunchError(
                "Player embedded build revision does not match the expected label"
            )
    if options.expected_build_id is not None and (
        payload is None or payload.build_id != options.expected_build_id
    ):
        raise PlayerLaunchError("Player embedded build ID does not match the expected ID")
    if payload is not None:
        output_root = options.output_directory.resolve()
        if output_root.is_relative_to(root):
            raise PlayerLaunchError(
                "Verified Player request/output directories must be outside the build directory"
            )
        verify_player_payload(root, payload, options.deadline_monotonic)
    executable = root / bundle.executable
    if (
        executable.is_symlink()
        or executable.resolve(strict=True).parent != root
        or not executable.is_file()
    ):
        raise PlayerLaunchError(
            "Player executable must be a regular file inside the selected build directory"
        )
    command = PlayScenarioCommand(
        action="run",
        name=bundle.scenario_name,
        job_id=options.player_job_id or uuid4().hex,
        repeat_count=options.repeat_count,
        timeout_seconds=options.timeout_seconds,
        source_revision=options.source_revision,
    )
    options.output_directory.mkdir(parents=True, exist_ok=True)
    directory = options.output_directory.resolve() / str(command.job_id)
    directory.mkdir(exist_ok=False)
    request_path = directory / "request.json"
    request: dict[str, JsonValue] = {
        "schema_version": bundle.schema_version,
        "job_id": command.job_id,
        "scenario_name": bundle.scenario_name,
        "definition_hash": bundle.definition_hash,
        "repeat_count": command.repeat_count,
        "timeout_seconds": command.timeout_seconds,
    }
    if payload is not None:
        request.update(
            {
                "build_id": payload.build_id,
                "build_source_revision": payload.build_source_revision,
                "payload_hash": payload.payload_hash,
            }
        )
    if command.source_revision is not None:
        request["source_revision"] = command.source_revision
    request_path.write_text(json.dumps(request, ensure_ascii=True) + "\n", encoding="utf-8")
    (directory / "definition.json").write_text(bundle.definition_json + "\n", encoding="utf-8")
    check_deadline(options.deadline_monotonic)
    process_started_unix_ms = int(time.time() * 1000)
    process = subprocess.Popen(
        [
            str(executable),
            "--mcp-scenario-request",
            str(request_path),
            "-batchmode",
            "-logFile",
            "-",
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
    )
    log_capture = _LogCapture(process, directory / "player.log")
    capture = Thread(target=log_capture.drain, daemon=True)
    observer = (
        ProgressObserver(
            ProgressIdentity(
                str(command.job_id),
                bundle.definition_hash,
                bundle.build_id,
                bundle.payload_hash,
                bundle.build_source_revision,
                process.pid,
            )
        )
        if payload is not None
        else None
    )
    deadline = time.monotonic() + options.timeout_seconds
    if options.deadline_monotonic is not None:
        deadline = min(deadline, options.deadline_monotonic)
    interrupted = False
    deadline_reached = False
    cancellation_sent = False
    forced_termination = False
    try:
        capture.start()
        while process.poll() is None:
            try:
                if observer is not None:
                    observer.observe(directory / "progress.json")
                expired = time.monotonic() >= deadline or (
                    log_capture.error is not None and not cancellation_sent
                )
                if expired and not cancellation_sent:
                    (directory / "cancel").write_text("cancel\n", encoding="utf-8")
                    cancellation_sent = True
                    deadline_reached = not interrupted
                    deadline = time.monotonic() + options.cleanup_wait_seconds
                elif expired:
                    forced_termination = process.poll() is None
                    _stop_owned(process)
                    break
                time.sleep(max(0.05, options.poll_interval_seconds))
            except KeyboardInterrupt:
                interrupted = True
                if cancellation_sent:
                    forced_termination = process.poll() is None
                    _stop_owned(process)
                    break
                (directory / "cancel").write_text("cancel\n", encoding="utf-8")
                cancellation_sent = True
                deadline = time.monotonic() + options.cleanup_wait_seconds
        return_code = process.wait(timeout=5)
    finally:
        forced_termination |= process.poll() is None
        _stop_owned(process)
        if observer is not None:
            observer.observe(directory / "progress.json")
        process_evidence = {
            "schema_version": 1,
            "job_id": command.job_id,
            "process_id": process.pid,
            "actual_exit_code": process.returncode,
            "process_ended": process.poll() is not None,
            "forced_termination": forced_termination,
            "started_unix_ms": process_started_unix_ms,
            "finished_unix_ms": int(time.time() * 1000),
            "request_sha256": sha256(request_path.read_bytes()).hexdigest(),
            "progress_observations": list(observer.observations) if observer is not None else [],
        }
        encoded_evidence = json.dumps(
            process_evidence, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded_evidence) > 65536:
            raise PlayerLaunchError("Owned process evidence exceeds its byte bound", directory)
        (directory / "process.json").write_bytes(encoded_evidence + b"\n")
        if capture.ident is not None:
            capture.join(timeout=5)
    if capture.is_alive():
        raise PlayerLaunchError("Owned Player output stream did not close")
    if log_capture.error is not None:
        raise PlayerLaunchError("Owned Player log could not be retained") from log_capture.error
    diagnostics: dict[str, JsonValue] = {
        "schema_version": 1,
        "job_id": command.job_id,
        "process_id": process.pid,
        "process_exit_code": return_code,
        "cancellation_sent": cancellation_sent,
        "interrupted": interrupted,
        "deadline_reached": deadline_reached,
        "cleanup_state": "process_exited",
        "payload_verification": "launcher_admission"
        if payload is not None
        else "unverified_legacy",
    }
    if observer is not None:
        observer.observe(directory / "progress.json")
        diagnostics.update(observer.diagnostics())
    try:
        raw = _read_json(directory / "run.json", REPORT_LIMIT)
        report = PlayerFinalReport.model_validate(raw)
        if (
            report.job_id != command.job_id
            or report.scenario != bundle.definition
            or report.reproduction.get("definition_hash") != bundle.definition_hash
            or report.reproduction.get("unity_version") != bundle.unity_version
            or report.reproduction.get("package_version") != bundle.package_version
            or report.repeat_count != command.repeat_count
            or report.timeout_seconds != command.timeout_seconds
            or report.reproduction.get("source_revision") != command.source_revision
            or report.finished_unix_ms < report.started_unix_ms
            or report.exit_code != return_code
            or (report.status == "succeeded") != (return_code == 0)
        ):
            raise PlayerLaunchError(
                "Player final report does not match the owned request or process exit"
            )
        if payload is not None and (
            report.player_schema_version != 2
            or report.reproduction.get("build_id") != payload.build_id
            or report.reproduction.get("build_source_revision") != payload.build_source_revision
            or report.reproduction.get("payload_hash") != payload.payload_hash
            or report.reproduction.get("payload_verification") != "launcher_admission"
        ):
            raise PlayerLaunchError("Player final report payload identity mismatch")
    except (PlayerLaunchError, OSError, ValueError, ValidationError) as exc:
        diagnostics["failure_code"] = (
            "player_report_missing"
            if not (directory / "run.json").exists()
            else "player_report_invalid"
        )
        _write_diagnostics(directory, diagnostics)
        raise PlayerLaunchError(str(diagnostics["failure_code"]), directory) from exc
    diagnostics["cleanup_state"] = "native_finalized"
    _write_diagnostics(directory, diagnostics)
    exit_code = 0 if report.status == "succeeded" and not report.report_error else 1
    client_error = report.progress_error
    if observer is not None and (
        observer.first_error or (report.status == "succeeded" and not observer.heartbeat_observed)
    ):
        client_error = client_error or observer.first_error or "player_heartbeat_missing"
    if client_error:
        exit_code = 1
    if interrupted:
        exit_code = 130
        client_error = "Player interrupted; native finalization confirmed"
    elif deadline_reached:
        exit_code = 1
        client_error = "Player launcher deadline expired"
    return PlayerOutcome(raw, directory, exit_code, client_error, diagnostics)
