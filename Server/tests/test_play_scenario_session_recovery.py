# noqa: SIZE_OK - Cohesive recovery boundary coverage includes CLI, damaged evidence and parent death.
"""Recovery uses bounded persisted evidence without replaying a Player."""

import json
import os
from pathlib import Path
import pytest
from xml.etree import ElementTree

from click.testing import CliRunner

from cli.main import cli
from cli.utils.play_scenario_player import PlayerLaunchError
from cli.utils.play_scenario_session_recovery import recover_player_session


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _session(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    session_id = "a" * 32
    mode = "shared-batches"
    header = {
        "schema_version": 2,
        "session_id": session_id,
        "status": "running",
        "mode": mode,
        "requested_iterations": 6,
        "iterations_per_process": {mode: 3},
        "started_unix_ms": 1000,
    }
    _write(source / "session-start.json", header)
    _write(source / "session.json", header)
    admissions = [
        {
            "schema_version": 1,
            "session_id": session_id,
            "sequence": index,
            "mode": mode,
            "job_id": str(index) * 32,
            "repeat_count": 3,
        }
        for index in (1, 2)
    ]
    (source / "admissions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in admissions), encoding="utf-8"
    )
    counts = dict(
        planned=3,
        executed=2,
        pending=0,
        running=0,
        passed=1,
        failed=1,
        timed_out=0,
        cancelled=0,
        skipped=1,
        unknown=0,
    )
    row = dict(
        admissions[0],
        schema_version=2,
        status="failed",
        exit_code=1,
        native_report_available=True,
        native_report_sha256="b" * 64,
        artifact_directory="runs/" + "1" * 32,
        process_id=123,
        process_ended=True,
        actual_exit_code=1,
        forced_termination=False,
        process_started_unix_ms=1000,
        process_finished_unix_ms=1200,
        started_unix_ms=1001,
        finished_unix_ms=1199,
        request_sha256="c" * 64,
        client_error=None,
        iteration_counts=counts,
        iteration_results_source="native",
        failure={
            "code": "state_provider_error",
            "stage": "main",
            "iteration": 2,
            "step_index": 1,
            "message": "Fixture failure",
        },
    )
    (source / "outcomes.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return source


def _recover(source, output):
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-session-recover",
            str(source),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 1, result.output
    response = json.loads(result.output)
    assert response["success"] is False
    return response["data"]


def test_recovery_keeps_unfinalized_admission_unknown_and_source_unchanged(tmp_path):
    # Given a finalized failed batch and an admitted child without a final outcome.
    source = _session(tmp_path)
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    output = tmp_path / "recovered"
    # When the actual CLI recovers a separate snapshot.
    report = _recover(source, output)
    # Then only proven executions are counted and automation remains incomplete.
    counts = report["iteration_counts"]["shared-batches"]
    assert counts == dict(
        planned=6,
        executed=2,
        pending=0,
        running=0,
        passed=1,
        failed=1,
        timed_out=0,
        cancelled=0,
        skipped=1,
        unknown=3,
    )
    assert report["recovered"] is True
    assert report["arms_complete"] == {"shared-batches": False}
    assert report["completed_iterations"] == {"shared-batches": 2}
    recovered = output / report["recovery_id"]
    junit = ElementTree.parse(recovered / "junit.xml").getroot()
    assert junit.find("./testcase[@name='session_completion']/error") is not None
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before


def test_recovery_uses_valid_journal_prefix_after_torn_tail_and_invalid_checkpoint(tmp_path):
    # Given a torn final journal line and a damaged replaceable checkpoint.
    source = _session(tmp_path)
    with (source / "outcomes.jsonl").open("ab") as stream:
        stream.write(b'{"sequence":2')
    (source / "session.json").write_bytes(b"incomplete")
    # When recovery reads the immutable header and valid evidence prefix.
    report = _recover(source, tmp_path / "recovered")
    # Then the completed child survives while the unresolved child remains unknown.
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 3
    assert report["recovery_issues"]


def _first_outcome(source):
    return json.loads((source / "outcomes.jsonl").read_text().splitlines()[0])


def _journal(source, name, rows):
    (source / (name + ".jsonl")).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_intact_admissions_mark_proven_unadmitted_remaining_slots_skipped(tmp_path):
    source = _session(tmp_path)
    first = json.loads((source / "admissions.jsonl").read_text().splitlines()[0])
    _journal(source, "admissions", [first])
    report = _recover(source, tmp_path / "output")
    counts = report["iteration_counts"]["shared-batches"]
    assert counts["skipped"] == 4
    assert counts["unknown"] == 0
    assert counts["planned"] == 6 and counts["executed"] == 2
    assert report["first_failure"]["artifact_directory"] is None


@pytest.mark.parametrize("journal", ["admissions", "outcomes"])
def test_missing_journal_is_explicit_and_never_infers_complete_success(tmp_path, journal):
    source = _session(tmp_path)
    (source / (journal + ".jsonl")).unlink()
    report = _recover(source, tmp_path / "output")
    counts = report["iteration_counts"]["shared-batches"]
    assert counts["unknown"] == 6 and counts["executed"] == 0
    assert journal + "_missing" in report["recovery_issues"]
    if journal == "admissions":
        assert report["first_failure"] is None
    else:
        assert report["first_failure"]["client_error"] == "recovery_outcome_unavailable"


@pytest.mark.parametrize("journal", ["admissions", "outcomes"])
def test_duplicate_journal_identity_preserves_only_trustworthy_prefix(tmp_path, journal):
    source = _session(tmp_path)
    first = json.loads((source / (journal + ".jsonl")).read_text().splitlines()[0])
    _journal(source, journal, [first, first])
    report = _recover(source, tmp_path / "output")
    assert report["outcomes_recorded"] == (2 if journal == "outcomes" else 1)
    assert report["first_failure"]["job_id"] == "1" * 32
    assert journal + "_duplicate_or_sequence" in report["recovery_issues"]


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("session_id", "f" * 32, "identity_mismatch"),
        ("mode", "fresh-process", "identity_mismatch"),
        ("repeat_count", 2, "plan_mismatch"),
        ("sequence", 2, "duplicate_or_sequence"),
        ("repeat_count", True, "invalid"),
    ],
)
def test_admission_disagreement_or_invalid_type_leaves_plan_unknown(tmp_path, field, value, issue):
    source = _session(tmp_path)
    first = json.loads((source / "admissions.jsonl").read_text().splitlines()[0])
    first[field] = value
    _journal(source, "admissions", [first])
    report = _recover(source, tmp_path / "output")
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 6
    assert "admissions_" + issue in report["recovery_issues"]


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("session_id", "f" * 32, "identity_mismatch"),
        ("mode", "fresh-process", "identity_mismatch"),
        ("repeat_count", 2, "invalid"),
        ("job_id", "f" * 32, "admission_mismatch"),
        ("sequence", 2, "duplicate_or_sequence"),
        ("exit_code", False, "invalid"),
        ("process_ended", 1, "invalid"),
    ],
)
def test_outcome_disagreement_never_rewrites_admission_identity(tmp_path, field, value, issue):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row[field] = value
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 6
    assert report["outcomes_recorded"] == 2
    assert report["children_finalized"] == 0
    assert report["first_failure"]["client_error"] == "recovery_outcome_unavailable"
    assert "outcomes_" + issue in report["recovery_issues"]


@pytest.mark.parametrize(("field", "value"), [("executed", 3), ("passed", True), ("unknown", -1)])
def test_invalid_counter_partition_or_type_rejects_outcome(tmp_path, field, value):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["iteration_counts"][field] = value
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["iteration_counts"]["shared-batches"]["executed"] == 0
    assert "outcomes_invalid" in report["recovery_issues"]


@pytest.mark.parametrize("tail", [b'{"sequence":2', b"x" * 4097 + b"\n", b"not-json\n"])
def test_damaged_outcome_tail_preserves_finalized_failure(tmp_path, tail):
    source = _session(tmp_path)
    with (source / "outcomes.jsonl").open("ab") as stream:
        stream.write(tail)
    report = _recover(source, tmp_path / "output")
    assert report["outcomes_recorded"] == 2
    assert report["children_finalized"] == 1
    assert report["first_failure"]["status"] == "failed"
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert report["recovery_issues"]


def test_legacy_checkpoint_and_outcomes_never_trust_old_completed_alias(tmp_path):
    source = _session(tmp_path)
    header = json.loads((source / "session-start.json").read_text())
    header.update(schema_version=1, completed_iterations={"shared-batches": 999})
    _write(source / "session.json", header)
    (source / "session-start.json").unlink()
    (source / "admissions.jsonl").unlink()
    row = _first_outcome(source)
    for field in (
        "schema_version",
        "session_id",
        "sequence",
        "iteration_counts",
        "iteration_results_source",
    ):
        row.pop(field)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["completed_iterations"] == {"shared-batches": 0}
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 6
    assert report["scheduled_iterations"] == {"shared-batches": 3}
    assert report["first_failure"]["status"] == "failed"


def test_all_children_passed_snapshot_still_has_completion_error(tmp_path):
    source = _session(tmp_path)
    header = json.loads((source / "session-start.json").read_text())
    header["requested_iterations"] = 3
    _write(source / "session-start.json", header)
    first = json.loads((source / "admissions.jsonl").read_text().splitlines()[0])
    _journal(source, "admissions", [first])
    row = _first_outcome(source)
    row.update(status="succeeded", exit_code=0, actual_exit_code=0, failure=None)
    row["iteration_counts"].update(executed=3, passed=3, failed=0, skipped=0)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["iteration_counts"]["shared-batches"]["passed"] == 3
    assert report["status"] == "interrupted" and report["arms_complete"] == {
        "shared-batches": False
    }
    root = ElementTree.parse(tmp_path / "output" / report["recovery_id"] / "junit.xml").getroot()
    assert root.attrib["errors"] == "1"
    assert root.find("./testcase[@name='session_completion']/error") is not None


def test_invalid_immutable_header_cannot_fall_back_to_replaceable_checkpoint(tmp_path):
    source = _session(tmp_path)
    (source / "session-start.json").write_bytes(b"broken")
    with pytest.raises(PlayerLaunchError, match="header is invalid"):
        recover_player_session(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("relative", [".", "child"])
def test_output_inside_source_is_rejected_before_write(tmp_path, relative):
    source = _session(tmp_path)
    with pytest.raises(PlayerLaunchError, match="separate"):
        recover_player_session(source, source / relative)


def test_output_ancestor_of_source_is_rejected_before_write(tmp_path):
    source = _session(tmp_path)
    with pytest.raises(PlayerLaunchError, match="separate"):
        recover_player_session(source, tmp_path)


def test_protected_source_path_is_rejected_before_any_read(tmp_path):
    protected = tmp_path / "Assets" / "Resources" / "GameData"
    with pytest.raises(PlayerLaunchError, match="protected"):
        recover_player_session(protected, tmp_path / "output")


def test_recovery_reads_source_files_only_and_does_not_follow_native_artifact(
    tmp_path, monkeypatch
):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["artifact_directory"] = str(tmp_path / "outside-native")
    _journal(source, "outcomes", [row])
    real_open = Path.open
    reads = []

    def read_only(path, mode="r", *args, **kwargs):
        if path.is_relative_to(source):
            assert mode == "rb"
            reads.append(path.name)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_only)
    report = _recover(source, tmp_path / "output")
    assert set(reads) == {"session-start.json", "admissions.jsonl", "outcomes.jsonl"}
    assert report["first_failure"]["artifact_directory"] is None


def test_linked_source_ancestor_is_rejected(tmp_path):
    source = _session(tmp_path)
    link = tmp_path / "link"
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable on this host")
    with pytest.raises(PlayerLaunchError, match="links or reparse"):
        recover_player_session(link, tmp_path / "output")


def test_real_parent_process_death_keeps_write_ahead_admission_without_launching_child(tmp_path):
    import subprocess
    import sys

    build = tmp_path / "build"
    build.mkdir()
    sessions = tmp_path / "sessions"
    script = (
        "import os,sys\n"
        "from pathlib import Path\n"
        "from cli.utils import play_scenario_player_session as session\n"
        "def die_before_launch(_options): os._exit(23)\n"
        "session.run_player=die_before_launch\n"
        "session.run_player_session(session.PlayerSessionOptions("
        "build_directory=Path(sys.argv[1]),output_directory=Path(sys.argv[2]),"
        "iterations=6,batch_size=3,interval_seconds=0))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(build), str(sessions)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        check=False,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 23, result.stderr.decode("utf-8", errors="replace")
    sources = list(sessions.iterdir())
    assert len(sources) == 1
    source = sources[0]
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    report = _recover(source, tmp_path / "output")
    counts = report["iteration_counts"]["shared-batches"]
    assert counts["unknown"] == counts["skipped"] == 3
    assert counts["executed"] == report["children_started"] == report["children_finalized"] == 0
    assert report["scheduled_iterations"] == {"shared-batches": 3}
    assert report["recovery_admissions_complete"] is True
    assert report["active_child"] is None
    assert report["outcomes_recorded"] == 1
    row = report["last_outcomes"][0]
    assert row["client_error"] == "recovery_outcome_unavailable"
    assert row["native_report_available"] is False
    assert row["artifact_directory"] is row["process_id"] is row["actual_exit_code"] is None
    assert {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} == before


def test_legacy_success_is_projected_to_unknown_error_without_inventing_a_pass(tmp_path):
    source = _session(tmp_path)
    header = json.loads((source / "session-start.json").read_text())
    header["schema_version"] = 1
    _write(source / "session.json", header)
    (source / "session-start.json").unlink()
    (source / "admissions.jsonl").unlink()
    row = _first_outcome(source)
    row.update(status="succeeded", exit_code=0, actual_exit_code=0, failure=None)
    for field in (
        "schema_version",
        "session_id",
        "sequence",
        "iteration_counts",
        "iteration_results_source",
    ):
        row.pop(field)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["recovery_admissions_complete"] is False
    assert report["iteration_counts"]["shared-batches"]["passed"] == 0
    recovered_row = report["last_outcomes"][0]
    assert recovered_row["status"] == "infrastructure_error"
    assert recovered_row["original_status"] == "succeeded"
    assert recovered_row["client_error"] == "recovery_legacy_iteration_unknown"
    assert recovered_row["iteration_counts"]["unknown"] == 3


def test_corrupt_admissions_tail_keeps_unscheduled_remainder_unknown(tmp_path):
    source = _session(tmp_path)
    first = json.loads((source / "admissions.jsonl").read_text().splitlines()[0])
    _journal(source, "admissions", [first])
    with (source / "admissions.jsonl").open("ab") as stream:
        stream.write(b'{"sequence":2')
    report = _recover(source, tmp_path / "output")
    assert report["recovery_admissions_complete"] is False
    assert report["scheduled_iterations"] == {"shared-batches": 3}
    counts = report["iteration_counts"]["shared-batches"]
    assert counts["executed"] == 2 and counts["unknown"] == 3 and counts["skipped"] == 1


def test_schema_two_success_requires_passed_iteration_evidence(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row.update(status="succeeded", exit_code=0, actual_exit_code=0, failure=None)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    assert report["iteration_counts"]["shared-batches"]["executed"] == 0
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 6


def test_duplicate_header_keys_are_rejected_before_creating_snapshot(tmp_path):
    source = _session(tmp_path)
    path = source / "session-start.json"
    text = path.read_text().rstrip()
    path.write_text(text[:-1] + ',"requested_iterations":1000}', encoding="utf-8")
    with pytest.raises(PlayerLaunchError, match="duplicate"):
        recover_player_session(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("journal", ["admissions", "outcomes"])
def test_duplicate_journal_keys_make_evidence_unknown(tmp_path, journal):
    source = _session(tmp_path)
    path = source / (journal + ".jsonl")
    first = path.read_text().splitlines()[0]
    path.write_text(first[:-1] + ',"repeat_count":3}\n', encoding="utf-8")
    report = _recover(source, tmp_path / "output")
    assert journal + "_invalid" in report["recovery_issues"]
    assert report["iteration_counts"]["shared-batches"]["passed"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iteration_results_source", "unavailable"),
        ("native_report_available", False),
        ("native_report_sha256", None),
    ],
)
def test_iteration_counters_require_native_report_identity_and_available_source(
    tmp_path, field, value
):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row[field] = value
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    counts = report["iteration_counts"]["shared-batches"]
    assert counts["passed"] == counts["executed"] == 0
    assert counts["unknown"] == 6


def test_new_recovery_directory_never_overwrites_existing_snapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cli.utils import play_scenario_session_recovery as recovery

    source = _session(tmp_path)
    output = tmp_path / "output"
    existing = output / ("d" * 32)
    existing.mkdir(parents=True)
    artifact = existing / "session.json"
    artifact.write_bytes(b"existing snapshot")
    monkeypatch.setattr(recovery, "uuid4", lambda: SimpleNamespace(hex="d" * 32))
    with pytest.raises(FileExistsError):
        recover_player_session(source, output)
    assert artifact.read_bytes() == b"existing snapshot"


def test_nested_duplicate_header_keys_are_rejected(tmp_path):
    source = _session(tmp_path)
    path = source / "session-start.json"
    text = path.read_text()
    text = text.replace('"shared-batches": 3', '"shared-batches":3,"shared-batches":3')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PlayerLaunchError, match="duplicate"):
        recover_player_session(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_nested_duplicate_outcome_keys_preserve_valid_prefix(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row.update(sequence=2, job_id="2" * 32)
    text = json.dumps(row).replace('"passed": 1', '"passed":1,"passed":1')
    with (source / "outcomes.jsonl").open("ab") as stream:
        stream.write((text + "\n").encode("utf-8"))
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 3


def test_oversized_failure_context_is_classified_before_output_projection(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["failure"]["message"] = "x" * 3200
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert any(
        issue in report["recovery_issues"] for issue in ("outcomes_invalid", "outcomes_oversized")
    )
    assert report["iteration_counts"]["shared-batches"]["executed"] == 0
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 6


def test_process_exit_evidence_requires_symmetric_finalization(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["process_ended"] = False
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    assert (
        report["children_finalized"]
        == report["iteration_counts"]["shared-batches"]["executed"]
        == 0
    )


def test_legacy_recovery_rows_normalize_identity_for_editor_import(tmp_path):
    source = _session(tmp_path)
    header = json.loads((source / "session-start.json").read_text())
    header["schema_version"] = 1
    _write(source / "session.json", header)
    (source / "session-start.json").unlink()
    (source / "admissions.jsonl").unlink()
    row = _first_outcome(source)
    for field in (
        "schema_version",
        "session_id",
        "sequence",
        "iteration_counts",
        "iteration_results_source",
    ):
        row.pop(field)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    projected = report["last_outcomes"][0]
    assert projected["schema_version"] == 2
    assert projected["session_id"] == header["session_id"]
    assert projected["sequence"] == 1
    assert projected["iteration_counts"]["unknown"] == 3


def test_valid_bounded_input_that_expands_at_projection_preserves_previous_prefix(tmp_path):
    source = _session(tmp_path)
    original = _first_outcome(source)
    row = {
        key: original[key]
        for key in (
            "schema_version",
            "session_id",
            "sequence",
            "mode",
            "job_id",
            "repeat_count",
            "status",
            "exit_code",
            "native_report_available",
            "native_report_sha256",
            "iteration_counts",
            "iteration_results_source",
        )
    }
    row.update(sequence=2, job_id="2" * 32, client_error="")
    row["failure"] = {
        "code": "x" * 128,
        "message": "x" * 512,
        "stage": "main",
        "iteration": 1,
        "step_index": 1,
    }
    row.update(
        build_source_revision="x" * 128,
        payload_verification="x" * 128,
        definition_hash="b" * 64,
        payload_hash="c" * 64,
        build_id="e" * 32,
    )
    row["client_error"] = "\u00e9" * 512 + "x" * 1024
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    assert len(encoded) <= 4096
    with (source / "outcomes.jsonl").open("ab") as stream:
        stream.write(encoded)
    report = _recover(source, tmp_path / "output")
    assert "outcomes_projection_oversized" in report["recovery_issues"]
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert report["iteration_counts"]["shared-batches"]["unknown"] == 3
    journal = tmp_path / "output" / report["recovery_id"] / "outcomes.jsonl"
    assert all(len(line) <= 4096 for line in journal.read_bytes().splitlines(keepends=True))


@pytest.mark.parametrize(
    ("field", "value"),
    [("stage", "bogus"), ("iteration", 10), ("code", "x" * 129), ("code", " ")],
)
def test_failure_context_matches_editor_import_contract(tmp_path, field, value):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["failure"][field] = value
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    assert report["iteration_counts"]["shared-batches"]["executed"] == 0


@pytest.mark.parametrize("missing", ["code"])
def test_failure_context_requires_code(tmp_path, missing):
    source = _session(tmp_path)
    row = _first_outcome(source)
    del row["failure"][missing]
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]


def test_unavailable_counters_with_available_report_still_require_hash(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["iteration_results_source"] = "unavailable"
    row["native_report_sha256"] = None
    row["iteration_counts"].update(executed=0, passed=0, failed=0, skipped=0, unknown=3)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert "outcomes_invalid" in report["recovery_issues"]
    assert report["children_finalized"] == 0


def test_native_failure_sentinels_remain_valid_for_editor_import(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["failure"].update(stage=None, iteration=0, step_index=-1)
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["first_failure"]["failure"]["iteration"] == 0
    assert report["first_failure"]["failure"]["step_index"] == -1
    assert "stage" not in report["first_failure"]["failure"]


@pytest.mark.parametrize("message", [None, ""])
def test_nullable_or_empty_native_failure_message_remains_compatible(tmp_path, message):
    source = _session(tmp_path)
    row = _first_outcome(source)
    row["failure"]["message"] = message
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert not report["recovery_issues"]


def test_omitted_native_failure_message_remains_compatible(tmp_path):
    source = _session(tmp_path)
    row = _first_outcome(source)
    del row["failure"]["message"]
    _journal(source, "outcomes", [row])
    report = _recover(source, tmp_path / "output")
    assert report["first_failure"]["failure"]["code"] == row["failure"]["code"]
    assert report["iteration_counts"]["shared-batches"]["executed"] == 2
    assert not report["recovery_issues"]
