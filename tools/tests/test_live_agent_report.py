"""The public trial report keeps the whole paired cohort and its evidence boundaries."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_report as report
from tools.experiments import live_agent_trial as trial


def test_private_absolute_paths_are_removed_but_the_actual_scene_and_uri_survive():
    answer = "Opened Assets/Scenes/Grove.unity ([machine](C:\\Users\\example\\world.unity))."
    assert (
        report.public_text(answer) == "Opened Assets/Scenes/Grove.unity ([machine]([local path]))."
    )
    assert report.public_text("mcpforunity://editor/state") == "mcpforunity://editor/state"


@pytest.mark.parametrize("suffix", ["", "/family"])
def test_live_registry_observation_is_separate_from_the_claim_that_it_caused_failure(suffix):
    native = {
        "rows": [
            {
                "tool_name": "family",
                "state": "disabled",
                "stage": "native_enablement",
                "reason": "native_disabled",
            }
        ]
    }
    calls = [
        {"name": f"mcpforunity://tool-diagnostics{suffix}", "result": {"data": {"native": native}}}
    ]
    assert report.native_blockers(calls) == [{"index": 0, **native["rows"][0]}]


def test_wire_errors_preserve_both_tool_and_protocol_failures_without_local_paths():
    assert report.wire_error({"success": False, "error": "Unknown action 'set_holes'."}) == {
        "message": "Unknown action 'set_holes'."
    }
    assert report.wire_error({"code": -32602, "message": "Missing C:\\Users\\user\\local"}) == {
        "code": -32602,
        "message": "Missing [local path]",
    }


def test_replacement_registry_review_cannot_use_a_previous_grade(tmp_path):
    for folder in ("reviews-registry", "reviews-catalog"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "case.json").write_text(json.dumps({"reviewer": folder}))
    assert report.read_ledger(tmp_path, {"id": "case"}, "registered_package")["review"] == {
        "reviewer": "reviews-registry"
    }
    assert report.read_ledger(tmp_path, {"id": "case"}, "first")["review"] == {
        "reviewer": "reviews-catalog"
    }


def test_published_counts_and_outcomes_match_every_actual_frozen_original():
    data = json.loads((trial.HERE / "live_agent_results.json").read_bytes())
    report.verify(data)
    assert data["summary"]["core"]["scheduled"] == data["summary"]["full"]["scheduled"] == 71
    for name in ("core", "full"):
        assert data["summary"][f"{name}/existing"]["scheduled"] == 59
        assert data["summary"][f"{name}/new"]["scheduled"] == 12
    data["cases"].pop()
    with pytest.raises(ValueError, match="coverage"):
        report.verify(data)
