"""Admit complete successful or failed same-process soak histories offline."""

from hashlib import sha256
from pathlib import Path

from models.play_scenarios import PlayScenario, normalize_native_definition

from cli.utils.play_scenario_analysis_io import AnalysisError, read_bytes
from cli.utils.play_scenario_history_models import HistoryEvidence, HistoryOutcome
from cli.utils.play_scenario_history_session import _decode
from cli.utils.play_scenario_iterations import zero_iteration_counts
from cli.utils.play_scenario_player import PlayerFinalReport, REPORT_LIMIT
from cli.utils.play_scenario_soak import (
    IDENTITY_FIELDS,
    SUMMARY_LIMIT,
    JOURNAL_LIMIT,
    ROW_LIMIT,
    SoakIdentity,
    SoakSummary,
)
from cli.utils.play_scenario_soak_compare_evidence import _Request
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS, RecordedEnvironment
from cli.utils.play_scenario_soak_reports import ClientSoakSummary, _ProcessReceipt, admit_soak_run


def read_soak_history(source: Path) -> tuple[HistoryEvidence, ...]:
    """Require owned natural process exit and exact native/client journal attribution."""
    directory = source.parent
    request_bytes = read_bytes(directory / "request.json", SUMMARY_LIMIT)
    request = _Request.model_validate(_decode(request_bytes))
    receipt = _ProcessReceipt.model_validate(
        _decode(read_bytes(directory / "process.json", SUMMARY_LIMIT))
    )
    raw = _decode(read_bytes(directory / "run.json", REPORT_LIMIT))
    report = PlayerFinalReport.model_validate(raw)
    if (
        receipt.job_id != request.job_id
        or receipt.request_sha256 != sha256(request_bytes).hexdigest()
        or not receipt.process_ended
        or receipt.forced_termination
        or receipt.actual_exit_code != report.exit_code
        or report.job_id != request.job_id
        or report.player_schema_version != 2
        or report.timeout_seconds != request.timeout_seconds
        or report.report_error is not None
        or report.progress_error is not None
        or raw.get("persistence_error") is not None
    ):
        raise AnalysisError("Soak final report lacks matching natural process evidence")
    identity = SoakIdentity.model_validate(
        {
            key: receipt.process_id if key == "process_id" else getattr(request, key)
            for key in IDENTITY_FIELDS
        }
    )
    definition = read_bytes(directory / "definition.json", SUMMARY_LIMIT)
    definition = definition.removesuffix(b"\r\n").removesuffix(b"\n")
    scenario = PlayScenario.model_validate(normalize_native_definition(_decode(definition)))
    if (
        sha256(definition).hexdigest() != identity.definition_hash
        or scenario != report.scenario
        or scenario.name != request.scenario_name
        or any(
            report.reproduction.get(key) != getattr(identity, key)
            for key in IDENTITY_FIELDS
            if key not in {"job_id", "process_id"}
        )
        or report.reproduction.get("source_revision") != request.source_revision
        or report.reproduction.get("payload_verification") != "launcher_admission"
    ):
        raise AnalysisError("Soak frozen definition or reproduction attribution disagrees")
    # Validate every fixed owned artifact path before the native admission helper reads it.
    journal_bytes = read_bytes(directory / "iterations.jsonl", JOURNAL_LIMIT)
    lines = journal_bytes.splitlines(keepends=True)
    if len(lines) > 1000 or any(
        len(line) > ROW_LIMIT or not line.endswith(b"\n") for line in lines
    ):
        raise AnalysisError("Soak journal is incomplete or exceeds its row bound")
    for line in lines:
        _decode(line)
    checkpoint = _decode(read_bytes(directory / "soak.json", SUMMARY_LIMIT))
    filenames = checkpoint.get("retained_report_files")
    if not isinstance(filenames, list):
        raise AnalysisError("Soak retained report inventory is unavailable")
    # SoakSummary admission owns basename syntax, count and selected-report validation.
    parsed_checkpoint = SoakSummary.model_validate(checkpoint)
    for filename in parsed_checkpoint.retained_report_files:
        _decode(read_bytes(directory / filename, REPORT_LIMIT))
    summary, journal = admit_soak_run(directory, raw, request.soak, identity)
    if summary != parsed_checkpoint or journal.digest != sha256(journal_bytes).hexdigest():
        raise AnalysisError("Soak evidence changed during bounded history admission")
    if (summary.status == "succeeded") != (report.exit_code == 0):
        raise AnalysisError("Soak parent status disagrees with the observed process exit")
    client = ClientSoakSummary.model_validate(
        _decode(read_bytes(directory / "client-soak.json", SUMMARY_LIMIT))
    )
    expected = {
        "evidence_state": "complete",
        "status": summary.status,
        "job_id": identity.job_id,
        "process_id": identity.process_id,
        "requested_iterations": summary.requested_iterations,
        "recorded_iterations": len(journal.rows),
        "attempted_iterations": summary.attempted_iterations,
        "passed_iterations": summary.passed_iterations,
        "failed_iterations": summary.failed_iterations,
        "skipped_iterations": summary.skipped_iterations,
        "unknown_iterations": 0,
        "journal_bytes": journal.byte_count,
        "journal_sha256": journal.digest,
        "client_error": None,
    }
    if any(getattr(client, key) != value for key, value in expected.items()):
        raise AnalysisError("Client soak parent disagrees with complete native journal evidence")
    environment = (
        RecordedEnvironment.model_validate(report.reproduction["environment"])
        if (report.reproduction.get("environment") is not None)
        else None
    )
    known = bool(
        environment
        and all(
            getattr(environment, key) is not None
            and str(getattr(environment, key)).strip().lower() not in {"", "unknown", "unavailable"}
            for key in ENVIRONMENT_FIELDS
        )
        and report.reproduction.get("unity_version")
        and report.reproduction.get("package_version")
    )
    outcomes = []
    totals = zero_iteration_counts()
    totals["planned"] = summary.requested_iterations
    for row in journal.rows:
        counts = zero_iteration_counts()
        counts["planned"] = 1
        counts["executed"] = int(row.started_unix_ms is not None)
        state = (
            "skipped"
            if row.started_unix_ms is None
            else (
                "passed" if row.passed else (row.status if row.status != "succeeded" else "failed")
            )
        )
        counts[state] = 1
        for key in ("executed", *[key for key in counts if key not in {"planned", "executed"}]):
            totals[key] += counts[key]
        outcomes.append(
            HistoryOutcome(
                job_id=row.job_id,
                sequence=row.iteration,
                first_iteration=row.iteration,
                last_iteration=row.iteration,
                status=row.status,
                counts=counts,
                first_failure_iteration=row.iteration
                if row.started_unix_ms is not None and not row.passed
                else None,
                failure_code=row.failure_code,
                failure={
                    "error": row.error,
                    "cleanup_error": row.cleanup_error,
                    "unexpected_log_count": row.unexpected_log_count,
                },
                started_unix_ms=row.started_unix_ms,
                finished_unix_ms=row.finished_unix_ms,
            )
        )
    totals["skipped"] += summary.requested_iterations - len(journal.rows)
    return (
        HistoryEvidence(
            source,
            identity.job_id,
            "soak",
            "same-process",
            summary.status,
            {**report.reproduction, "execution_environment": "player"},
            known,
            True,
            totals,
            tuple(outcomes),
            () if known else ("Recorded environment is unavailable or incomplete.",),
        ),
    )
