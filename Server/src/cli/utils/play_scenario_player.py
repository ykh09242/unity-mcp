"""Launch one explicitly selected standalone Player and verify its actual final report."""

from dataclasses import dataclass
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
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

MANIFEST_LIMIT: Final = 262144
REPORT_LIMIT: Final = 2097152
PROCESS_LOG_LIMIT: Final = 1048576


class PlayerLaunchError(RuntimeError):
    """A bundle, process or final report could not establish a truthful Player outcome."""


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
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
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
    report_error: str | None = None
    started_unix_ms: Annotated[int, Field(strict=True, ge=0)]
    finished_unix_ms: Annotated[int, Field(strict=True, ge=0)]

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


def run_player(options: PlayerRunOptions) -> PlayerOutcome:
    """Run foreground, request cancellation once, and require fresh native finalization evidence."""
    root = options.build_directory.resolve(strict=True)
    manifest = root / "scenario-bundle.json"
    if manifest.resolve(strict=True).parent != root:
        raise PlayerLaunchError("Player manifest leaves the selected build directory")
    bundle = PlayerBundle.model_validate(_read_json(manifest, MANIFEST_LIMIT))
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
        job_id=uuid4().hex,
        repeat_count=options.repeat_count,
        timeout_seconds=options.timeout_seconds,
        source_revision=options.source_revision,
    )
    options.output_directory.mkdir(parents=True, exist_ok=True)
    directory = options.output_directory.resolve() / str(command.job_id)
    directory.mkdir(exist_ok=False)
    request_path = directory / "request.json"
    request: dict[str, JsonValue] = {
        "schema_version": 1,
        "job_id": command.job_id,
        "scenario_name": bundle.scenario_name,
        "definition_hash": bundle.definition_hash,
        "repeat_count": command.repeat_count,
        "timeout_seconds": command.timeout_seconds,
    }
    if command.source_revision is not None:
        request["source_revision"] = command.source_revision
    request_path.write_text(json.dumps(request, ensure_ascii=True) + "\n", encoding="utf-8")
    (directory / "definition.json").write_text(bundle.definition_json + "\n", encoding="utf-8")
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
    deadline = time.monotonic() + options.timeout_seconds
    interrupted = False
    deadline_reached = False
    cancellation_sent = False
    try:
        capture.start()
        while process.poll() is None:
            try:
                expired = time.monotonic() >= deadline or (
                    log_capture.error is not None and not cancellation_sent
                )
                if expired and not cancellation_sent:
                    (directory / "cancel").write_text("cancel\n", encoding="utf-8")
                    cancellation_sent = True
                    deadline_reached = not interrupted
                    deadline = time.monotonic() + options.cleanup_wait_seconds
                elif expired:
                    _stop_owned(process)
                    break
                time.sleep(max(0.05, options.poll_interval_seconds))
            except KeyboardInterrupt:
                interrupted = True
                if cancellation_sent:
                    _stop_owned(process)
                    break
                (directory / "cancel").write_text("cancel\n", encoding="utf-8")
                cancellation_sent = True
                deadline = time.monotonic() + options.cleanup_wait_seconds
        return_code = process.wait(timeout=5)
    finally:
        _stop_owned(process)
        if capture.ident is not None:
            capture.join(timeout=5)
    if capture.is_alive():
        raise PlayerLaunchError("Owned Player output stream did not close")
    if log_capture.error is not None:
        raise PlayerLaunchError("Owned Player log could not be retained") from log_capture.error
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
    exit_code = 0 if report.status == "succeeded" and not report.report_error else 1
    client_error = None
    if interrupted:
        exit_code = 130
        client_error = "Player interrupted; native finalization confirmed"
    elif deadline_reached:
        exit_code = 1
        client_error = "Player launcher deadline expired"
    return PlayerOutcome(raw, directory, exit_code, client_error)
