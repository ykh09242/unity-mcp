"""Strict saved soak identity, integrity and completeness admission regressions."""

import json
import pytest

from cli.utils.play_scenario_soak_compare import SoakComparisonError, compare_soak_metrics
from .play_scenario_soak_compare_fixtures import (
    _evidence,
    _change,
    _change_reproduction,
    _change_samples,
)


@pytest.mark.parametrize(
    "field,value",
    [("unity_version", "6000.1"), ("package_version", "other"), ("platform", "LinuxPlayer")],
)
def test_recorded_environment_mismatch_fails_clearly(tmp_path, field, value):
    baseline = _evidence(tmp_path / "left", [0, 1, 2], environment={"platform": "WindowsPlayer"})
    candidate = _evidence(tmp_path / "right", [0, 1, 2], environment={"platform": "WindowsPlayer"})
    _change_reproduction(candidate, **{field: value})
    with pytest.raises(SoakComparisonError, match="Incompatible"):
        compare_soak_metrics(baseline, candidate)


@pytest.mark.parametrize("values,warmup", [([0, 1, 2, 3], 1), ([0, 1, 2], 0)])
def test_plan_or_sampling_options_mismatch_is_not_comparable(tmp_path, values, warmup):
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", values, warmup=warmup)
    with pytest.raises(SoakComparisonError, match="Incompatible"):
        compare_soak_metrics(baseline, candidate)


@pytest.mark.parametrize(
    "filename,changes",
    [
        ("process.json", {"process_ended": False}),
        ("process.json", {"forced_termination": True}),
        ("process.json", {"actual_exit_code": 2}),
        ("process.json", {"process_id": 5678}),
        ("process.json", {"request_sha256": "a" * 64}),
        ("client-soak.json", {"evidence_state": "incomplete"}),
        ("client-soak.json", {"passed_iterations": 2}),
        ("client-soak.json", {"journal_sha256": "a" * 64}),
        ("run.json", {"player_schema_version": 1}),
        ("run.json", {"runner_resources_released": False}),
        ("run.json", {"progress_error": ""}),
        (
            "run.json",
            {
                "persistence_error": {
                    "schema_version": 1,
                    "stage": "final_report",
                    "exception_type": "IOException",
                    "error": "Disk unavailable",
                    "iteration": 3,
                }
            },
        ),
    ],
)
def test_complete_claim_requires_matching_process_client_and_final_evidence(
    tmp_path, filename, changes
):
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    _change(candidate, filename, **changes)
    with pytest.raises(SoakComparisonError, match="Invalid candidate evidence"):
        compare_soak_metrics(baseline, candidate)


@pytest.mark.parametrize(
    "filename",
    ["process.json", "client-soak.json", "run.json", "definition.json", "iteration-0003.json"],
)
def test_missing_required_evidence_never_admits_native_success_alone(tmp_path, filename):
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    (candidate / filename).unlink()
    with pytest.raises(SoakComparisonError, match="Invalid candidate evidence"):
        compare_soak_metrics(baseline, candidate)


@pytest.mark.parametrize("damage", ["torn", "hash", "order", "release", "nan", "bool", "overflow"])
def test_torn_or_corrupt_journal_is_never_compared(tmp_path, damage):
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    path = candidate / "iterations.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "torn":
        path.write_bytes(path.read_bytes()[:-1])
    elif damage == "hash":
        path.write_bytes(path.read_bytes().replace(b'"managed_bytes":1', b'"managed_bytes":9', 1))
    else:
        if damage == "order":
            rows[1]["iteration"] = 3
        elif damage == "release":
            rows[1]["runner_resources_released"] = False
        else:
            rows[1]["metrics"][0]["managed_bytes"] = {
                "nan": float("nan"),
                "bool": True,
                "overflow": 2**63,
            }[damage]
        path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    with pytest.raises(SoakComparisonError, match="Invalid candidate evidence"):
        compare_soak_metrics(baseline, candidate)


@pytest.mark.parametrize(
    "filename", ["request.json", "definition.json", "client-soak.json", "process.json", "soak.json"]
)
def test_oversized_artifact_is_rejected_before_json_parsing(tmp_path, filename):
    # Given: an artifact outside its 64 KiB admission limit.
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    (candidate / filename).write_bytes(b" " * 65537)
    # When/Then: no comparison is admitted.
    with pytest.raises(SoakComparisonError):
        compare_soak_metrics(baseline, candidate)


def test_artifact_link_is_rejected_even_if_target_is_valid(tmp_path):
    # Given: process evidence redirected to another otherwise valid regular file.
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    target = candidate / "original-process.json"
    (candidate / "process.json").rename(target)
    try:
        (candidate / "process.json").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable on this host: {exc}")
    # When/Then: admission rejects the linked artifact.
    with pytest.raises(SoakComparisonError):
        compare_soak_metrics(baseline, candidate)


def test_bounded_deep_json_is_an_evidence_admission_error(tmp_path):
    # Given: a valid JSON artifact with 30000 nested arrays, below its byte limit.
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    (candidate / "request.json").write_bytes(
        b'{"nested":' + b"[" * 30000 + b"0" + b"]" * 30000 + b"}"
    )
    # When/Then: the public comparison boundary classifies parser depth failure.
    with pytest.raises(SoakComparisonError):
        compare_soak_metrics(baseline, candidate)
