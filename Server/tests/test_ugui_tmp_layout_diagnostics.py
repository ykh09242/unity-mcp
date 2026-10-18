"""Closed current-generated TMP diagnostics; never invokes Unity."""

import copy
import importlib

import pytest


def sample(status="observed"):
    metrics = {
        "native_overflow": False,
        "first_overflow_index": -1,
        "native_truncated": False,
        "character_count": 4,
        "line_count": 1,
        "base_font_size": 32.0,
        "autosize": False,
        "min_point_size": 32.0,
        "max_point_size": 32.0,
        "fallback_characters": 0,
        "font_count": 1,
        "ink_bounds": [-40.0, -16.0, 80.0, 32.0],
        "text_rect": [-100.0, -50.0, 200.0, 100.0],
        "margins": [0.0, 0.0, 0.0, 0.0],
    }
    node = {
        "instance_id": 2,
        "diagnostic_path": "/0",
        "name": "Label",
        "status": status,
        "reason": None if status == "observed" else "input_owned",
        **(metrics if status == "observed" else dict.fromkeys(metrics)),
    }
    return {
        "kind": "ugui_tmp_layout_diagnostics",
        "schema_version": 1,
        "framework": "ugui_tmp",
        "mode": "edit",
        "frame": 0,
        "target_id": 1,
        "status": "complete",
        "evidence": {
            "observation": "existing_generated_layout",
            "freshness": "cached_not_marked_dirty",
            "rasterization": "not_observed",
            "profile": None,
        },
        "nodes": [node],
        "traversal": {
            "max_nodes": 200,
            "max_depth": 10,
            "max_evaluations": 20000,
            "max_characters": 65536,
            "nodes_visited": 2,
            "nodes_emitted": 1,
            "evaluations": 50,
            "characters": 7,
            "cached_characters_scanned": 4,
            "cached_lines_scanned": 1,
            "truncated": False,
            "reasons": [],
        },
        "limitations": [],
    }


def decoder():
    return importlib.import_module(
        "models.ugui_tmp_layout_diagnostics"
    ).validate_ugui_tmp_layout_diagnostics


def test_observed_and_redacted_are_closed_distinct_contracts():
    assert decoder()(sample()) == sample()
    redacted = sample("redacted")
    redacted["traversal"].update(cached_characters_scanned=0, cached_lines_scanned=0)
    assert decoder()(redacted) == redacted


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "raw_text",
        "unknown_metric",
        "redacted_metric",
        "nan",
        "over_nodes",
        "over_work",
        "over_scan",
        "overflow_contradiction",
        "bounds",
        "duplicate_id",
        "metadata",
        "status",
        "astral_metadata",
        "root_identity",
        "noncanonical_path",
    ],
)
def test_contradictory_or_unbounded_peer_data_is_refused(change):
    data = sample()
    node = data["nodes"][0]
    if change == "version":
        data["schema_version"] = 2
    elif change == "raw_text":
        node["text"] = "PRIVATE_SENTINEL"
    elif change == "unknown_metric":
        node.update(status="unknown", reason="dirty_layout")
    elif change == "redacted_metric":
        node.update(status="redacted", reason="input_owned")
    elif change == "nan":
        node["base_font_size"] = float("nan")
    elif change == "over_nodes":
        data["traversal"]["nodes_visited"] = 201
    elif change == "over_work":
        data["traversal"]["evaluations"] = 20001
    elif change == "over_scan":
        data["traversal"]["cached_characters_scanned"] = 16385
    elif change == "overflow_contradiction":
        node["first_overflow_index"] = 2
    elif change == "bounds":
        node["ink_bounds"][2] = -1.0
    elif change == "duplicate_id":
        data["nodes"].append(copy.deepcopy(node))
    elif change == "metadata":
        node["name"] = "x" * 257
    elif change == "astral_metadata":
        node["name"] = "\U0001f600" * 200
        data["traversal"]["characters"] = 402
    elif change == "root_identity":
        node["diagnostic_path"] = ""
        data["traversal"]["characters"] = 5
    elif change == "noncanonical_path":
        node["diagnostic_path"] = "/00"
        data["traversal"]["characters"] = 8
    else:
        data["status"] = "hypothetical"
    with pytest.raises(ValueError, match="^unsupported_text_response$"):
        decoder()(data)


@pytest.mark.parametrize("reason", ["dirty_layout", "unsupported_overflow_mode"])
def test_partial_unknown_is_not_claimed_complete(reason):
    data = sample("unknown")
    data["nodes"][0]["reason"] = reason
    data["status"] = "partial"
    data["traversal"].update(cached_characters_scanned=0, cached_lines_scanned=0)
    assert decoder()(data) == data
    data["status"] = "complete"
    with pytest.raises(ValueError, match="^unsupported_text_response$"):
        decoder()(data)
