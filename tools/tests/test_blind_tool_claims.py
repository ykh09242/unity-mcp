"""Check fact, origin and format scoring across mixed and absent evidence."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_answers import FIELDS, answer_instruction, final_answer
from tools.experiments.blind_tool_claims import score_claims


def claim(value=None, source="unknown", channel=None):
    return {"value": value, "source": source, "channel": channel}


def empty(task):
    return {field: claim() for field in FIELDS[task]}


@pytest.mark.parametrize("route", ["inspect_open_shader_graph", "manage_shader.inspect_open_graph"])
def test_open_model_is_scored_only_for_the_requested_path_and_source(route):
    from tools.experiments import blind_tool_trace as trace
    from tools.experiments.blind_tool_observations import fixture_availability

    path = "Assets/장면 A.shadergraph"
    args = {"path": path}
    if route.startswith("manage_shader."):
        args["action"] = "inspect_open_graph"
    data = {
        "path": path,
        "status": "open_model",
        "nodeCount": 2,
        "edgeCount": 0,
        "hasUnsavedChanges": True,
    }
    assert trace.decision(route, args, path, endpoint=True) == "unity"
    assert trace.decision(route, args | {"page_number": 1}, path, endpoint=True) == "blocked"
    assert trace.observation(route, data, graph_asset="Assets/Other.shadergraph") == {}
    evidence = trace.observation(route, data, graph_asset=path)
    row = {"route": route, **evidence}
    assert evidence["has_unsaved_changes"] is True
    answer = empty("graph-open") | {
        key: claim(value, "editor_live", "editor")
        for key, value in {"data_state": "open", "nodes": 2, "edges": 0}.items()
    }
    assert score_claims("graph-open", answer, [row], [])["status"] == "correct"
    answer["saved_reference"] = {"nodes": claim(2, "asset_file", "editor")}
    assert score_claims("graph-open", answer, [row], [])["status"] != "correct"
    fixture = {"task": "graph-open", "graph_asset": path}
    proof = {"arguments": {"path": path}, "reply": {"success": True, "data": data}}
    assert (
        fixture_availability(fixture, {"observation_fixtures": {"graph-open": proof}}) == "positive"
    )
    assert fixture_availability(fixture, {}) == "unverified"
    assert not trace.observation(route, data | {"edgeCount": False}, graph_asset=path)


def test_open_window_and_saved_counts_have_independent_receipts():
    answer = empty("graph-open")
    answer["saved_reference"] = {"nodes": claim(24, "asset_file", "file")}
    file = [{"nodes": 24, "edges": 24, "node_types": ["A", "B"]}]
    result = score_claims("graph-open", answer, [], file)
    assert result["status"] == "correct"
    assert (
        result["fact_accuracy"]
        == result["provenance_accuracy"]
        == {"matched": 1, "measured": 1, "unknown": 4, "unverified": 0, "unscored": 0}
    )
    answer["nodes"] = claim(24, "asset_file", "file")
    wrong = score_claims("graph-open", answer, [], file)
    assert wrong["status"] == "incorrect"
    assert wrong["claims"]["nodes"]["provenance"] == "mismatch"
    assert wrong["claims"]["nodes"]["fact"] != "match"


def test_fact_correct_and_claimed_source_or_channel_wrong():
    file = [{"nodes": 10, "edges": 11, "node_types": ["A", "B"]}]
    answer = {
        key: claim(value, "asset_file", "file")
        for key, value in {
            "nodes": 10,
            "edges": 11,
            "node_types": ["B", "A"],
            "data_state": "saved",
        }.items()
    }
    answer["nodes"]["channel"] = "editor"
    answer["edges"]["source"] = "imported_asset"
    result = score_claims("graph-saved", answer, [], file)
    assert result["fact_accuracy"]["matched"] == 4
    assert result["provenance_accuracy"]["matched"] == 2
    assert result["source_accuracy"]["matched"] == result["channel_accuracy"]["matched"] == 3
    assert (result["claims"]["nodes"]["source"], result["claims"]["nodes"]["channel"]) == (
        "match",
        "mismatch",
    )
    assert (result["claims"]["edges"]["source"], result["claims"]["edges"]["channel"]) == (
        "mismatch",
        "match",
    )
    assert result["status"] == "incorrect"


def test_true_does_not_match_one_and_absent_receipts_cannot_credit_guess():
    answer = empty("game-view")
    answer["width"] = claim(True, "editor_live", "editor")
    answer["height"] = claim(2, "editor_live", "editor")
    result = score_claims("game-view", answer, [{"render_size": [1, 2]}], [])
    assert result["claims"]["width"]["fact"] == "mismatch"
    assert result["claims"]["width"]["provenance"] == "match"
    assert result["claims"]["height"]["fact"] == "match"
    missing = score_claims("game-view", answer, [], [])
    assert missing["fact_accuracy"]["matched"] == missing["provenance_accuracy"]["matched"] == 0
    assert missing["status"] == "needs_review"


@pytest.mark.parametrize("bad", ["not json", '{"width": {}}', '{"extra": 5}', "[]"])
def test_invalid_format_retains_separate_metrics(tmp_path, bad):
    (tmp_path / "answer.txt").write_text(bad)
    result = final_answer(tmp_path, "game-view", [], [], protocol="v3")
    assert result["format_valid"] is False
    assert result["fact_accuracy"]["unscored"] == 3
    assert result["status"] == "needs_review"


def test_unavailable_reports_distinguish_scoped_limits_from_unobserved_runs(tmp_path):
    answer = empty("graph-open")
    (tmp_path / "answer.txt").write_text(json.dumps(answer))
    assert final_answer(tmp_path, "graph-open", [], [], protocol="v3")["status"] == "limited"
    assert (
        final_answer(tmp_path, "graph-open", [], [], protocol="v3", explicit_limit=True)["status"]
        == "correct"
    )
    assert "search_unity_tools" not in answer_instruction("graph-open", "v3")


def test_unknown_additional_keys_and_bad_values_do_not_crash():
    answer = empty("game-view") | {"extra": {}}
    answer["height"] = {"value": {}, "source": [], "channel": {}}
    assert not score_claims("game-view", answer, [], [])["format_valid"]


def test_child_report_cannot_be_supplied_or_corrected_by_parent(tmp_path):
    parent = json.dumps(empty("game-view"))
    (tmp_path / "answer.txt").write_text(parent)
    assert (
        final_answer(tmp_path, "game-view", [], [], parent, protocol="v3", actor_answer="")[
            "status"
        ]
        == "missing"
    )
    assert not final_answer(
        tmp_path, "game-view", [], [], parent, protocol="v3", actor_answer="child prose"
    )["format_valid"]
