"""One repository corpus exercises native admission, CI export and recovery contracts."""

import json
from pathlib import Path
from typing import Annotated, Final
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
import pytest

from cli.utils.play_scenario_iterations import IterationResultsError, iteration_counts
from cli.utils.play_scenario_player import PlayerFinalReport, PlayerLaunchError
from cli.utils.play_scenario_reports import junit_xml
from cli.utils.play_scenario_session_recovery import _Outcome, recover_player_session
from cli.utils.play_scenario_soak import (
    SoakContractError,
    SoakIdentity,
    SoakRequest,
    SoakSummary,
    admit_soak_summary,
    read_soak_journal,
)
from cli.utils.play_scenario_soak_reports import admit_soak_run

FIXTURE: Final = (
    Path(__file__).resolve().parents[2]
    / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/PlayScenarios"
    / "Fixtures/shared-report-contract.json"
)


class NativeCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    report: dict[str, JsonValue]
    accepted: bool
    expected_counts: dict[str, int] | None


class SessionCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    session: dict[str, JsonValue]
    accepted: bool
    recovery_counts: dict[str, int]


class UnicodeCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    value: str
    xml_text: str
    json_utf8_bytes: int


class SoakCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    identity: dict[str, JsonValue]
    request: dict[str, JsonValue]
    summary: dict[str, JsonValue]
    journal_text: str
    accepted: bool
    editor_result: str
    run: dict[str, JsonValue]
    reports: dict[str, dict[str, JsonValue]]


class Corpus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(ge=1, le=1)]
    native_cases: list[NativeCase]
    session_cases: list[SessionCase]
    engine_cases: list[dict[str, JsonValue]]
    unicode_cases: list[UnicodeCase]
    soak_cases: list[SoakCase]


CORPUS: Final = Corpus.model_validate_json(FIXTURE.read_bytes())


@pytest.mark.parametrize("case", CORPUS.native_cases, ids=lambda case: case.name)
def test_native_report_admission_and_junit_use_shared_truth(case: NativeCase) -> None:
    report = case.report
    repeat_count = report["repeat_count"]
    assert type(repeat_count) is int
    suite: dict[str, JsonValue] = {
        "suite": {"name": "shared-contract"},
        "scenarios": [{"name": case.name, "status": report["status"], "report": report}],
    }
    if not case.accepted:
        with pytest.raises((ValidationError, IterationResultsError, PlayerLaunchError)):
            PlayerFinalReport.model_validate(report)
        with pytest.raises((ValidationError, IterationResultsError)):
            junit_xml(suite)
        return
    admitted = PlayerFinalReport.model_validate(report)
    assert admitted.status == report["status"]
    assert iteration_counts(report, repeat_count) == case.expected_counts
    root = ElementTree.fromstring(junit_xml(suite))
    properties = {
        item.attrib["name"].removeprefix("iteration."): int(item.attrib["value"])
        for item in root.findall("testcase/properties/property")
        if item.attrib["name"].startswith("iteration.")
    }
    assert properties == case.expected_counts
    assert root.attrib["failures"] == str(int(report["status"] == "failed"))
    assert root.attrib["errors"] == str(int(report["status"] in {"timed_out", "cancelled"}))


@pytest.mark.parametrize("case", CORPUS.session_cases, ids=lambda case: case.name)
def test_session_outcome_and_recovery_use_shared_truth(case: SessionCase, tmp_path: Path) -> None:
    session = case.session
    recent = session["last_outcomes"]
    assert isinstance(recent, list) and isinstance(recent[0], dict)
    outcome = recent[0]
    if case.accepted:
        _Outcome.model_validate(outcome)
    else:
        with pytest.raises((ValidationError, PlayerLaunchError)):
            _Outcome.model_validate(outcome)
    source = tmp_path / "source"
    source.mkdir()
    (source / "session-start.json").write_text(json.dumps(session), encoding="utf-8")
    admission: dict[str, JsonValue] = {
        "schema_version": 1,
        **{
            key: outcome[key]
            for key in ("session_id", "sequence", "mode", "job_id", "repeat_count")
        },
    }
    (source / "admissions.jsonl").write_text(json.dumps(admission) + "\n", encoding="utf-8")
    (source / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n", encoding="utf-8")
    original = (source / "outcomes.jsonl").read_bytes()
    summary, directory, exit_code = recover_player_session(source, tmp_path / "recovery")
    assert exit_code == 1 and summary["status"] == "interrupted"
    assert summary["iteration_counts"] == {"shared-batches": case.recovery_counts}
    assert bool(summary["recovery_issues"]) == (not case.accepted)
    assert (source / "outcomes.jsonl").read_bytes() == original
    xml = ElementTree.fromstring((directory / "junit.xml").read_bytes())
    properties = {
        item.attrib["name"]: item.attrib["value"] for item in xml.findall("properties/property")
    }
    assert properties["iteration.shared-batches.executed"] == str(case.recovery_counts["executed"])
    assert properties["iteration.shared-batches.unknown"] == str(case.recovery_counts["unknown"])


@pytest.mark.parametrize("case", CORPUS.unicode_cases, ids=lambda case: case.name)
def test_shared_unicode_controls_have_valid_escaped_xml_and_stable_json_bytes(
    case: UnicodeCase,
) -> None:
    encoded = json.dumps(case.value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) == case.json_utf8_bytes
    assert json.loads(encoded) == case.value
    suite: dict[str, JsonValue] = {
        "suite": {"name": "unicode-contract"},
        "scenarios": [
            {"name": "unicode-case", "status": "failed", "report": {"error": case.value}}
        ],
    }
    xml = junit_xml(suite)
    assert b"&lt;&amp;&gt;" in xml
    assert ElementTree.fromstring(xml).findtext("testcase/failure") == case.xml_text


@pytest.mark.parametrize("case", CORPUS.soak_cases, ids=lambda case: case.name)
def test_same_process_soak_admission_uses_exact_shared_journal_bytes(
    case: SoakCase, tmp_path: Path
) -> None:
    path = tmp_path / "iterations.jsonl"
    path.write_bytes(case.journal_text.encode("utf-8"))
    (tmp_path / "soak.json").write_text(json.dumps(case.summary), encoding="utf-8")
    for filename, report in case.reports.items():
        (tmp_path / filename).write_text(json.dumps(report), encoding="utf-8")
    identity = SoakIdentity.model_validate(case.identity)
    request = SoakRequest.model_validate(case.request)
    if not case.accepted:
        with pytest.raises((ValidationError, SoakContractError, ValueError)):
            summary = SoakSummary.model_validate(case.summary)
            journal = read_soak_journal(path, identity)
            admit_soak_summary(summary, request, identity, journal)
            admit_soak_run(tmp_path, case.run, request, identity)
        return
    summary = SoakSummary.model_validate(case.summary)
    journal = read_soak_journal(path, identity)
    admit_soak_summary(summary, request, identity, journal)
    admitted_summary, admitted_journal = admit_soak_run(tmp_path, case.run, request, identity)
    assert admitted_summary == summary and admitted_journal == journal
    assert len(journal.rows) == summary.recorded_iterations
    assert journal.byte_count == summary.journal_bytes
    assert (
        sum(row.started_unix_ms is not None for row in journal.rows) == summary.attempted_iterations
    )
