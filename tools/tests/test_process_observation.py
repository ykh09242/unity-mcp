"""Reject truncated, cross-process and fabricated-zero OS observations."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.process_observation import summarize_process_observation


def records():
    return [
        {
            "type": "header",
            "schema": "unity-mcp-owned-process-v1",
            "pid": 123,
            "creation_time_utc_ticks": 100,
            "started_utc": "2026-10-22T00:00:00Z",
            "scope": "one_owned_process_excluding_children",
        },
        {
            "type": "sample",
            "pid": 123,
            "creation_time_utc_ticks": 100,
            "index": 0,
            "utc": "2026-10-22T00:00:01Z",
            "elapsed_ms": 1000,
            "working_set_bytes": 1024,
            "working_set_status": "supported",
            "process_handle_count": None,
            "process_handle_status": "unavailable",
        },
        {
            "type": "summary",
            "schema": "unity-mcp-owned-process-v1",
            "reason": "completed",
            "stop_reason": "process_exited",
            "sample_count": 1,
            "first_sample_utc": "2026-10-22T00:00:01Z",
            "last_sample_utc": "2026-10-22T00:00:01Z",
            "closed_utc": "2026-10-22T00:00:02Z",
            "observation_duration_ms": 2000,
            "working_set_supported_samples": 1,
            "handle_supported_samples": 0,
            "peak_working_set_bytes": 1024,
            "peak_process_handle_count": None,
            "coverage": "sampled_window",
        },
    ]


def write(tmp_path, rows):
    path = tmp_path / "process.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_supported_counters_and_unknown_handles_keep_distinct_meaning(tmp_path):
    summary = summarize_process_observation(write(tmp_path, records()))
    assert summary["complete_controller_run"]
    assert summary["peak_working_set_bytes"] == 1024
    assert summary["peak_process_handle_count"] is None
    assert summary["scope"] == "one_owned_process_excluding_children"


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "wrong_pid",
        "wrong_creation",
        "zero_supported",
        "fake_unknown",
        "wrong_peak",
        "wrong_count",
        "wrong_order",
        "time_reversed",
        "nan",
        "after_footer",
        "bool_count",
    ],
)
def test_rejects_incomplete_or_fabricated_observations(tmp_path, kind):
    rows = records()
    if kind == "truncated":
        rows.pop()
    elif kind == "wrong_pid":
        rows[1]["pid"] = 456
    elif kind == "wrong_creation":
        rows[1]["creation_time_utc_ticks"] += 1
    elif kind == "zero_supported":
        rows[1]["working_set_bytes"] = 0
    elif kind == "fake_unknown":
        rows[1]["process_handle_count"] = 0
    elif kind == "wrong_peak":
        rows[2]["peak_working_set_bytes"] = 2048
    elif kind == "wrong_count":
        rows[2]["sample_count"] = 2
    elif kind == "wrong_order":
        rows[1]["index"] = 1
    elif kind == "time_reversed":
        rows[1]["utc"] = "2026-10-21T00:00:00Z"
    elif kind == "nan":
        rows[1]["elapsed_ms"] = float("nan")
    elif kind == "after_footer":
        rows.append(deepcopy(rows[1]))
    elif kind == "bool_count":
        rows[2]["sample_count"] = True
    with pytest.raises(ValueError):
        summarize_process_observation(write(tmp_path, rows))


def test_identity_loss_is_visible_even_with_prior_valid_samples(tmp_path):
    rows = records()
    rows[-1]["stop_reason"] = "identity_mismatch"
    assert not summarize_process_observation(write(tmp_path, rows))["complete_controller_run"]


def test_empty_run_is_unknown_not_zero_memory(tmp_path):
    rows = records()
    rows.pop(1)
    rows[-1].update(
        sample_count=0,
        first_sample_utc=None,
        last_sample_utc=None,
        working_set_supported_samples=0,
        peak_working_set_bytes=None,
        coverage="empty",
    )
    summary = summarize_process_observation(write(tmp_path, rows))
    assert not summary["complete_controller_run"]
    assert summary["peak_working_set_bytes"] is None


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows process counter/lifecycle check")
@pytest.mark.parametrize("mode", ["complete", "exited", "identity", "failure"])
def test_owned_windows_sampler_captures_and_closes_on_every_path(tmp_path, mode):
    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell7 unavailable")
    script = Path(__file__).with_name("fixtures") / "owned_process_observation.ps1"
    output = tmp_path / "owned.jsonl"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(script),
            "-PythonPath",
            sys.executable,
            "-SamplerPath",
            str(Path(__file__).resolve().parents[1] / "process_observation.ps1"),
            "-OutputPath",
            str(output),
            "-Mode",
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = summarize_process_observation(output)
    assert summary["sample_count"] >= 2
    assert summary["working_set_supported_samples"] == summary["sample_count"]
    assert summary["handle_supported_samples"] == summary["sample_count"]
    assert summary["complete_controller_run"] == (mode in {"complete", "exited"})
    output.rename(tmp_path / "released.jsonl")


@pytest.mark.parametrize(
    "fault", [None, "pid", "creation", "run", "inputs", "nunit", "cleanup", "sampling_error"]
)
def test_process_window_binds_to_exact_native_run(tmp_path, fault):
    import hashlib
    from tools.process_observation import read_owned_process_run

    path = write(tmp_path, records())
    path.rename(tmp_path / "process-observation.jsonl")
    invocation = {
        "pid": 123,
        "creationTimeUtcTicks": 100,
        "runId": "owned-run",
        "cacheReused": True,
    }
    result = {
        "runId": "owned-run",
        "exitCode": 0,
        "timeout": False,
        "ownedSurvivors": 0,
        "protectedPreserved": True,
        "processObservationError": None,
    }
    if fault == "pid":
        invocation["pid"] += 1
    if fault == "creation":
        invocation["creationTimeUtcTicks"] += 1
    if fault == "run":
        result["runId"] = "different"
    if fault == "cleanup":
        result["ownedSurvivors"] = 1
    if fault == "sampling_error":
        result["processObservationError"] = "stream failed"
    (tmp_path / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
    (tmp_path / "process-result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "inputs.json").write_bytes(b"owned input bytes")
    (tmp_path / "results.xml").write_bytes(b"owned nunit bytes")
    inputs = hashlib.sha256(b"owned input bytes").hexdigest()
    nunit = hashlib.sha256(b"owned nunit bytes").hexdigest()
    if fault == "inputs":
        inputs = "f" * 64
    if fault == "nunit":
        nunit = "f" * 64
    if fault:
        with pytest.raises(ValueError):
            read_owned_process_run(tmp_path, inputs_sha256=inputs, nunit_sha256=nunit)
    else:
        summary = read_owned_process_run(tmp_path, inputs_sha256=inputs, nunit_sha256=nunit)
        assert summary["observation"]["sample_count"] == 1
        assert len(summary["evidence_sha256"]) == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 456),
        ("creation_time_utc_ticks", 789),
        ("scope", "another_process"),
        ("started_utc", "2026-10-21T00:00:00Z"),
    ],
)
def test_footer_cannot_replace_validated_sample_identity(tmp_path, field, value):
    rows = records()
    rows[-1][field] = value
    with pytest.raises(ValueError):
        summarize_process_observation(write(tmp_path, rows))
