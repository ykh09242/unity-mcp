"""Admit native soak evidence and export truthful client summaries and JUnit."""

from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
from typing import Annotated, Literal, TYPE_CHECKING
from uuid import uuid4
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from cli.utils.play_scenario_iterations import validate_iteration_results
from cli.utils.play_scenario_reports import _duration, _properties, _xml_text
from cli.utils.play_scenario_soak import (
    IDENTITY_FIELDS,
    SoakContractError,
    SoakIdentity,
    SoakJournal,
    SoakRequest,
    SoakSummary,
    admit_soak_summary,
    parse_soak_json,
    read_soak_bytes,
    read_soak_json,
    read_soak_journal,
)

if TYPE_CHECKING:
    from cli.utils.play_scenario_player import PlayerRunOptions


class _ProcessReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")
    schema_version: Annotated[int, Field(ge=1, le=1)]
    job_id: str
    process_id: Annotated[int, Field(ge=1)]
    request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    process_ended: bool
    forced_termination: bool
    actual_exit_code: int | None


class ClientSoakSummary(BaseModel):
    """Separate launcher evidence never overwrites native soak.json or run.json."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    execution_environment: Literal["player"] = "player"
    process_scope: Literal["same-process"] = "same-process"
    evidence_state: Literal["complete", "incomplete"]
    status: Literal["succeeded", "failed", "cancelled", "timed_out", "incomplete"]
    job_id: str
    process_id: int | None
    requested_iterations: int
    recorded_iterations: int | None
    attempted_iterations: int | None
    passed_iterations: int | None
    failed_iterations: int | None
    skipped_iterations: int | None
    unknown_iterations: int | None
    journal_bytes: int | None
    journal_sha256: str | None
    client_error: str | None
    native_summary_file: Literal["soak.json"] = "soak.json"
    journal_file: Literal["iterations.jsonl"] = "iterations.jsonl"
    native_report_file: Literal["run.json"] = "run.json"


@dataclass(frozen=True, slots=True)
class SoakOutcome:
    summary: ClientSoakSummary
    directory: Path
    exit_code: int


def admit_soak_run(
    directory: Path,
    raw: dict[str, JsonValue],
    request: SoakRequest,
    identity: SoakIdentity,
) -> tuple[SoakSummary, SoakJournal]:
    """Join one final checkpoint, one journal pass and every bounded retained full report."""
    from cli.utils.play_scenario_player import PlayerFinalReport, REPORT_LIMIT

    checkpoint = read_soak_json(directory / "soak.json")
    summary = SoakSummary.model_validate(checkpoint)
    if raw.get("soak") != checkpoint:
        raise SoakContractError("Soak run projection disagrees with native final checkpoint")
    journal = read_soak_journal(directory / "iterations.jsonl", identity)
    admit_soak_summary(summary, request, identity, journal)
    selected_report = PlayerFinalReport.model_validate(raw)
    if selected_report.scenario.metrics.enabled:
        if summary.metrics_summary is None or (
            summary.metrics_summary.warmup_iterations
            != selected_report.scenario.metrics.warmup_iterations
        ):
            raise SoakContractError(
                "Soak metrics evidence disagrees with the frozen sampling policy"
            )
    elif summary.metrics_summary is not None or any(row.metrics for row in journal.rows):
        raise SoakContractError("Disabled soak metrics cannot claim boundary samples")
    selected = summary.selected_iteration
    if selected is None:
        if summary.status == "succeeded":
            raise SoakContractError("Soak success cannot select an unstarted iteration")
    for filename in summary.retained_report_files:
        full = read_soak_json(directory / filename, REPORT_LIMIT)
        report = PlayerFinalReport.model_validate(full)
        row = next(entry for entry in journal.rows if entry.report_file == filename)
        ledger = validate_iteration_results(full, 1, terminal=True)
        if (
            ledger is None
            or ledger[0].started_unix_ms != row.started_unix_ms
            or ledger[0].finished_unix_ms != row.finished_unix_ms
            or report.job_id != identity.job_id
            or report.status != row.status
            or report.repeat_count != 1
            or report.timeout_seconds != raw.get("timeout_seconds")
            or full.get("scenario") != raw.get("scenario")
            or full.get("reproduction") != raw.get("reproduction")
            or report.runner_resources_released != row.runner_resources_released
        ):
            raise SoakContractError("Soak retained native report disagrees with its journal row")
        if row.iteration == selected and {
            key: value for key, value in raw.items() if key not in {"soak", "exit_code"}
        } != {key: value for key, value in full.items() if key not in {"soak", "exit_code"}}:
            raise SoakContractError("Soak selected report is not the preserved actual native run")
    if (
        selected is not None
        and journal.rows[selected - 1].report_file not in summary.retained_report_files
    ):
        raise SoakContractError("Soak selected native report was not retained")
    return summary, journal


def _owned_identity(directory: Path) -> SoakIdentity:
    """A persisted receipt binds the exact fresh request bytes to one owned child PID."""
    request_path = directory / "request.json"
    request_encoded = read_soak_bytes(request_path)
    request = parse_soak_json(request_encoded)
    receipt = _ProcessReceipt.model_validate(read_soak_json(directory / "process.json"))
    if (
        receipt.job_id != request.get("job_id")
        or receipt.request_sha256 != sha256(request_encoded).hexdigest()
    ):
        raise SoakContractError("Soak owned process receipt does not bind the exact request")
    return SoakIdentity.model_validate(
        {
            key: receipt.process_id if key == "process_id" else request.get(key)
            for key in IDENTITY_FIELDS
        }
    )


def _client_summary(
    request: SoakRequest,
    job_id: str,
    identity: SoakIdentity | None,
    journal: SoakJournal | None,
    native: SoakSummary | None,
    error: str | None,
) -> ClientSoakSummary:
    attempted = (
        sum(row.started_unix_ms is not None for row in journal.rows)
        if journal is not None
        else None
    )
    passed = sum(row.passed for row in journal.rows) if journal is not None else None
    return ClientSoakSummary(
        evidence_state="complete" if native is not None and not error else "incomplete",
        status=native.status if native is not None and not error else "incomplete",
        job_id=job_id,
        process_id=identity.process_id if identity is not None else None,
        requested_iterations=request.iterations,
        recorded_iterations=len(journal.rows) if journal is not None else None,
        attempted_iterations=attempted,
        passed_iterations=passed,
        failed_iterations=attempted - passed
        if attempted is not None and passed is not None
        else None,
        skipped_iterations=(
            request.iterations - attempted
            if native is not None and not error and attempted is not None
            else sum(row.started_unix_ms is None for row in journal.rows)
            if journal is not None
            else None
        ),
        unknown_iterations=(
            0
            if native is not None and not error
            else request.iterations - len(journal.rows)
            if journal is not None
            else None
        ),
        journal_bytes=journal.byte_count if journal is not None else None,
        journal_sha256=journal.digest if journal is not None else None,
        client_error=error,
    )


def _atomic_write(path: Path, encoded: bytes) -> None:
    """Publish only a complete file; propagate a failed write or replacement to the caller."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_soak_junit(
    directory: Path, summary: ClientSoakSummary, journal: SoakJournal | None
) -> Path:
    """Represent every planned iteration, distinguishing actual attempts and unavailable evidence."""
    suite = ElementTree.Element(
        "testsuite", name="player-soak", tests=str(summary.requested_iterations)
    )
    _properties(suite, summary.model_dump(mode="json"))
    rows = {row.iteration: row for row in journal.rows} if journal is not None else {}
    failures = errors = skipped = 0
    for iteration in range(1, summary.requested_iterations + 1):
        row = rows.get(iteration)
        case = ElementTree.SubElement(
            suite,
            "testcase",
            classname="player-soak",
            name=f"iteration-{iteration:04d}",
            time=_duration(row.started_unix_ms, row.finished_unix_ms)
            if row is not None
            else "0.000",
        )
        if row is not None:
            _properties(
                case,
                {
                    "iteration": iteration,
                    "process_id": row.process_id,
                    "report_file": row.report_file,
                },
            )
        if journal is None:
            errors += 1
            ElementTree.SubElement(
                case, "error", type="execution_unverified"
            ).text = "Execution evidence unavailable"
        elif row is None and summary.evidence_state == "incomplete":
            errors += 1
            ElementTree.SubElement(
                case, "error", type="execution_unverified"
            ).text = "No durable terminal iteration evidence"
        elif row is None or row.started_unix_ms is None:
            skipped += 1
            ElementTree.SubElement(case, "skipped").text = "Iteration was not started"
        elif not row.passed:
            if row.status == "failed" and row.runner_resources_released:
                failures += 1
                tag = "failure"
            else:
                errors += 1
                tag = "error"
            ElementTree.SubElement(
                case, tag, type=_xml_text(row.failure_code or row.status)
            ).text = _xml_text(row.error or row.cleanup_error or row.status)
    if summary.client_error:
        case = ElementTree.SubElement(
            suite, "testcase", classname="player-soak", name="soak-finalization", time="0.000"
        )
        ElementTree.SubElement(case, "error", type="soak_infrastructure_error").text = _xml_text(
            summary.client_error
        )
        errors += 1
        suite.set("tests", str(summary.requested_iterations + 1))
    elif summary.status != "succeeded" and not (failures or errors):
        case = ElementTree.SubElement(
            suite, "testcase", classname="player-soak", name="soak-outcome", time="0.000"
        )
        ElementTree.SubElement(case, "error", type=f"soak_{summary.status}").text = summary.status
        errors += 1
        suite.set("tests", str(summary.requested_iterations + 1))
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    suite.set("skipped", str(skipped))
    path = directory / "junit.xml"
    _atomic_write(path, ElementTree.tostring(suite, encoding="utf-8", xml_declaration=True))
    return path


def run_player_soak(options: "PlayerRunOptions") -> SoakOutcome:
    """Launch once and preserve incomplete evidence after a native or launcher failure."""
    from cli.utils.play_scenario_player import PlayerLaunchError, run_player

    if options.soak is None:
        raise SoakContractError("Explicit Player soak request is required")
    options = replace(options, player_job_id=options.player_job_id or uuid4().hex)
    directory = options.output_directory.resolve() / str(options.player_job_id)
    if directory.exists() or directory.is_symlink():
        raise FileExistsError("Player soak output directory already exists")
    native = None
    error = None
    exit_code = 1
    journal = None
    try:
        outcome = run_player(options)
        native = SoakSummary.model_validate(outcome.report["soak"])
        error = outcome.client_error
        exit_code = outcome.exit_code
        journal = outcome.soak_journal
        if (
            exit_code == 130
            and outcome.diagnostics.get("progress_error") is None
            and outcome.report.get("progress_error") is None
        ):
            # An acknowledged user cancellation has a complete native final outcome.
            error = None
    except (PlayerLaunchError, OSError, ValueError) as exc:
        if not directory.is_dir() or not (directory / "request.json").is_file():
            raise
        error = str(exc)[:2048]
    identity = None
    try:
        identity = _owned_identity(directory)
        if journal is None:
            journal = read_soak_journal(directory / "iterations.jsonl", identity, partial=True)
        error = error or journal.error
    except (OSError, ValueError) as exc:
        error = error or str(exc)[:2048]
    summary = _client_summary(
        options.soak, str(options.player_job_id), identity, journal, native, error
    )
    _atomic_write(directory / "client-soak.json", summary.model_dump_json().encode("utf-8") + b"\n")
    write_soak_junit(directory, summary, journal)
    return SoakOutcome(summary, directory, exit_code if not error or exit_code == 130 else 1)
