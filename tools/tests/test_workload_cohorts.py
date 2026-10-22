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
    assert len(manifest["scenarios"]) == 7
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


@pytest.mark.parametrize("kind", ["crlf", "content", "binary"])
def test_source_provenance_accepts_only_checkout_newlines(tmp_path, monkeypatch, kind):
    from tools import workload_cohorts as cohorts

    monkeypatch.setattr(cohorts, "ROOT", tmp_path)
    source = tmp_path / ("source.cs" if kind != "binary" else "source.bin")
    staged = tmp_path / "staged.cs"
    staged.write_bytes(b"first\nsecond\n")
    source.write_bytes(b"first\r\nsecond\r\n" if kind != "content" else b"first\nchanged\n")
    entry = {"source": str(source), "path": str(staged), "sha256": cohorts.digest(staged)}
    if kind == "crlf":
        assert cohorts.source_provenance(entry) == "crlf_only"
    else:
        with pytest.raises(ValueError, match="differs"):
            cohorts.source_provenance(entry)


def test_capture_real_reload_bundles_and_binary_git_diff(tmp_path, monkeypatch):
    import hashlib
    from tools import workload_cohorts as cohorts

    source = tmp_path / "prepared.cs"
    source.write_text("retained prepared overlay")
    frozen = tmp_path / "inputs.json"
    frozen.write_text(
        json.dumps({"files": [{"path": str(source), "sha256": cohorts.digest(source)}]})
    )
    value = raw()
    value["inputs_sha256"] = cohorts.digest(frozen)
    artifact = tmp_path / "raw.json"
    artifact.write_text(json.dumps(value))

    def xml(path, method, stamp):
        path.write_text(
            f'<test-run result="Passed" total="1" passed="1" failed="0" skipped="0" inconclusive="0" start-time="{stamp}"><test-case fullname="{method}" result="Passed" /></test-run>'
        )

    manifest = cohorts.load_manifest()
    results = tmp_path / "results.xml"
    xml(results, manifest["scenarios"]["report_pages"]["producer"][0], "native")
    directories = []
    for n in range(3):
        directory = tmp_path / str(n)
        directory.mkdir()
        (directory / "inputs.json").write_bytes(frozen.read_bytes())
        (directory / "invocation.json").write_text(
            json.dumps(
                {
                    "runId": str(n),
                    "project": "owned-project",
                    "cacheReused": True,
                    "filters": manifest["scenarios"]["repeated_reload"]["producer"],
                }
            )
        )
        (directory / "process-result.json").write_text(
            json.dumps(
                {
                    "runId": str(n),
                    "exitCode": 0,
                    "timeout": False,
                    "ownedSurvivors": 0,
                    "protectedPreserved": True,
                }
            )
        )
        xml(
            directory / "lifecycle-results.xml",
            manifest["scenarios"]["repeated_reload"]["producer"][0],
            str(n),
        )
        directories.append(directory)
    binary_diff = b"diff with non-UTF8 \x95\x00\n"

    def git_output(args):
        if "rev-parse" in args:
            return b"123456\n"
        assert "--no-ext-diff" in args and "--no-textconv" in args
        return binary_diff

    monkeypatch.setattr(cohorts.subprocess, "check_output", git_output)
    kwargs = dict(inputs=frozen, nunit=results, reload_runs=directories, allow_partial=True)
    captured = cohorts.capture([artifact], reload_results_name="lifecycle-results.xml", **kwargs)
    assert captured["cases"][-1]["verified_reloads"] == 3
    assert len(captured["native_source_provenance"]["prepared_overlays"]) == 1
    assert captured["source"]["tracked_diff_sha256"] == hashlib.sha256(binary_diff).hexdigest()
    with pytest.raises(ValueError, match="reload verification failed"):
        cohorts.capture([artifact], **kwargs)
    with pytest.raises(ValueError, match="Unsupported"):
        cohorts.capture([artifact], reload_results_name="../results.xml", **kwargs)


def observed_raw():
    value = raw()
    value.update(
        observation_schema="editor-update-retention-v1",
        observer_released=True,
        baseline={
            "domain": "d" * 32,
            "post_idle_managed_bytes": 1200,
            "owner_counts": {"files": 64},
            "working_set_bytes": 2048,
            "process_handle_count": 100,
        },
        observations=[
            {
                "domain": "d" * 32,
                "run_index": i,
                "post_idle_managed_bytes": 1000,
                "after_work_managed_bytes": 1300,
                "sampled_peak_managed_bytes": 1400,
                "editor_update_count": 2,
                "editor_update_gap_ms": 100,
                "owner_counts": {"files": 64},
                "working_set_bytes": 2048,
                "process_handle_count": 100,
            }
            for i in range(6)
        ],
        main_thread_stall_ms=100,
    )
    return value


@pytest.mark.parametrize(
    "corruption", ["updates", "owner", "peak", "gap", "round", "domain", "observer"]
)
def test_observed_cohort_rejects_incomplete_or_incoherent_measurements(corruption):
    value = observed_raw()
    row = value["observations"][0]
    if corruption == "updates":
        row["editor_update_count"] = 0
    if corruption == "owner":
        row["owner_counts"]["files"] = -1
    if corruption == "peak":
        row["sampled_peak_managed_bytes"] = 900
    if corruption == "gap":
        value["main_thread_stall_ms"] = 1
    if corruption == "round":
        value["observations"].pop()
    if corruption == "domain":
        row["domain"] = "e" * 32
    if corruption == "observer":
        value["observer_released"] = False
    with pytest.raises(ValueError):
        validate_raw(value, load_manifest())


def test_summary_retains_actual_update_gaps_and_post_idle_series():
    value = observed_raw()
    validate_raw(value, load_manifest())
    summary = summarize(value)
    assert summary["main_thread_stall_ms"] == 100
    assert summary["max_main_thread_work_ms"] == 60
    assert summary["retention"]["post_idle_managed_bytes"] == [1000] * 6
    assert summary["retention"]["sampled_peak_managed_bytes"] == 1400
    assert summary["retention"]["owner_counts_unchanged"] is True
    assert summary["retention"]["plateau"] == "within_observed_band"


@pytest.mark.parametrize("damage", [None, "missing_pair", "same_domain", "missing_heap"])
def test_reload_memory_requires_complete_distinct_domain_pairs(damage):
    value = raw()
    pair = [observed_raw()["baseline"].copy(), observed_raw()["baseline"].copy()]
    pair[0].update(phase="before_reload", domain="a" * 32)
    pair[1].update(phase="after_reload_cleanup", domain="b" * 32)
    value.update(
        scenario="repeated_reload",
        variant="actual_domain",
        expected_units=3,
        verified_reloads=3,
        evidence_sha256=["a" * 64, "b" * 64, "c" * 64],
        samples=[],
        reload_observations=[deepcopy(pair) for _ in range(3)],
    )
    if damage == "missing_pair":
        value["reload_observations"].pop()
    if damage == "same_domain":
        value["reload_observations"][0][1]["domain"] = "a" * 32
    if damage == "missing_heap":
        value["reload_observations"][0][1].pop("post_idle_managed_bytes")
    if damage:
        with pytest.raises(ValueError):
            validate_raw(value, load_manifest())
    else:
        validate_raw(value, load_manifest())
        assert summarize(value)["reload_retention"]["post_idle_managed_bytes"] == [1200] * 3


@pytest.mark.parametrize("status", ["invalid", "full", "empty", "not_measured"])
def test_measurement_metadata_rejects_unknown_allocation_as_zero(status):
    value = raw()
    value["allocation_bytes"] = 0
    value["allocation_observation"] = {
        "metric": "unity_gc_alloc_bytes",
        "scope": "current_thread",
        "status": status,
        "samples": 16,
        "capacity": 16,
        "bytes": 0,
        "reason": "owned-control",
    }
    with pytest.raises(ValueError):
        validate_raw(value, load_manifest())


def test_measurement_metadata_keeps_full_recorder_unknown_and_scope_visible():
    value = raw()
    value["allocation_bytes"] = None
    value["allocation_observation"] = {
        "metric": "unity_gc_alloc_bytes",
        "scope": "current_thread",
        "status": "full",
        "samples": 16,
        "capacity": 16,
        "bytes": None,
    }
    value["worker_allocation_observation"] = {
        "metric": "managed_allocated_bytes",
        "scope": "worker_threads",
        "status": "not_measured",
        "bytes": None,
        "reason": "main-thread-only",
    }
    validate_raw(value, load_manifest())
    result = summarize(value)
    assert result["allocation_observation"]["status"] == "full"
    assert result["worker_allocation_observation"]["bytes"] is None


def python_observed():
    value = raw()
    value.update(
        scenario="concurrent_readers",
        variant="ascii-16-B",
        expected_units=16,
        allocation_bytes=None,
        retained_managed_delta_bytes=None,
        traced_peak_bytes=4096,
        event_loop_callback_gap_ms=7,
    )
    value["environment"].update(editor="not_applicable", backend="CPython 3.14")
    for sample in value["samples"]:
        sample.update(completed_units=16, main_thread_work_ms=None)
    value["python_observation"] = {
        "schema": "python-traced-and-callback-v1",
        "tracemalloc": {
            "metric": "python_traced_peak_bytes",
            "scope": "python_allocations",
            "phase": "separate_pass",
            "peak_bytes": 4096,
        },
        "callbacks": [
            {
                "pass_index": i,
                "callback_count": 2,
                "maximum_gap_ms": i + 1,
                "observer_released": True,
            }
            for i in range(7)
        ],
    }
    return value


@pytest.mark.parametrize(
    "fault", ["unknown_peak", "wrong_scope", "live_observer", "wrong_gap", "as_unity_heap"]
)
def test_measurement_metadata_rejects_mislabeled_python_observations(fault):
    value = python_observed()
    if fault == "unknown_peak":
        value["python_observation"]["tracemalloc"]["peak_bytes"] = None
    elif fault == "wrong_scope":
        value["python_observation"]["tracemalloc"]["scope"] = "whole_process_rss"
    elif fault == "live_observer":
        value["python_observation"]["callbacks"][0]["observer_released"] = False
    elif fault == "wrong_gap":
        value["event_loop_callback_gap_ms"] = 0
    elif fault == "as_unity_heap":
        value["retained_managed_delta_bytes"] = 4096
    with pytest.raises(ValueError):
        validate_raw(value, load_manifest())


def test_measurement_metadata_separates_python_callbacks_and_traced_peak():
    value = python_observed()
    validate_raw(value, load_manifest())
    result = summarize(value)
    assert result["event_loop_callback_gap_ms"] == 7
    assert result["traced_peak_bytes"] == 4096
    assert result["retained_managed_delta_bytes"] is None
    assert result["main_thread_stall_ms"] is None


@pytest.mark.parametrize(
    "fault", ["empty", "missing_capacity", "missing_samples", "fractional_samples"]
)
def test_complete_recorder_cannot_hide_empty_or_missing_coverage(fault):
    value = raw()
    value["allocation_bytes"] = 0
    item = {
        "metric": "unity_gc_alloc_bytes",
        "scope": "current_thread",
        "status": "complete",
        "method": "profiler_recorder",
        "unit": "Bytes",
        "positive_control_bytes": 65536,
        "bytes": 0,
        "samples": 1,
        "capacity": 16,
    }
    if fault == "empty":
        item["samples"] = 0
    if fault == "missing_capacity":
        item.pop("capacity")
    if fault == "missing_samples":
        item.pop("samples")
    if fault == "fractional_samples":
        item["samples"] = 0.5
    value["allocation_observation"] = item
    with pytest.raises(ValueError):
        validate_raw(value, load_manifest())
