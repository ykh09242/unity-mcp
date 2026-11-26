"""Negative controls for outcome grading, paired native state and evidence provenance."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_outcomes as trial
from tools.experiments import verify_agent_outcome_originals as originals


def test_host_stderr_is_counted_even_when_public_calls_succeed():
    report = Path(originals.__file__).with_name("agent_outcome_original_intermediate.json")
    counts = originals.verify(json.loads(report.read_bytes()))
    for profile, tool_errors, failed_items, stderr_errors, stderr_only in (
        ("core", 43, 51, 58, 25),
        ("full", 49, 53, 56, 28),
    ):
        group = counts[profile]
        assert group["calls_by_method"]["tools/call"]["unsuccessful_replies"] == tool_errors
        assert group["failed_host_items"] == failed_items
        assert group["host_stderr_error_records"] == stderr_errors
        assert group["host_stderr_errors_in_otherwise_clean_cases"] == stderr_only

        # Give the acceptance check a fully successful control before isolating one host error.
        group["outcomes"] = {"pass": 71}
        group["unsuccessful_public_replies"] = 0
        group["failed_host_items"] = 0
        group["host_stderr_error_records"] = 0
    originals.require_perfect(counts)
    counts["core"]["host_stderr_error_records"] = 1
    with pytest.raises(ValueError, match="host error"):
        originals.require_perfect(counts)


def row(task="current_scene", language="ko", **state):
    before = {
        "activeScene": "West",
        "loadedScenes": ["West", "East"],
        "sceneIdentities": [{"name": "West", "handle": 1}, {"name": "East", "handle": 2}],
        "sceneCollateral": "objects",
        "assetCollateral": "assets",
        "selection": "",
        "frameDebuggerWindows": 0,
        **state,
    }
    return {
        "id": "opaque",
        "query": "original",
        "task": task,
        "language": language,
        "profile": "core",
        "panel": "existing",
        "status": "completed",
        "delivered": True,
        "availability": {"ready": True},
        "before": before,
        "after": deepcopy(before),
        "answer": "West",
        "calls": [
            {
                "name": "manage_scene",
                "arguments": {"action": "get_active"},
                "success": True,
                "result": {"success": True, "data": {"name": "West", "isLoaded": True}},
            }
        ],
    }


def accept(opaque, **changes):
    opaque["review"] = {
        "evidence_sha256": trial.review_packet(opaque)["evidence_sha256"],
        "answer_correct": True,
        "evidence_supports": True,
        "constraints_respected": True,
        "call_indices": [0],
        "reason": "Independent review of complete answer, successful result and native evidence.",
        **changes,
    }


def verdict(opaque):
    return trial.score(opaque)["outcome"]


def test_active_requires_an_unambiguous_answer_and_positive_receipt():
    opaque = row()
    assert verdict(opaque) == "pass"
    opaque["calls"][0]["arguments"]["action"] = "get_loaded_scenes"
    opaque["calls"][0]["result"] = {
        "scenes": [
            {"name": "West", "isActive": False, "isLoaded": True},
            {"name": "East", "isActive": True, "isLoaded": True},
        ]
    }
    assert verdict(opaque) == "review"
    opaque["calls"][0]["result"]["scenes"][0]["isActive"] = True
    opaque["calls"][0]["result"]["scenes"][1]["isActive"] = False
    assert verdict(opaque) == "pass"
    opaque["answer"] = "It could be East or West."
    assert verdict(opaque) == "review"
    opaque["calls"][0].update(name="manage_tools", arguments={"action": "describe"})
    assert verdict(opaque) == "fail"
    opaque["calls"] = []
    assert verdict(opaque) == "fail"


def test_review_is_profile_blind_and_bound_to_evidence_and_positive_indices():
    opaque = row()
    opaque["calls"][0].update(name="unity://editor/state", result={"activeScene": "West"})
    opaque["answer"] = "현재 활성 씬은 West입니다."
    packet = trial.review_packet(opaque)
    assert "profile" not in packet and "panel" not in packet
    assert verdict(opaque) == "review"
    accept(opaque)
    result = trial.score(opaque)
    assert result["outcome"] == "pass" and len(result["positive_receipts"]) == 1
    assert "answer" not in result and "West" not in str(result)
    opaque["answer"] = "West is not active."
    assert trial.score(opaque)["reason"] == "stale_review"
    accept(opaque, call_indices=[9])
    assert verdict(opaque) == "review"
    accept(opaque, evidence_supports=False)
    assert verdict(opaque) == "fail"


@pytest.mark.parametrize(
    "call",
    [
        {"name": "search_unity_tools", "success": True, "result": {"example": "West"}},
        {"name": "manage_tools", "success": True, "result": {"example": "West"}},
        {"name": "manage_scene", "success": False, "result": {"name": "West"}},
        {"name": "manage_scene", "success": True, "result": {"success": False, "name": "West"}},
    ],
)
def test_wrong_channel_or_failed_native_calls_cannot_ground_a_correct_guess(call):
    opaque = row()
    opaque["calls"] = [call]
    accept(opaque)
    assert verdict(opaque) == "fail"


def test_false_wire_value_and_missing_or_blocked_reply_are_distinct():
    opaque = row()
    opaque["calls"][0].update(name="unity://editor/some-observation", result=False)
    assert trial.review_packet(opaque)["calls"][0]["eligible"]
    assert verdict(opaque) == "review"
    opaque["calls"][0].update(success=None, result=None)
    assert trial.score(opaque)["stage"] == "harness"
    opaque["calls"][0]["success"] = False
    opaque["stage"] = "approval"
    assert verdict(opaque) == "fail" and trial.score(opaque)["stage"] == "approval"
    del opaque["stage"]
    opaque["calls"][0]["name"] = "search_unity_tools"
    assert trial.score(opaque)["stage"] == "discovery"


def test_host_approval_evidence_is_auditable_even_if_another_route_completed():
    opaque = row()
    opaque["host_calls"] = [
        {
            "server": "unityMCP",
            "tool": "example",
            "status": "failed",
            "error": "Approval required.",
        },
        {"server": "unityMCP", "tool": "manage_scene", "status": "completed", "error": None},
        {
            "server": "unityMCP",
            "tool": "scene_lifecycle",
            "status": "failed",
            "error": "{'message': 'This action was rejected due to unacceptable risk.'}",
        },
    ]
    opaque["stage"] = "approval"
    result = trial.score(opaque)
    assert result["outcome"] == "pass" and result["stage"] == "complete"
    assert [h["index"] for h in result["host_approval_receipts"]] == [0, 2]
    assert trial.review_packet(opaque)["failed_host_calls"][0]["tool"] == "example"
    opaque["answer"] = "No approval available."
    opaque["host_calls"] = []
    assert trial.score(opaque)["host_approval_receipts"] == []


@pytest.mark.parametrize(
    ("task", "language", "state"),
    [
        (
            "clip_platform_inheritance",
            "te",
            {
                "audioPath": "rain.wav",
                "audioPlatform": "Android",
                "audioOverride": False,
                "audioCollateral": "audio",
            },
        ),
        (
            "selected_agent",
            "ko",
            {"selection": "Guard", "selectedAgent": {"name": "Guard", "speed": 3}},
        ),
        (
            "package_list",
            "ja",
            {"packages": {"com.unity.inputsystem": "1.0", "com.unity.ai.navigation": "2.0"}},
        ),
        (
            "sample_surface_mask",
            "lv",
            {
                "surfaceSamples": [
                    {"x": 18, "y": 24, "surface": True},
                    {"x": 19, "y": 24, "surface": False},
                ],
                "terrainCollateral": "mask",
            },
        ),
        ("lookout_fov", "bn", {"cameraFov": 64, "cameraOrthographic": False}),
    ],
)
def test_fuzzy_multilingual_facts_never_pass_on_a_value_or_name_match(task, language, state):
    opaque = row(task, language, **state)
    opaque["answer"] = str(state) + " None of these values applies."
    assert verdict(opaque) == "review"
    accept(opaque, answer_correct=False, evidence_supports=False)
    assert verdict(opaque) == "fail"


def test_gate_native_verification_and_replacement_or_collateral_rejection():
    opaque = row(
        "gate_sensor_trigger",
        "fi",
        boxTrigger=False,
        boxSize=[2, 3, 4],
        boxCenter=[1, 0, 0],
        boxEnabled=True,
        boxId=123,
        boxSharedMaterialId=0,
        boxSharedMaterial="",
        gateObserverVersion=2,
    )
    opaque["answer"] = "Valmis."
    opaque["calls"][0].update(
        name="manage_components",
        arguments={"action": "set_property"},
        result={"success": True, "component": "BoxCollider", "isTrigger": True},
    )
    accept(opaque)
    assert verdict(opaque) == "fail"
    assert trial.score(opaque)["native_effect"] is False
    opaque["after"]["boxTrigger"] = True
    accept(opaque)
    assert verdict(opaque) == "pass"
    assert trial.score(opaque)["native_effect"] is True
    for field, value in (
        ("boxId", 124),
        ("boxSize", [4, 4, 4]),
        ("boxEnabled", False),
        ("sceneCollateral", "other"),
        ("assetCollateral", "other"),
        ("boxSharedMaterialId", 456),
    ):
        modified = deepcopy(opaque)
        modified["after"][field] = value
        accept(modified)
        assert trial.score(modified)["reason"] == "collateral_change"
        assert trial.score(modified)["native_effect"] is False
        assert trial.score(modified)["target_reached"] is (field != "boxId")
    del opaque["after"]["boxTrigger"]
    opaque["after"]["sceneCollateral"] = "target-component-deleted"
    assert trial.score(opaque)["reason"] == "collateral_change"


def test_initial_gate_observer_cannot_score_collateral_even_when_trigger_changed():
    opaque = row(
        "gate_sensor_trigger",
        "fi",
        boxTrigger=False,
        boxSize=[2, 3, 4],
        boxCenter=[1, 0, 0],
        boxEnabled=True,
        boxId=123,
    )
    opaque["after"].update(boxTrigger=True, sceneCollateral="trigger-was-included-in-v1-hash")
    result = trial.score(opaque)
    assert result["target_reached"] is True and result["native_effect"] is None
    assert result["outcome"] == "unobserved" and result["reason"] == "incomplete_gate_observer"


def test_map_target_must_be_empty_and_single_import_protecting_the_source_file():
    base_map = {"id": "original", "actions": ["move"], "bindings": ["up"]}
    opaque = row(
        "author_two_control_maps",
        "lv",
        inputPath="controls.inputactions",
        inputMaps={"Play": base_map},
        targetMaps=["Laiva", "Krasts"],
        fileHash="v1",
        importCount=0,
        inputCollateral="root-hash",
    )
    opaque["after"].update(fileHash="v2", importCount=1)
    for name in opaque["before"]["targetMaps"]:
        opaque["after"]["inputMaps"][name] = {"id": name, "actions": [], "bindings": []}
    assert trial.score(opaque)["native_effect"] is True
    opaque["after"]["importCount"] = 2
    assert trial.score(opaque)["reason"] == "single_import_not_observed"
    opaque["after"]["importCount"] = 1
    opaque["after"]["inputMaps"]["Laiva"]["actions"] = ["new"]
    assert trial.score(opaque)["reason"] == "maps_not_empty"
    opaque["after"]["inputMaps"]["Play"]["bindings"] = []
    assert trial.score(opaque)["reason"] == "collateral_change"


def test_terrain_target_and_every_untouched_cell_remain_independently_verified():
    opaque = row(
        "fill_surface_gap",
        "lv",
        terrainCollateral="unaffected-mask",
        surfaceSamples=[{"x": 18, "y": 24, "surface": False}, {"x": 19, "y": 24, "surface": False}],
    )
    opaque["after"]["surfaceSamples"][0]["surface"] = True
    assert trial.score(opaque)["native_effect"] is True
    opaque["after"]["surfaceSamples"][1]["surface"] = True
    assert trial.score(opaque)["reason"] == "collateral_change"
    opaque["before"]["surfaceSamples"][0]["surface"] = True
    assert verdict(opaque) == "unobserved"


def test_closed_window_and_readonly_state_required_even_after_correct_menu_answer():
    opaque = row("menu-path", "ko", menuPaths=["Window/Analysis/Frame Debugger"])
    opaque["answer"] = "Window/Analysis/Frame Debugger"
    opaque["after"]["frameDebuggerWindows"] = 1
    accept(opaque)
    assert trial.score(opaque)["reason"] == "collateral_change"


def test_timeout_preserves_visible_native_effect_separately_from_semantic_verdict():
    opaque = row("activate_scene", "es", targetScene="Cantera", loadedScenes=["West", "Cantera"])
    opaque["after"]["activeScene"] = "Cantera"
    opaque["status"] = "timeout"
    result = trial.score(opaque)
    assert (result["outcome"], result["stage"], result["native_effect"]) == (
        "incomplete",
        "timeout",
        True,
    )
    assert not result["observed"]


def test_missing_package_and_approval_failure_are_not_observed_agent_failures():
    missing = row("installed_package", "ko", targetPackage={})
    missing["availability"]["ready"] = False
    missing["id"] = "missing"
    declined = row()
    declined.update(status="model_error", stage="approval", delivered=False, calls=[], answer=None)
    summary = trial.aggregate([missing, declined])
    cell = summary["by_profile_panel"]["core"]["existing"]
    assert cell == {
        "scheduled": 2,
        "delivered": 1,
        "observed": 0,
        "resolved": 0,
        "passed": 0,
        "failed": 0,
        "review": 0,
        "attempted_incomplete": 0,
        "unobserved": 2,
        "native_effects_measured": 0,
        "native_effects_verified": 0,
        "targets_measured": 0,
        "targets_reached": 0,
        "pass_fraction_of_resolved": None,
    }
    assert [e["stage"] for e in summary["exceptions"]] == ["precondition", "approval"]
    with pytest.raises(ValueError, match="Duplicate"):
        trial.aggregate([missing, missing])


def test_registry_questions_require_the_installed_version_and_independent_registry_entry():
    record = {
        "name": "com.unity.inputsystem",
        "version": "1.14.2",
        "displayName": "Input System",
        "description": "Reads input from devices.",
        "registry": "https://packages.unity.com",
        "metadataSha256": "1" * 64,
        "tarballSha256": "2" * 64,
    }
    package = {k: record[k] for k in ("name", "version", "displayName", "description", "registry")}
    package["source"] = "Registry"
    valid = row("installed_package", "ja", targetPackage=package, registryRecord=record)
    valid["calls"] = [
        {
            "name": "manage_packages",
            "arguments": {"action": "get_package_info"},
            "success": True,
            "result": {"success": True, "data": package},
        }
    ]
    valid["answer"] = "Input System 1.14.2 (com.unity.inputsystem) provides device input."
    assert verdict(valid) == "review"
    accept(valid)
    assert verdict(valid) == "pass"
    for field, value in (("source", "Local"), ("source", "LocalTarball"), ("version", "1.0")):
        bad = deepcopy(valid)
        for state in (bad["before"], bad["after"]):
            state["targetPackage"][field] = value
        assert trial.score(bad)["reason"] == "registry_record_unavailable"
        assert trial.score(bad)["delivered"] and not trial.score(bad)["observed"]
    for field in ("metadataSha256", "tarballSha256", "name", "version"):
        bad = deepcopy(valid)
        del bad["before"]["registryRecord"][field]
        assert trial.score(bad)["reason"] == "registry_record_unavailable"
    del valid["before"]["registryRecord"]
    assert trial.score(valid)["reason"] == "registry_record_unavailable"
