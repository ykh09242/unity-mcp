"""Profile selection must freeze the exact required native verification scope."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Literal, assert_never

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_play_scenario_player_e2e import initialize
import run_play_scenario_player_e2e as runner
from player_e2e_artifacts import write_json
from player_e2e_profiles import (
    ProfileError,
    get_profile,
    require_profile,
    require_case_names,
    validate_execution_profile,
)
from player_e2e_soak import CASES, _check_growth


def test_initializer_records_the_selected_profile_before_any_launch(tmp_path):
    output = tmp_path / "quick"

    session_id = initialize(output, profile_name="quick")

    start = json.loads((output / "session-start.json").read_text(encoding="utf-8"))
    assert start["session_id"] == session_id
    assert start["profile"]["name"] == "quick"
    assert "ordinary" in start["profile"]["required_cases"]
    assert "soak-1000" not in start["profile"]["required_cases"]
    assert start["profile"]["max_execution_seconds"] > 0


def test_long_profile_adds_a_bounded_thousand_iteration_native_body():
    quick, standard, long = (get_profile(name) for name in ("quick", "standard", "long"))

    assert set(quick.cases) < set(standard.cases) < set(long.cases)
    assert "soak-100" in standard.cases and "soak-1000" not in standard.cases
    assert set(long.cases) - set(standard.cases) == {"soak-1000"}
    assert CASES["soak-1000"][0] == 1000
    assert 0 < CASES["soak-1000"][3] < long.max_execution_seconds
    assert (
        quick.artifact_retention_days
        < standard.artifact_retention_days
        < long.artifact_retention_days
    )


def test_unknown_profile_is_rejected_before_creating_evidence(tmp_path):
    output = tmp_path / "unknown"

    with pytest.raises(ProfileError):
        initialize(output, "unknown")

    assert not output.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "long"),
        ("max_execution_seconds", True),
        ("max_execution_seconds", 600.0),
        ("required_cases", ["success"]),
        ("manifest_sha256", "0" * 64),
        ("artifact_retention_days", 90),
    ],
)
def test_recorded_scope_cannot_replace_the_external_requirement(field, value):
    profile = get_profile("quick")
    record = profile.evidence()
    record[field] = value

    with pytest.raises(ProfileError):
        require_profile({"profile": record}, profile)


@pytest.fixture
def execution(tmp_path):
    profile = get_profile("quick")
    session = {"session_id": "a" * 32, "started_unix_ms": 1000, "profile": profile.evidence()}
    results = {
        "session_id": session["session_id"],
        "profile": profile.evidence(),
        "execution_started_unix_ms": 1100,
        "execution_finished_unix_ms": 2100,
        "execution_elapsed_seconds": 1.0,
        "failures": [],
    }
    write_json(tmp_path / "session-start.json", session)
    write_json(tmp_path / "results.json", results)
    return tmp_path, session, results, profile


def test_valid_execution_has_a_bounded_receipt_window(execution):
    root, session, _, profile = execution

    window = validate_execution_profile(root, session, profile)

    assert (window.started_unix_ms, window.finished_unix_ms) == (1100, 2100)


@pytest.mark.parametrize(
    "field,value",
    [
        ("failures", [{"case": "success", "error": "failed"}]),
        ("failures", None),
        ("execution_elapsed_seconds", True),
        ("execution_elapsed_seconds", -1),
        ("execution_elapsed_seconds", 601),
        ("execution_started_unix_ms", 999),
        ("execution_finished_unix_ms", 1099),
        ("session_id", "b" * 32),
    ],
)
def test_failed_expired_or_foreign_execution_is_never_complete(execution, field, value):
    root, session, results, profile = execution
    results[field] = value
    (root / "results.json").write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(ProfileError):
        validate_execution_profile(root, session, profile)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_profile"])
def test_case_directory_must_match_all_independently_required_bodies(
    tmp_path, mutation: Literal["missing", "extra", "wrong_profile"]
):
    quick = get_profile("quick")
    for case in quick.cases:
        (tmp_path / case).mkdir()
    match mutation:
        case "missing":
            (tmp_path / quick.cases[-1]).rmdir()
        case "extra":
            (tmp_path / "unplanned").mkdir()
        case "wrong_profile":
            pass
        case unreachable:
            assert_never(unreachable)
    expected = get_profile("long") if mutation == "wrong_profile" else quick

    with pytest.raises(ProfileError):
        require_case_names(tmp_path, expected)


@pytest.mark.parametrize("mutation", [None, "missing_release", "wrong_pid", "invented_arrays"])
def test_growth_checker_requires_actual_bounded_retention_and_release(
    tmp_path, mutation: Literal["missing_release", "wrong_pid", "invented_arrays"] | None
):
    rows = [
        {
            "process_id": 123,
            "iteration": index,
            "stage": "retained",
            "retained_arrays": index,
            "retained_bytes": index * 1048576,
            "allocation_witness": index * (index + 1) // 2,
            "allocation_limit": 30,
            "allocation_bytes": 1048576,
        }
        for index in range(1, 31)
    ]
    rows.append(
        {
            "process_id": 123,
            "iteration": 30,
            "stage": "released",
            "retained_arrays": 0,
            "retained_bytes": 0,
            "allocation_witness": 0,
        }
    )
    match mutation:
        case "missing_release":
            rows.pop()
        case "wrong_pid":
            rows[5]["process_id"] = 999
        case "invented_arrays":
            rows[5]["retained_arrays"] = 0
        case None:
            pass
        case unreachable:
            assert_never(unreachable)
    (tmp_path / "fixture-memory-growth.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    if mutation is None:
        _check_growth(tmp_path, 123, 30)
    else:
        with pytest.raises(ValueError):
            _check_growth(tmp_path, 123, 30)


@pytest.mark.parametrize("failure", [None, "case", "budget", "deadline", "budget_deadline"])
def test_profile_scheduler_records_all_bodies_and_budget_outcomes(
    tmp_path, monkeypatch, failure: Literal["case", "budget", "deadline", "budget_deadline"] | None
):
    """Replace only native launch boundaries; exercise actual scope, deadline and result writes."""
    profile = get_profile("quick")
    args = SimpleNamespace(output=tmp_path, session_id="a" * 32)
    clock = [1000.0]
    bodies = []
    budget_calls = []

    def body(_args, case, _bundle, _builds, remaining):
        assert remaining > 0
        bodies.append(case)
        clock[0] += profile.max_execution_seconds if failure == "deadline" else 1
        if failure == "case" and case == "listener-error":
            raise ProfileError("Actual body evidence was rejected")

    def budget(root, session_id, *, deadline):
        assert root == tmp_path and session_id == args.session_id
        assert deadline == 1000 + profile.max_execution_seconds
        budget_calls.append(session_id)
        clock[0] += profile.max_execution_seconds if failure == "budget_deadline" else 3
        if failure == "budget":
            raise ProfileError("Required budget outcome missing")

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "run_case", body)
    monkeypatch.setattr(runner, "run_budget_checks", budget, raising=False)

    succeeded = runner.run_profile(
        args, profile, {}, (tmp_path / "scenario", tmp_path / "ordinary")
    )

    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert succeeded is (failure is None)
    assert results["session_id"] == args.session_id
    assert results["profile"] == profile.evidence()
    assert bool(results["failures"]) is (failure is not None)
    assert bodies == list(profile.cases[:1] if failure == "deadline" else profile.cases)
    assert len(budget_calls) == (0 if failure in {"case", "deadline"} else 1)
    if failure is None:
        assert results["execution_elapsed_seconds"] == len(profile.cases) + 3
