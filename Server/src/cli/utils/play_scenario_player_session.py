"""Bounded foreground sessions compare shared batches with fresh owned Player processes."""

from collections import deque
from hashlib import sha256
import json
import os
import stat
from pathlib import Path
import re
import time
from typing import Annotated, Final, Literal
from uuid import uuid4
from xml.etree import ElementTree

from models.play_scenarios import JobId, PlayScenarioCommand
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player
from cli.utils.play_scenario_player_payload import PlayerPayloadError
from cli.utils.play_scenario_reports import write_player_artifacts

OUTCOME_LIMIT: Final = 4096
OUTCOME_FILE_LIMIT: Final = 8 * 1024**2
SUMMARY_LIMIT: Final = 65536
CHILD_ARTIFACT_LIMIT: Final = 8 * 1024**2
OWNED_FILES: Final = frozenset(
    {
        "request.json",
        "definition.json",
        "run.json",
        "junit.xml",
        "player.log",
        "progress.json",
        "process.json",
        "diagnostics.json",
        "cancel",
        "run.json.tmp",
        "progress.json.tmp",
    }
)


class PlayerSessionOptions(BaseModel):
    """Strict local admission, retention and runtime bounds for one finite session."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    build_directory: Path
    output_directory: Path
    mode: Literal["shared-batches", "fresh-process", "compare"] = "shared-batches"
    iterations: Annotated[int, Field(strict=True, ge=1, le=1000)] = 100
    batch_size: Annotated[int, Field(strict=True, ge=2, le=10)] = 10
    max_runtime_seconds: Annotated[int, Field(strict=True, ge=1, le=86400)] = 3600
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=1800)] = 300
    cleanup_wait_seconds: Annotated[int, Field(strict=True, ge=1, le=600)] = 360
    interval_seconds: Annotated[float, Field(strict=True, ge=0, le=60, allow_inf_nan=False)] = 0.5
    failure_policy: Literal["stop", "continue"] = "stop"
    retain_reports: Annotated[int, Field(strict=True, ge=1, le=64)] = 16
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_id: JobId | None = None

    @field_validator("source_revision", "expected_build_revision")
    @classmethod
    def bounded_revision(cls, value: str | None) -> str | None:
        return PlayScenarioCommand.check_revision(value)


class PlayerProcessReceipt(BaseModel):
    """Bounded launcher-owned lifetime evidence retained even after child artifact eviction."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    job_id: JobId
    process_id: Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
    actual_exit_code: Annotated[int, Field(strict=True, ge=-(2**31), le=2**32 - 1)] | None
    process_ended: Annotated[bool, Field(strict=True)]
    forced_termination: Annotated[bool, Field(strict=True)]
    started_unix_ms: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    finished_unix_ms: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    request_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def valid_lifetime(self) -> "PlayerProcessReceipt":
        if self.finished_unix_ms < self.started_unix_ms:
            raise ValueError("Process receipt interval is reversed")
        if self.process_ended != (self.actual_exit_code is not None):
            raise ValueError("Process exit observation and finalization disagree")
        return self

    def outcome_fields(self) -> dict[str, JsonValue]:
        """Distinguish process lifetime timestamps from native scenario execution timestamps."""
        return {
            "process_id": self.process_id,
            "actual_exit_code": self.actual_exit_code,
            "process_ended": self.process_ended,
            "forced_termination": self.forced_termination,
            "process_started_unix_ms": self.started_unix_ms,
            "process_finished_unix_ms": self.finished_unix_ms,
            "request_sha256": self.request_sha256,
        }


def _read_small(path: Path) -> dict[str, JsonValue]:
    if not path.is_file() or path.is_symlink():
        return {}
    with path.open("rb") as stream:
        encoded = stream.read(SUMMARY_LIMIT + 1)
    if len(encoded) > SUMMARY_LIMIT:
        raise PlayerLaunchError("Session child evidence exceeds its byte bound", path.parent)
    value = json.loads(encoded)
    return value if isinstance(value, dict) else {}


def _retain(directory: Path, kept: deque[Path], pinned: str | None, limit: int) -> None:
    """Evict only bounded, finalized owned child files beneath this session's runs directory."""
    if not directory.exists():
        return
    if (
        directory.is_symlink()
        or getattr(directory.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not re.fullmatch(r"[0-9a-f]{32}", directory.name)
    ):
        raise PlayerLaunchError("Session artifact directory identity is invalid")
    info = _read_small(directory / "process.json")
    if not info.get("process_ended"):
        raise PlayerLaunchError(
            "Session cannot retain/evict an unconfirmed owned process", directory
        )
    entries = []
    with os.scandir(directory) as discovered:
        for entry in discovered:
            if len(entries) >= len(OWNED_FILES):
                raise PlayerLaunchError("Session child contains too many artifacts", directory)
            entries.append(Path(entry.path))
    if any(
        entry.name not in OWNED_FILES
        or entry.is_symlink()
        or getattr(entry.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not entry.is_file()
        for entry in entries
    ):
        raise PlayerLaunchError("Session child contains unexpected or linked artifacts", directory)
    if sum(entry.stat().st_size for entry in entries) > CHILD_ARTIFACT_LIMIT:
        raise PlayerLaunchError("Session child artifacts exceed their byte bound", directory)
    kept.append(directory)
    while len(kept) > limit:
        candidate = next(entry for entry in kept if entry.name != pinned)
        if candidate.resolve().parent != directory.resolve().parent:
            raise PlayerLaunchError("Session eviction target leaves the owned runs directory")
        for entry in candidate.iterdir():
            if (
                entry.name not in OWNED_FILES
                or entry.is_symlink()
                or getattr(entry.lstat(), "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or not entry.is_file()
            ):
                raise PlayerLaunchError(
                    "Session eviction encountered unexpected or linked artifacts"
                )
            entry.unlink()
        candidate.rmdir()
        kept.remove(candidate)


def _summary_file(summary: dict[str, JsonValue], directory: Path) -> None:
    encoded = json.dumps(summary, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > SUMMARY_LIMIT:
        raise PlayerLaunchError("Player session summary exceeds its byte bound")
    temporary = directory / "session.json.tmp"
    temporary.write_bytes(encoded + b"\n")
    temporary.replace(directory / "session.json")


def _write_session_junit(summary: dict[str, JsonValue], directory: Path) -> None:
    """Stream compact child outcomes; raw native reports are never collected in memory."""
    counts = summary["outcome_counts"]
    incomplete = (
        summary["status"] in ("cancelled", "timed_out")
        or summary.get("session_error") is not None
        or not all(summary["arms_complete"].values())
    )
    header = ElementTree.Element(
        "testsuite",
        {
            "name": "player-session",
            "tests": str(summary["outcomes_recorded"] + int(incomplete)),
            "failures": str(counts["failures"]),
            "errors": str(counts["errors"] + int(incomplete)),
            "skipped": "0",
            "time": f"{max(0, summary['finished_unix_ms'] - summary['started_unix_ms']) / 1000:.3f}",
        },
    )
    opening = ElementTree.tostring(header, encoding="utf-8").replace(b" />", b">")
    with (
        (directory / "junit.xml").open("wb") as output,
        (directory / "outcomes.jsonl").open("rb") as inputs,
    ):
        output.write(b'<?xml version="1.0" encoding="utf-8"?>\n' + opening)
        for _index in range(2000):
            encoded = inputs.readline(OUTCOME_LIMIT + 1)
            if not encoded:
                break
            if len(encoded) > OUTCOME_LIMIT or not encoded.endswith(b"\n"):
                raise PlayerLaunchError("Player session outcome journal is truncated or oversized")
            entry = json.loads(encoded)
            start, finish = entry.get("started_unix_ms"), entry.get("finished_unix_ms")
            duration = (
                max(0, finish - start) / 1000
                if isinstance(start, int) and isinstance(finish, int)
                else 0
            )
            case = ElementTree.Element(
                "testcase",
                name=str(entry["job_id"]),
                classname=str(entry["mode"]),
                time=f"{duration:.3f}",
            )
            properties = ElementTree.SubElement(case, "properties")
            for key in (
                "mode",
                "process_scope",
                "repeat_count",
                "native_report_available",
                "native_report_sha256",
                "artifact_directory",
                "definition_hash",
                "build_id",
                "build_source_revision",
                "payload_hash",
                "payload_verification",
            ):
                ElementTree.SubElement(
                    properties, "property", name=key, value=str(entry.get(key, ""))
                )
            if entry["status"] == "failed" and not entry.get("client_error"):
                ElementTree.SubElement(
                    case, "failure", type="scenario_failed"
                ).text = "Native Player scenario failed"
            elif (
                entry["status"] != "succeeded"
                or entry["exit_code"] != 0
                or entry.get("client_error")
            ):
                ElementTree.SubElement(case, "error", type="player_child_error").text = str(
                    entry.get("client_error") or entry["status"]
                )
            output.write(ElementTree.tostring(case, encoding="utf-8"))
        if inputs.read(1):
            raise PlayerLaunchError("Player session outcome journal exceeds its child bound")
        if incomplete:
            case = ElementTree.Element(
                "testcase", name="session_completion", classname="player-session", time="0.000"
            )
            ElementTree.SubElement(case, "error", type="session_incomplete").text = str(
                summary["status"]
            )
            output.write(ElementTree.tostring(case, encoding="utf-8"))
        output.write(b"</testsuite>")


def run_player_session(options: PlayerSessionOptions) -> tuple[dict[str, JsonValue], Path, int]:
    """Run finite batches serially, preserve the first failure, and stop admission on cancellation."""
    session_id = uuid4().hex
    options.output_directory.mkdir(parents=True, exist_ok=True)
    directory = options.output_directory.resolve() / session_id
    if directory.is_relative_to(options.build_directory.resolve()):
        raise PlayerLaunchError(
            "Player sessions must store artifacts outside the immutable build directory"
        )
    directory.mkdir(exist_ok=False)
    runs = directory / "runs"
    runs.mkdir()
    modes = ("shared-batches", "fresh-process") if options.mode == "compare" else (options.mode,)
    completed = {mode: 0 for mode in modes}
    scheduled = {mode: 0 for mode in modes}
    counts = {"failures": 0, "errors": 0}
    summary: dict[str, JsonValue] = {
        "schema_version": 1,
        "session_id": session_id,
        "status": "running",
        "mode": options.mode,
        "process_scope": "batch",
        "iterations_per_process": {
            mode: options.batch_size if mode == "shared-batches" else 1 for mode in modes
        },
        "requested_iterations": options.iterations,
        "completed_iterations": completed,
        "scheduled_iterations": scheduled,
        "children_started": 0,
        "children_finalized": 0,
        "outcomes_recorded": 0,
        "first_failure": None,
        "session_error": None,
        "arms_complete": {mode: False for mode in modes},
        "started_unix_ms": int(time.time() * 1000),
        "finished_unix_ms": None,
        "last_outcomes": [],
        "retained_job_ids": [],
        "outcome_counts": counts,
        "source_revision": options.source_revision,
        "expected_build_revision": options.expected_build_revision,
        "expected_build_id": options.expected_build_id,
    }
    recent: deque[dict[str, JsonValue]] = deque(maxlen=32)
    kept: deque[Path] = deque()
    pinned: str | None = None
    deadline = time.monotonic() + options.max_runtime_seconds
    stopped = False
    exit_code = 0
    _summary_file(summary, directory)
    with (directory / "outcomes.jsonl").open("xb") as journal:
        for mode in modes:
            while scheduled[mode] < options.iterations and not stopped:
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    summary["status"] = "timed_out"
                    exit_code = 1
                    stopped = True
                    break
                repeats = min(
                    options.batch_size if mode == "shared-batches" else 1,
                    options.iterations - scheduled[mode],
                )
                job_id = uuid4().hex
                child_directory = runs / job_id
                record: dict[str, JsonValue] = {
                    "schema_version": 1,
                    "session_id": session_id,
                    "sequence": summary["outcomes_recorded"] + 1,
                    "mode": mode,
                    "process_scope": "batch",
                    "job_id": job_id,
                    "repeat_count": repeats,
                    "status": "infrastructure_error",
                    "exit_code": 1,
                    "native_report_available": False,
                    "native_report_sha256": None,
                    "artifact_directory": f"runs/{job_id}",
                    "started_unix_ms": None,
                    "finished_unix_ms": None,
                    "client_error": None,
                    "process_id": None,
                    "actual_exit_code": None,
                    "process_ended": False,
                    "forced_termination": False,
                    "process_started_unix_ms": None,
                    "process_finished_unix_ms": None,
                    "request_sha256": None,
                }
                expected_native_exit = None
                try:
                    outcome = run_player(
                        PlayerRunOptions(
                            build_directory=options.build_directory,
                            output_directory=runs,
                            repeat_count=repeats,
                            timeout_seconds=options.timeout_seconds,
                            source_revision=options.source_revision,
                            cleanup_wait_seconds=options.cleanup_wait_seconds,
                            deadline_monotonic=deadline,
                            expected_build_revision=options.expected_build_revision,
                            expected_build_id=options.expected_build_id,
                            require_verified_payload=True,
                            player_job_id=job_id,
                        )
                    )
                    child_directory = outcome.directory
                    expected_native_exit = outcome.report["exit_code"]
                    record.update(
                        {
                            "status": outcome.report["status"],
                            "exit_code": outcome.exit_code,
                            "native_report_available": True,
                            "native_report_sha256": sha256(
                                (child_directory / "run.json").read_bytes()
                            ).hexdigest(),
                            "started_unix_ms": outcome.report.get("started_unix_ms"),
                            "finished_unix_ms": outcome.report.get("finished_unix_ms"),
                            "client_error": outcome.client_error
                            or (
                                "player_native_report_export_failed"
                                if outcome.report.get("report_error")
                                else None
                            ),
                            **{
                                key: outcome.report["reproduction"].get(key)
                                for key in (
                                    "definition_hash",
                                    "build_id",
                                    "build_source_revision",
                                    "payload_hash",
                                    "payload_verification",
                                )
                            },
                        }
                    )
                    # Native final reports retain skipped slots for unexecuted repeats.
                    observed_iterations = (
                        repeats
                        if outcome.report["status"] == "succeeded"
                        else len(
                            {
                                step["iteration"]
                                for step in outcome.report.get("steps", [])
                                if isinstance(step, dict)
                                and type(step.get("iteration")) is int
                                and 1 <= step["iteration"] <= repeats
                                and step.get("status") != "skipped"
                                and step.get("started_unix_ms") is not None
                            }
                        )
                    )
                    completed[mode] += min(repeats, observed_iterations)
                    write_player_artifacts(outcome.report, child_directory, outcome.client_error)
                    del outcome
                except KeyboardInterrupt:
                    record["status"], record["exit_code"], record["client_error"] = (
                        "cancelled",
                        130,
                        "Session interrupted before child admission",
                    )
                except (PlayerLaunchError, PlayerPayloadError, OSError, ValueError) as exc:
                    record["exit_code"] = 1
                    record["client_error"] = (
                        type(exc).__name__ + ": player child admission or export failed"
                    )
                process: dict[str, JsonValue] = {}
                try:
                    encoded_process = _read_small(child_directory / "process.json")
                    if encoded_process:
                        receipt = PlayerProcessReceipt.model_validate(encoded_process)
                        if receipt.job_id != job_id or (
                            expected_native_exit is not None
                            and receipt.actual_exit_code != expected_native_exit
                        ):
                            raise ValueError("Process receipt does not match the owned child")
                        process = receipt.model_dump(mode="json")
                        record.update(receipt.outcome_fields())
                    elif record["native_report_available"]:
                        raise ValueError("Process lifetime receipt is missing")
                except (PlayerLaunchError, OSError, ValueError):
                    record["exit_code"] = 1
                    record["client_error"] = (
                        record["client_error"] or "session_process_evidence_invalid"
                    )

                diagnostics = _read_small(child_directory / "diagnostics.json")
                if diagnostics.get("interrupted") is True:
                    record["exit_code"], record["status"] = 130, "cancelled"
                elif diagnostics.get("deadline_reached") is True and time.monotonic() >= deadline:
                    summary["status"] = "timed_out"
                    stopped = True
                if process.get("process_id") is not None:
                    summary["children_started"] += 1
                if process.get("process_ended") is True:
                    summary["children_finalized"] += 1
                failed = record["exit_code"] != 0
                if failed:
                    exit_code = 130 if record["exit_code"] == 130 else max(1, exit_code)
                    counts[
                        "failures"
                        if record["status"] == "failed" and not record["client_error"]
                        else "errors"
                    ] += 1
                    if summary["first_failure"] is None:
                        summary["first_failure"] = dict(record)
                        pinned = job_id
                scheduled[mode] += repeats
                # Advance admission once, including a failed child; no retry can erase it.
                if process.get("process_ended") is not True and child_directory.exists():
                    stopped = True
                encoded = (
                    json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                if (
                    len(encoded) > OUTCOME_LIMIT
                    or journal.tell() + len(encoded) > OUTCOME_FILE_LIMIT
                    or summary["outcomes_recorded"] >= 2000
                ):
                    raise PlayerLaunchError(
                        "Player session outcome journal exceeds its fixed bounds"
                    )
                journal.write(encoded)
                journal.flush()
                summary["outcomes_recorded"] += 1
                recent.append(record)
                while len(json.dumps(list(recent), ensure_ascii=True).encode("utf-8")) > 49152:
                    recent.popleft()
                if process.get("process_ended") is True:
                    try:
                        _retain(child_directory, kept, pinned, options.retain_reports)
                    except (PlayerLaunchError, OSError, ValueError):
                        summary["session_error"] = "session_retention_failed"
                        stopped, exit_code = True, max(1, exit_code)
                summary["last_outcomes"] = list(recent)
                summary["retained_job_ids"] = [entry.name for entry in kept]
                summary["arms_complete"] = {
                    arm: completed[arm] >= options.iterations for arm in modes
                }
                _summary_file(summary, directory)
                if record["exit_code"] == 130:
                    summary["status"] = "cancelled"
                    stopped = True
                elif failed and options.failure_policy == "stop":
                    stopped = True
                if not stopped and scheduled[mode] < options.iterations:
                    try:
                        time.sleep(
                            min(options.interval_seconds, max(0, deadline - time.monotonic()))
                        )
                    except KeyboardInterrupt:
                        summary["status"] = "cancelled"
                        exit_code, stopped = 130, True
    if summary["status"] == "running":
        summary["status"] = (
            "failed"
            if summary["first_failure"] is not None or summary["session_error"] is not None
            else "succeeded"
        )
    summary["finished_unix_ms"] = int(time.time() * 1000)
    _summary_file(summary, directory)
    _write_session_junit(summary, directory)
    return summary, directory, exit_code
