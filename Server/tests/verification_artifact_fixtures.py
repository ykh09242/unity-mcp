"""Independent synthetic receipts shaped like retained Unity captures."""

import json
from pathlib import Path
from xml.etree import ElementTree


def write_nunit(path: Path, cases: dict[str, str]) -> Path:
    """Write plain NUnit leaves for a simulated runner submission."""
    if "Failed" in cases.values():
        result = "Failed(Child)"
    else:
        result = next(iter(cases.values()))
    root = ElementTree.Element("test-run", total=str(len(cases)), result=result, duration="0.3")
    suite = ElementTree.SubElement(root, "test-suite", type="Assembly", name="Game.Tests")
    for name, outcome in cases.items():
        ElementTree.SubElement(suite, "test-case", fullname=name, result=outcome, duration="0.1")
    ElementTree.ElementTree(root).write(path, encoding="utf-8")
    return path


def make_capture(
    directory,
    index,
    state="passed",
    *,
    platform="WindowsPlayer",
    manifest=None,
    base_manifest=None,
    base_source=None,
):
    directory.mkdir(parents=True, exist_ok=True)
    source, job, build = f"{index:064x}", f"job-{index}", f"build-{index}"
    payload = f"{index + 200:064x}"
    status = "succeeded" if state == "passed" else "failed"
    stage = {
        "name": "standalone_player",
        "state": state,
        "evidence": {
            "job_id": job,
            "build_id": build,
            "payload_hash": payload,
            "build_source_revision": source,
            "status": status,
            "scenario": "doors",
            "exit_code": 0 if state == "passed" else 1,
            "finalization_state": "completed",
        },
    }
    report = {
        "schema": "verification_report@1",
        "state": state,
        "native_settled": True,
        "run_id": f"run-{index}",
        "project_scope": "game",
        "installation": {"unity_version": "6000.0.69f1"},
        "source_token": source,
        "source_mode": "current",
        "comparison": {
            "selected_checks": {"checks": [], "outstanding": []},
            "live_matches_candidate": True,
            "source_revision": source,
            "manifest_sha256": manifest or f"{index + 400:064x}",
            "baseline_manifest_sha256": base_manifest,
            "baseline_source_revision": base_source,
        },
        "children": [{"kind": "standalone_player", "job_id": job, "settled": True}],
        "executed_checks": [{"kind": "scenario", "id": "doors", "job_id": job, "status": status}],
        "stages": [stage, {"name": "final_source", "state": "passed", "evidence": {}}],
    }
    native = {
        "job_id": job,
        "status": status,
        "exit_code": 0 if state == "passed" else 1,
        "execution_environment": "player",
        "phase": "finished",
        "repeat_count": 1,
        "timeout_seconds": 120,
        "runner_resources_released": True,
        "finalization_state": "completed",
        "scenario": {"name": "doors"},
        "reproduction": {
            "build_id": build,
            "build_source_revision": source,
            "payload_hash": payload,
            "definition_hash": "a" * 64,
            "payload_verification": "launcher_admission",
            "unity_version": "6000.0.69f1",
            "package_version": "2.5.0",
            "environment": {
                "operating_system": "one",
                "platform": platform,
                "architecture": "x64",
                "scripting_backend": "mono",
                "build_mode": "release",
                "runtime_version": "1",
            },
        },
    }
    if state == "failed":
        native["failure"] = {
            "code": "condition_unmet",
            "target": "fixture.ready",
            "expected": "true",
            "actual": "false",
        }
    a, b = directory / f"verification-{index}.json", directory / f"native-{index}.json"
    a.write_text(json.dumps({"success": True, "data": report}), encoding="utf-8")
    b.write_text(json.dumps(native), encoding="utf-8")
    return a, b
