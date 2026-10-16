"""Whole saved histories remain diagnostic evidence after report eviction."""

import json
from hashlib import sha256

import pytest

from .play_scenario_soak_compare_fixtures import _evidence
from .play_scenario_history_fixtures import _session, _write, offline_only
from cli.utils.play_scenario_history import analyze_history


def test_session_counts_evicted_success_and_preserves_failure_trend(tmp_path):
    # Given a complete journal with every child directory already evicted.
    directory = _session(tmp_path / "session")
    # When its parent is analyzed offline.
    result = analyze_history((directory / "session.json",))
    # Then journal failures, first iteration and process streak remain visible.
    group = result.groups[0]
    assert result.status == "analyzed"
    assert group.counts["passed"] == 1 and group.counts["failed"] == 2
    assert group.first_failure_iteration == 2
    assert group.longest_failure_streak == group.trailing_failure_streak == 2
    assert group.failure_frequencies == {"ASSERT_FAILED": 2}
    assert group.trend["failure_rate_delta"] == 1
    assert not group.compatible_history
    assert len(group.outcomes) == 3


@pytest.mark.parametrize(
    "damage",
    ["duplicate", "torn", "counter", "identity", "overlap", "escape", "infinity", "parent_success"],
)
def test_invalid_session_history_remains_unavailable(tmp_path, damage):
    # Given a formerly complete history with one forged or incomplete evidence boundary.
    directory = _session(tmp_path / "session")
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "duplicate":
        rows[1] = rows[0]
    elif damage == "identity":
        rows[1]["session_id"] = "f" * 32
    elif damage == "overlap":
        rows[1]["process_started_unix_ms"] = 1000
    elif damage == "escape":
        rows[1]["artifact_directory"] = "../foreign"
    elif damage == "infinity":
        rows[1]["extra"] = float("inf")
    elif damage in {"counter", "parent_success"}:
        parent = json.loads((directory / "session.json").read_text())
        if damage == "counter":
            parent["iteration_counts"]["fresh-process"]["passed"] += 1
        else:
            parent["status"] = "succeeded"
        _write(directory / "session.json", parent)
    encoded = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(encoded[:-1] if damage == "torn" else encoded, encoding="utf-8")
    # When the explicitly selected parent is analyzed.
    result = analyze_history((directory / "session.json",))
    # Then evidence is unavailable rather than counted as a successful retained sample.
    assert result.status == "incomplete"
    assert result.groups == [] and len(result.unsupported_inputs) == 1


def test_soak_failed_parent_preserves_all_iteration_outcomes(tmp_path):
    # Given a continuing failed soak retaining only its first failure report.
    directory = _evidence(tmp_path / "soak", [10, 20, 30])
    rows = [json.loads(line) for line in (directory / "iterations.jsonl").read_text().splitlines()]
    rows[0].update(status="failed", failure_code="ASSERT_FAILED", error="Assertion failed")
    encoded = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    (directory / "iterations.jsonl").write_bytes(encoded)
    summary = json.loads((directory / "soak.json").read_text())
    summary.update(
        status="failed",
        passed_iterations=2,
        failed_iterations=1,
        first_failure_iteration=1,
        selected_iteration=1,
        failure_policy="continue",
        journal_bytes=len(encoded),
        journal_sha256=sha256(encoded).hexdigest(),
        retained_report_files=["iteration-0001.json"],
    )
    request = json.loads((directory / "request.json").read_text())
    request["soak"]["failure_policy"] = "continue"
    request_bytes = json.dumps(request).encode()
    (directory / "request.json").write_bytes(request_bytes)
    report = json.loads((directory / "iteration-0003.json").read_text())
    report.update(
        status="failed",
        exit_code=1,
        started_unix_ms=rows[0]["started_unix_ms"],
        finished_unix_ms=rows[0]["finished_unix_ms"],
        metrics=[dict(rows[0]["metrics"][0], iteration=1)],
    )
    report["iteration_results"][0].update(
        status="failed",
        started_unix_ms=rows[0]["started_unix_ms"],
        finished_unix_ms=rows[0]["finished_unix_ms"],
    )
    _write(directory / "iteration-0001.json", report)
    _write(directory / "run.json", dict(report, soak=summary))
    _write(directory / "soak.json", summary)
    process = json.loads((directory / "process.json").read_text())
    process.update(actual_exit_code=1, request_sha256=sha256(request_bytes).hexdigest())
    _write(directory / "process.json", process)
    client = json.loads((directory / "client-soak.json").read_text())
    client.update(
        status="failed",
        passed_iterations=2,
        failed_iterations=1,
        journal_bytes=len(encoded),
        journal_sha256=sha256(encoded).hexdigest(),
    )
    _write(directory / "client-soak.json", client)
    # When the whole explicit soak parent is analyzed.
    result = analyze_history((directory / "soak.json",))
    # Then all outcomes remain, and later successful rows do not erase the first failure.
    assert result.status == "analyzed", result.unsupported_inputs
    group = result.groups[0]
    assert group.counts["passed"] == 2 and group.counts["failed"] == 1
    assert group.parent_status == "failed" and group.first_failure_iteration == 1
    assert group.failure_rate == pytest.approx(1 / 3)
    assert [row.status for row in group.outcomes] == ["failed", "succeeded", "succeeded"]


def test_duplicate_history_alias_does_not_double_count(tmp_path):
    # Given two explicit aliases of the same complete soak history.
    directory = _evidence(tmp_path / "soak", [10, 20])
    # When both aliases are analyzed together.
    result = analyze_history((directory / "soak.json", directory / "client-soak.json"))
    # Then the original history counts once and the duplicate is explicit unavailable input.
    assert result.status == "incomplete" and len(result.groups) == 1
    assert result.groups[0].counts["passed"] == 2
    assert len(result.unsupported_inputs) == 1


def test_unavailable_session_child_remains_unknown_with_original_error(tmp_path):
    # Given a durable infrastructure outcome with no native iteration evidence.
    directory = _session(tmp_path / "session", ("passed", "failed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = rows[1]
    row.update(
        status="infrastructure_error",
        native_report_available=False,
        native_report_sha256=None,
        iteration_results_source="unavailable",
        failure=None,
        client_error="native_evidence_unavailable",
    )
    row["iteration_counts"].update(executed=0, failed=0, unknown=1)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in rows), encoding="utf-8")
    parent = json.loads((directory / "session.json").read_text())
    parent["iteration_counts"]["fresh-process"].update(executed=1, failed=0, unknown=1)
    parent["completed_iterations"]["fresh-process"] = 1
    parent["first_failure"] = row
    _write(directory / "session.json", parent)
    # When the complete journal with unavailable native evidence is analyzed.
    result = analyze_history((directory / "session.json",))
    # Then unknown evidence and infrastructure errors stay distinct from scenario failure.
    assert result.status == "incomplete" and result.unsupported_inputs == []
    group = result.groups[0]
    assert group.counts["passed"] == group.counts["unknown"] == 1
    assert group.counts["failed"] == 0
    assert group.outcomes[1].status == "infrastructure_error"
    assert group.outcomes[1].iteration_results_source == "unavailable"


def test_batch_counters_do_not_invent_exact_failure_iteration_or_iteration_streak(tmp_path):
    # Given one failed three-iteration batch whose compact failure lacks a local index.
    directory = _session(tmp_path / "session", ("failed",))
    header = json.loads((directory / "session-start.json").read_text())
    header.update(
        mode="shared-batches", requested_iterations=3, iterations_per_process={"shared-batches": 3}
    )
    _write(directory / "session-start.json", header)
    admission = json.loads((directory / "admissions.jsonl").read_text())
    admission.update(mode="shared-batches", repeat_count=3)
    (directory / "admissions.jsonl").write_text(json.dumps(admission) + "\n", encoding="utf-8")
    row = json.loads((directory / "outcomes.jsonl").read_text())
    row.update(mode="shared-batches", repeat_count=3, failure=None)
    row["iteration_counts"].update(planned=3, executed=3, passed=2)
    (directory / "outcomes.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    parent = json.loads((directory / "session.json").read_text())
    parent.update(
        header,
        iteration_counts={"shared-batches": row["iteration_counts"]},
        scheduled_iterations={"shared-batches": 3},
        completed_iterations={"shared-batches": 3},
        first_failure=row,
    )
    _write(directory / "session.json", parent)
    # When the journal aggregate is analyzed.
    result = analyze_history((directory / "session.json",))
    # Then only observable batch order is exported and the exact first failure stays unknown.
    assert result.status == "analyzed", result.unsupported_inputs
    group = result.groups[0]
    assert group.first_failure_iteration is None
    assert group.streak_unit == "process_outcome" and group.longest_failure_streak == 1
    assert group.counts["passed"] == 2 and group.counts["failed"] == 1


def test_session_build_change_partitions_counts_without_intermittency(tmp_path):
    # Given pass/fail process outcomes attributed to different builds.
    directory = _session(tmp_path / "session", ("passed", "failed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["build_id"] = "f" * 32
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    parent = json.loads((directory / "session.json").read_text())
    parent["first_failure"] = rows[1]
    _write(directory / "session.json", parent)
    # When the whole explicit session is analyzed.
    result = analyze_history((directory / "session.json",))
    # Then different build identities remain separate diagnostic groups.
    assert result.status == "analyzed" and len(result.groups) == 2
    assert all(not group.intermittent for group in result.groups)
    assert sum(group.counts["passed"] for group in result.groups) == 1
    assert sum(group.counts["failed"] for group in result.groups) == 1


def test_session_environment_survives_report_eviction(tmp_path):
    from cli.utils.play_scenario_player_session import _append_record, _compact_reproduction

    directory = _session(tmp_path / "session", ("passed", "passed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    environments = []
    for row, operating_system, backend in zip(rows, ("Windows 11", "Linux"), ("mono", "il2cpp")):
        environment = dict(
            schema_version=1,
            operating_system=operating_system,
            platform="WindowsPlayer" if backend == "mono" else "LinuxPlayer",
            architecture="x64",
            scripting_backend=backend,
            build_mode="release",
            runtime_version="runtime-1",
        )
        environments.append(environment)
        child = directory / "runs" / row["job_id"]
        child.mkdir(parents=True)
        reproduction = dict(environment=environment, unity_version="6000.0", package_version="1.0")
        _write(child / "run.json", dict(reproduction=reproduction, full_details="retained report"))
        row.update(_compact_reproduction(reproduction))
        assert json.loads((child / "run.json").read_text())["reproduction"] == reproduction
        (child / "run.json").unlink()
        child.rmdir()
    with path.open("wb") as stream:
        for row in rows:
            _append_record(stream, row)
    result = analyze_history((directory / "session.json",))
    assert result.status == "analyzed", result.unsupported_inputs
    assert len(result.groups) == 2
    assert [group.compatibility["environment"] for group in result.groups] == environments
    assert all(group.compatible_history for group in result.groups)
    assert all(group.counts["passed"] == 1 for group in result.groups)


@pytest.mark.parametrize("damage", ["oversized", "unicode", "nested", "unknown_fields"])
def test_optional_session_environment_cannot_destroy_later_outcomes(tmp_path, damage):
    from cli.utils.play_scenario_player_session import (
        _append_record,
        _bound_record_environment,
        _compact_reproduction,
    )

    directory = _session(tmp_path / "session", ("passed", "passed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    environment = dict(
        schema_version=1,
        operating_system="Windows",
        platform="WindowsPlayer",
        architecture="x64",
        scripting_backend="mono",
        build_mode="release",
        runtime_version="v1",
    )
    if damage == "oversized":
        environment["operating_system"] = "x" * 10000
    elif damage == "unicode":
        environment["operating_system"] = "한" * 256
        rows[0]["client_error"] = "x" * 2048
    elif damage == "nested":
        environment["operating_system"] = {"path": "private"}
    else:
        environment["private_path"] = "x" * 10000
    reproduction = dict(environment=environment, unity_version="6000.0", package_version="1.0")
    rows[0].update(_compact_reproduction(reproduction))
    _bound_record_environment(rows[0])
    if damage in {"oversized", "nested", "unicode"}:
        assert rows[0].get("environment") is None
    else:
        assert rows[0]["environment"]["operating_system"] == "Windows"
    with path.open("wb") as stream:
        for row in rows:
            _append_record(stream, row)
    assert all(len(line) <= 4096 for line in path.read_bytes().splitlines(keepends=True))
    assert b"private_path" not in path.read_bytes()
    result = analyze_history((directory / "session.json",))
    assert result.status == "analyzed", result.unsupported_inputs
    assert sum(group.counts["passed"] for group in result.groups) == 2
    legacy = next(group for group in result.groups if group.outcomes[-1].sequence == 2)
    assert legacy.compatibility["environment"] is None
    assert not legacy.compatible_history


def test_near_cap_legacy_outcome_keeps_following_record_during_recovery(tmp_path):
    from cli.utils.play_scenario_player_session import _append_record
    from cli.utils.play_scenario_session_recovery import _Outcome, recover_player_session

    directory = _session(tmp_path / "session", ("passed", "passed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    # Include the old model's default fields, then fill a valid bounded failure preview
    # so the pre-change normalized row is exactly the accepted 4096-byte boundary.
    row = _Outcome.model_validate(rows[0]).model_dump(mode="json")
    for key in ("environment", "unity_version", "package_version"):
        row.pop(key)
    row["artifact_directory"] = None
    row["client_error"] = "x" * 2048
    row["build_source_revision"] = "x" * 128
    row["payload_verification"] = "x" * 128
    row["failure"] = dict(code="X" * 128, stage="main", message="", iteration=1, step_index=0)
    encoded_size = len(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode()) + 1
    assert 0 <= 4096 - encoded_size <= 512
    row["failure"]["message"] = "x" * (4096 - encoded_size)
    rows[0] = row
    with path.open("wb") as stream:
        for entry in rows:
            _append_record(stream, entry)
    assert len(path.read_bytes().splitlines(keepends=True)[0]) == 4096
    result = analyze_history((directory / "session.json",))
    assert result.status == "analyzed", result.unsupported_inputs
    assert sum(group.counts["passed"] for group in result.groups) == 2
    summary, recovered, _code = recover_player_session(directory, tmp_path / "recovered")
    assert summary["outcomes_recorded"] == 2
    assert len((recovered / "outcomes.jsonl").read_bytes().splitlines()) == 2
    assert all(
        len(line) <= 4096
        for line in (recovered / "outcomes.jsonl").read_bytes().splitlines(keepends=True)
    )


@pytest.mark.parametrize(
    "environment", [{"schema_version": 99}, {"schema_version": 1, "platform": []}]
)
def test_malformed_optional_environment_on_read_keeps_following_outcome(tmp_path, environment):
    from cli.utils.play_scenario_player_session import _append_record
    from cli.utils.play_scenario_session_recovery import recover_player_session

    directory = _session(tmp_path / "session", ("passed", "passed"))
    path = directory / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0].update(environment=environment, unity_version={"untrusted": "value"})
    with path.open("wb") as stream:
        for row in rows:
            _append_record(stream, row)
    result = analyze_history((directory / "session.json",))
    assert result.status == "analyzed", result.unsupported_inputs
    assert result.groups[0].counts["passed"] == 2
    assert result.groups[0].compatibility["environment"] is None
    summary, recovered, _code = recover_player_session(directory, tmp_path / "recovered")
    assert summary["outcomes_recorded"] == 2
    assert len((recovered / "outcomes.jsonl").read_bytes().splitlines()) == 2


@pytest.mark.parametrize(
    "damage", ["journal_hash", "torn", "duplicate_key", "infinity", "forced_exit", "client_counts"]
)
def test_corrupt_soak_evidence_is_unavailable(tmp_path, damage):
    # Given a complete history with corrupted native, client or process evidence.
    directory = _evidence(tmp_path / "soak", [10, 20, 30])
    path = directory / "iterations.jsonl"
    encoded = path.read_bytes()
    if damage == "torn":
        path.write_bytes(encoded[:-1])
    elif damage == "journal_hash":
        path.write_bytes(encoded.replace(b'"succeeded"', b'"failed"', 1))
    elif damage in {"duplicate_key", "infinity"}:
        lines = encoded.splitlines()
        lines[0] = lines[0][:-1] + (
            b',"iteration":1}' if damage == "duplicate_key" else b',"extra":1e999}'
        )
        path.write_bytes(b"\n".join(lines) + b"\n")
    elif damage == "forced_exit":
        raw = json.loads((directory / "process.json").read_text())
        raw["forced_termination"] = True
        _write(directory / "process.json", raw)
    else:
        raw = json.loads((directory / "client-soak.json").read_text())
        raw["passed_iterations"] = 1
        _write(directory / "client-soak.json", raw)
    # When the explicit summary is analyzed.
    result = analyze_history((directory / "soak.json",))
    # Then corruption never turns a retained success report into whole-history success.
    assert result.status == "incomplete" and result.groups == []
    assert len(result.unsupported_inputs) == 1
