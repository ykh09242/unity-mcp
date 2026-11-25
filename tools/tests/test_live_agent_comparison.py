"""The matched repeat binds the published results to the original prompts and catalog."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_comparison as comparison
from tools.experiments import live_agent_report as report


def test_pairing_keeps_both_directions_and_visible_starting_differences():
    first = {
        "id": "a",
        "query": "same original",
        "profile": "core",
        "outcome": "fail",
        "target_before": {"boxId": 17, "boxTrigger": False},
    }
    second = first | {"id": "b", "outcome": "pass"}
    repeat = [
        first | {"outcome": "pass", "target_before": {"boxId": 99, "boxTrigger": False}},
        second | {"outcome": "fail", "target_before": {"boxId": 98, "boxTrigger": True}},
    ]
    assert comparison.compare([first, second], repeat) == {
        "by_profile": {"core": {"fail_to_pass": 1, "pass_to_fail": 1}},
        "different_starting_facts": {"b": ["boxTrigger"]},
        "editor_local_starting_differences": {"boxId": 2},
    }
    with pytest.raises(ValueError, match="Different original"):
        comparison.compare([first], [first | {"query": "rewritten original"}])


def test_equal_inventory_sizes_cannot_hide_an_unrelated_tool_replacement():
    old = {"tools": ["first"], "resources": [], "templates": []}
    previous = dict.fromkeys(("core", "full"), {"sha256": report.digest(old)})
    new = {**old, "tools": ["replacement"]}
    for key, uri in comparison.PACKAGE_URIS.items():
        new[key] = [uri]
    fingerprints = {key: len(v) for key, v in new.items()} | {"sha256": report.digest(new)}
    with pytest.raises(ValueError, match="beyond the two package URIs"):
        comparison.check_inventories(
            dict.fromkeys(previous, new), dict.fromkeys(previous, fingerprints), previous
        )
