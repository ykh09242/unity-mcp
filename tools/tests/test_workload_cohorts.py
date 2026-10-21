"""Large work must finish before timing can be compared."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.workload_cohorts import validate_raw, compare, summarize, load_manifest


def raw():
    return {
        "schema": "unity-mcp-workload-raw-v1",
        "scenario": "report_pages",
        "variant": "indexed",
        "expected_units": 10000,
        "completed": True,
        "environment": {
            "editor": "6000.0",
            "backend": "Editor Mono",
            "os": "Windows",
            "cpu": "cpu",
            "processors": 8,
            "memory_mb": 32768,
            "gpu": "Null",
        },
        "samples": [
            {
                "preparation": "warm",
                "elapsed_ms": n,
                "completed_units": 10000,
                "output_sha256": "a" * 64,
                "main_thread_work_ms": n,
            }
            for n in range(10, 70, 10)
        ],
        "allocation_bytes": 1000,
        "retained_managed_delta_bytes": -200,
        "main_thread_stall_ms": None,
    }


def report(value):
    return {
        "schema": "unity-mcp-workload-capture-v1",
        "source": {"commit": "1" * 40},
        "manifest_sha256": "a" * 64,
        "harness_sha256": "b" * 64,
        "cases": [value],
    }


@pytest.mark.parametrize(
    "kind", ["refused", "short", "reordered", "nan", "bool", "few", "empty_environment"]
)
def test_rejects_faster_refusal_partial_work_and_incomparable_samples(kind):
    value = raw()
    if kind == "refused":
        value["completed"] = False
    if kind == "short":
        value["samples"][0]["completed_units"] = 1
    if kind == "reordered":
        value["samples"][0]["output_sha256"] = "b" * 64
    if kind == "nan":
        value["samples"][0]["elapsed_ms"] = float("nan")
    if kind == "bool":
        value["samples"][0]["elapsed_ms"] = True
    if kind == "few":
        value["samples"] = value["samples"][:1]
    if kind == "empty_environment":
        value["environment"] = {}
    with pytest.raises(ValueError):
        validate_raw(value, load_manifest())


def test_summary_separates_unknown_stalls_allocation_and_retention():
    value = raw()
    validate_raw(value, load_manifest())
    summary = summarize(value)
    assert summary["warm"]["p50_ms"] == 35
    assert summary["warm"]["p95_ms"] == 60
    assert summary["warm"]["throughput_units_per_second"] == pytest.approx(60000 / 0.210)
    assert summary["main_thread_stall_ms"] is None
    assert summary["allocation_bytes"] == 1000
    assert summary["retained_managed_delta_bytes"] == -200


@pytest.mark.parametrize(
    "where,key",
    [
        ("environment", "cpu"),
        ("environment", "backend"),
        ("environment", "editor"),
        ("report", "harness_sha256"),
        ("report", "manifest_sha256"),
    ],
)
def test_only_matching_environments_workloads_and_harnesses_compare(where, key):
    left = report(raw())
    right = deepcopy(left)
    target = right if where == "report" else right["cases"][0][where]
    target[key] = "different"
    assert compare(left, right)["status"] == "not_comparable"


def test_regression_is_advisory_but_output_drift_is_not_a_speedup():
    left = report(raw())
    right = deepcopy(left)
    for sample in right["cases"][0]["samples"]:
        sample["elapsed_ms"] *= 2
    result = compare(left, right)
    assert result["status"] == "advisory_regression"
    assert result["cases"][0]["ratio"] == 2
    for sample in right["cases"][0]["samples"]:
        sample["output_sha256"] = "c" * 64
    assert compare(left, right)["status"] == "not_comparable"


def test_manifest_has_all_scenarios_and_real_named_producers():
    manifest = load_manifest()
    assert len(manifest["scenarios"]) == 6
    for scenario in manifest["scenarios"].values():
        assert scenario["variants"] and scenario["producer"] and scenario["correctness"]
    json.dumps(manifest, allow_nan=False)


def test_native_capture_rejects_missing_or_mismatched_bound_inputs(tmp_path):
    from tools.workload_cohorts import capture, digest

    value = raw()
    source = tmp_path / "source.cs"
    source.write_text("native inputs")
    frozen = tmp_path / "inputs.json"
    frozen.write_text(json.dumps({"files": [{"path": str(source), "sha256": digest(source)}]}))
    value["inputs_sha256"] = "f" * 64
    artifact = tmp_path / "raw.json"
    artifact.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="requires verified"):
        capture([artifact], allow_partial=True)
    with pytest.raises(ValueError, match="different inputs"):
        capture([artifact], inputs=frozen, nunit=tmp_path / "absent.xml", allow_partial=True)
    source.write_text("changed after measurement")
    with pytest.raises(ValueError, match="changed after"):
        capture([artifact], inputs=frozen, nunit=tmp_path / "absent.xml", allow_partial=True)


def test_reload_cannot_use_duplicate_xml_or_invent_latency():
    value = raw()
    value.update(
        scenario="repeated_reload",
        variant="actual_domain",
        expected_units=3,
        verified_reloads=3,
        evidence_sha256=["a" * 64, "b" * 64, "c" * 64],
        samples=[],
    )
    validate_raw(value, load_manifest())
    value["evidence_sha256"][1] = "a" * 64
    with pytest.raises(ValueError, match="duplicated"):
        validate_raw(value, load_manifest())
    value["evidence_sha256"][1] = "b" * 64
    value["samples"] = raw()["samples"]
    with pytest.raises(ValueError, match="not latency"):
        validate_raw(value, load_manifest())


def test_native_capture_requires_real_inventory_and_marks_partial_scope(tmp_path):
    from tools.workload_cohorts import capture, digest

    value = raw()
    source = tmp_path / "source.cs"
    source.write_text("native inputs")
    frozen = tmp_path / "inputs.json"
    frozen.write_text(json.dumps({"files": [{"path": str(source), "sha256": digest(source)}]}))
    value["inputs_sha256"] = digest(frozen)
    artifact = tmp_path / "raw.json"
    artifact.write_text(json.dumps(value))
    results = tmp_path / "results.xml"
    method = load_manifest()["scenarios"]["report_pages"]["producer"][0]
    results.write_text(
        f'<test-run result="Passed" total="1" passed="1" failed="0" skipped="0" inconclusive="0"><test-case fullname="{method}" result="Passed" /></test-run>'
    )
    captured = capture([artifact], inputs=frozen, nunit=results, allow_partial=True)
    assert captured["partial"] is True
    assert captured["native_inputs_sha256"] == digest(frozen)
    with pytest.raises(ValueError, match="Missing or duplicate"):
        capture([artifact], inputs=frozen, nunit=results)
    results.write_text(results.read_text().replace(method, "Unrelated.Test"))
    with pytest.raises(ValueError, match="inventory failed"):
        capture([artifact], inputs=frozen, nunit=results, allow_partial=True)
