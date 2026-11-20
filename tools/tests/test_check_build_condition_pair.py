"""Offline contract controls for the optional native fixture's archived report."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_build_condition_pair import verify


def receipt():
    pair = {"editor_reward": 1}
    for label, digit, coins in (("broken", "1", 0), ("repaired", "2", 1)):
        guid = digit * 32
        pair[label] = {
            "report": {
                "report_id": "br-" + guid,
                "report_revision": digit * 64,
                "summary": {
                    "native_build_guid": guid,
                    "native_report_available": True,
                    "native_result": "succeeded",
                    "native_platform": "StandaloneWindows64",
                },
                "build_conditions": {
                    "editor_active_target": "StandaloneWindows64",
                    "player_target": "StandaloneWindows64",
                    "player_extra_defines": {
                        "complete": True,
                        "names": ["MCP_FIXTURE_REWARD_DISABLED"] if coins == 0 else [],
                    },
                    "editor_compilation_only_assemblies": {"names": ["MCPForUnity.Editor"]},
                    "player_filter_assemblies": {"names": ["TestAsmdef"]},
                },
            },
            "player_marker": f"MCP_BUILD_CONDITION_REWARD {guid} {coins}",
            "process_exit": 0 if coins else 42,
        }
    return pair


def test_requires_two_tied_process_markers_and_known_define_inputs():
    verify(receipt())
    pair = receipt()
    pair["broken"]["player_marker"] = pair["repaired"]["player_marker"]
    with pytest.raises(ValueError, match="reward observation"):
        verify(pair)
    pair = receipt()
    pair["repaired"]["report"]["build_conditions"]["player_extra_defines"]["complete"] = False
    with pytest.raises(ValueError, match="compiler inputs"):
        verify(pair)
