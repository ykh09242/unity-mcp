"""Retained NUnit shard submissions reconcile against a declared case inventory."""

import json
from pathlib import Path

import pytest
from tests.verification_artifact_fixtures import write_nunit
from unity_mcp_evidence.verification_nunit_shards import merge_shards

RED = 'Game.Tests.Doors.Opens("key,red")'
BLUE = 'Game.Tests.Doors.Opens("key","red")'
LOCKED = "Game.Tests.Doors.RemainsLocked"


def _manifest(path: Path, submissions: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "verification_nunit_shards@1",
                "run_id": "build-12/editor",
                "splits": [
                    {"split_id": "opens", "cases": [RED, BLUE]},
                    {"split_id": "locked", "cases": [LOCKED]},
                ],
                "submissions": submissions,
            }
        ),
        encoding="utf-8",
    )
    return path


def _submission(split_id: str, attempt: int, path: Path) -> dict:
    return {
        "run_id": "build-12/editor",
        "split_id": split_id,
        "attempt": attempt,
        "execution_id": f"{split_id}-{attempt}",
        "xml": path.name,
    }


def test_retries_preserve_full_parameterized_names_and_count_latest_each_case(tmp_path):
    initial = write_nunit(tmp_path / "initial.xml", {RED: "Failed", BLUE: "Passed"})
    retry = write_nunit(tmp_path / "retry.xml", {RED: "Passed"})
    locked = write_nunit(tmp_path / "locked.xml", {LOCKED: "Passed"})
    manifest = _manifest(
        tmp_path / "shards.json",
        [
            _submission("opens", 2, retry),
            _submission("locked", 1, locked),
            _submission("opens", 1, initial),
        ],
    )

    result = merge_shards(manifest)

    assert result["state"] == "passed"
    assert result["coverage"]["passed_cases"] == result["coverage"]["planned_cases"] == 3
    cases = next(split["cases"] for split in result["splits"] if split["split_id"] == "opens")
    assert set(cases) == {RED, BLUE}
    assert [row["result"] for row in cases[RED]["attempts"]] == ["Failed", "Passed"]
    assert [row["execution_id"] for row in cases[RED]["attempts"]] == ["opens-1", "opens-2"]
    assert [row["attempt"] for row in cases[BLUE]["attempts"]] == [1]


def test_missing_split_keeps_planned_case_in_coverage_and_uploads_are_idempotent(tmp_path):
    initial = write_nunit(tmp_path / "initial.xml", {RED: "Passed", BLUE: "Passed"})
    mirror = tmp_path / "mirror.xml"
    mirror.write_bytes(initial.read_bytes())
    from_one = [_submission("opens", 1, initial)]
    manifest = _manifest(tmp_path / "shards.json", from_one)
    single = merge_shards(manifest)
    _manifest(manifest, [*from_one, *from_one, _submission("opens", 1, mirror)])

    repeated = merge_shards(manifest)

    assert repeated == single
    assert repeated["state"] == "incomplete"
    assert repeated["missing_splits"] == ["locked"]
    assert repeated["coverage"]["planned_splits"] == 2
    assert repeated["coverage"]["completed_splits"] == 1
    assert repeated["coverage"]["planned_cases"] == 3
    assert repeated["coverage"]["reported_cases"] == repeated["coverage"]["passed_cases"] == 2
    assert repeated["coverage"]["missing_cases"] == 1


@pytest.mark.parametrize(
    ("variant", "expected_error"),
    [
        ("mismatched_run", "declared run ID"),
        ("wrong_split", "outside its declared split"),
        ("changed_bytes", "Conflicting NUnit artifacts"),
        ("replayed_artifact", "reused for different executions"),
        ("reused_execution", "execution ID was reused"),
        ("path_escape", "stay inside"),
        ("truncated_xml", "Invalid NUnit evidence"),
        ("forbidden_entity", "document type or entities"),
    ],
)
def test_rejects_conflicting_or_unadmitted_shard_evidence(tmp_path, variant, expected_error):
    initial = write_nunit(tmp_path / "initial.xml", {RED: "Passed"})
    changed = write_nunit(tmp_path / "changed.xml", {RED: "Failed"})
    basic = [_submission("opens", 1, initial)]
    if variant == "truncated_xml":
        changed.write_bytes(changed.read_bytes()[:-3])
    if variant == "forbidden_entity":
        changed.write_bytes(b'<!DOCTYPE test-run [<!ENTITY bad "value">]>' + changed.read_bytes())
    trial = {
        "mismatched_run": [{**basic[0], "run_id": "another-build"}],
        "wrong_split": [_submission("locked", 1, initial)],
        "changed_bytes": [*basic, _submission("opens", 1, changed)],
        "replayed_artifact": [*basic, _submission("opens", 2, initial)],
        "reused_execution": [
            *basic,
            {**_submission("opens", 2, changed), "execution_id": "opens-1"},
        ],
        "path_escape": [{**basic[0], "xml": "../initial.xml"}],
        "truncated_xml": [_submission("opens", 1, changed)],
        "forbidden_entity": [_submission("opens", 1, changed)],
    }
    manifest = _manifest(tmp_path / "shards.json", trial[variant])

    with pytest.raises(ValueError, match=expected_error):
        merge_shards(manifest)


def test_missing_retry_artifact_does_not_launder_previous_failure(tmp_path):
    initial = write_nunit(tmp_path / "initial.xml", {RED: "Failed", BLUE: "Passed"})
    locked = write_nunit(tmp_path / "locked.xml", {LOCKED: "Passed"})
    manifest = _manifest(
        tmp_path / "shards.json",
        [
            _submission("opens", 1, initial),
            _submission("opens", 2, tmp_path / "missing.xml"),
            _submission("locked", 1, locked),
        ],
    )

    result = merge_shards(manifest)

    assert result["state"] == "incomplete"
    assert result["incomplete_splits"] == ["opens"]
    assert result["coverage"]["failed_cases"] == 1
