"""A report cannot certify native profile acceptance with skipped or invented work."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.verification_background_qa import validate_profiles


def witness():
    profiles = {}
    for name in ("script-check", "editmode-check", "playmode-check"):
        stages = [
            {"name": "freshness", "state": "passed"},
            {"name": "analysis", "state": "passed"},
            {"name": "final_freshness", "state": "passed"},
        ]
        if name != "script-check":
            stages.append(
                {"name": "tests", "state": "passed", "evidence": {"total": 1, "passed": 1}}
            )
        profiles[name] = {"native_settled": True, "stages": stages}
    return {
        "pid": 123,
        "focus_samples": [4, 4],
        "profiles": profiles,
        "host_reload": {"old_process_exited": True, "occupied": 0, "domain_changed": True},
    }


def test_complete_native_profile_witness():
    assert validate_profiles(witness()) == []


def test_missing_test_stage_or_failed_analysis_never_passes():
    value = witness()
    value["profiles"]["playmode-check"]["stages"] = []
    assert validate_profiles(value)
    value = witness()
    value["profiles"]["script-check"]["stages"][1]["state"] = "incomplete"
    assert validate_profiles(value)


def test_live_host_or_unsettled_native_job_prevents_acceptance():
    value = witness()
    value["host_reload"]["old_process_exited"] = False
    assert validate_profiles(value)
    value = witness()
    value["profiles"]["editmode-check"]["native_settled"] = False
    assert validate_profiles(value)


def test_wrong_profile_set_or_foreground_editor_prevents_acceptance():
    value = witness()
    del value["profiles"]["playmode-check"]
    assert validate_profiles(value)
    value = copy.deepcopy(witness())
    value["focus_samples"] = [123]
    assert validate_profiles(value)


def test_pinned_diagnostics_with_explicit_project_generator_gap_stays_incomplete():
    value = witness()
    report = value["profiles"]["script-check"]
    report["state"] = "incomplete"
    report["stages"][1].update(
        state="incomplete",
        evidence={
            "coverage_gaps": ["project_analyzers_and_generators_not_executed"],
            "providers": {
                "compiler": "5.9.0",
                "Microsoft.CodeAnalysis.CSharp.CodeStyle": "5.9.0",
                "Microsoft.Unity.Analyzers": "1.28.0",
            },
            "source_token": "a" * 64,
            "analysis_id": "b" * 32,
            "active_severity_counts": {"error": 0, "warning": 0, "info": 0},
        },
    )
    assert validate_profiles(value) == []
    report["state"] = "passed"
    assert validate_profiles(value)
    report["state"] = "incomplete"
    report["stages"][1]["evidence"]["coverage_gaps"].append("missing_source")
    assert validate_profiles(value)


@pytest.mark.asyncio
async def test_bridge_retries_only_atomic_publication_with_the_same_request(tmp_path, monkeypatch):
    from tools.verification_background_qa import Bridge
    import tools.source_freshness_background_qa as focus

    original = Path.replace
    attempts = []

    def replace(path, target):
        attempts.append(json.loads(path.read_text(encoding="utf-8")))
        if len(attempts) == 1:
            raise PermissionError("owned reader holds a non-delete-sharing handle")
        result = original(path, target)
        (tmp_path / (attempts[-1]["id"] + ".json")).write_text('{"success":true}', encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(focus, "foreground_pid", lambda: 42)
    process = SimpleNamespace(poll=lambda: None, decline_script_update=lambda: False)
    record = {"commands": [], "focus_samples": []}
    result = await Bridge(process, tmp_path, record).call("profiles", timeout=1)
    assert result["success"] is True
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(record["commands"]) == 1


@pytest.mark.asyncio
async def test_bridge_observes_same_reply_after_a_transient_read_lock(tmp_path, monkeypatch):
    from tools.verification_background_qa import Bridge
    import tools.source_freshness_background_qa as focus

    replace_original, read_original = Path.replace, Path.read_text
    reads = []

    def replace(path, target):
        payload = json.loads(read_original(path, encoding="utf-8"))
        result = replace_original(path, target)
        (tmp_path / (payload["id"] + ".json")).write_text('{"success":true}', encoding="utf-8")
        return result

    def read(path, *args, **kwargs):
        reads.append(path)
        if len(reads) == 1:
            raise PermissionError("temporary owned reply handle")
        return read_original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(focus, "foreground_pid", lambda: 42)
    record = {"commands": [], "focus_samples": []}
    process = SimpleNamespace(poll=lambda: None, decline_script_update=lambda: False)
    assert (await Bridge(process, tmp_path, record).call("profiles", timeout=1))["success"]
    assert len(record["commands"]) == 1 and len(reads) == 2 and reads[0] == reads[1]
