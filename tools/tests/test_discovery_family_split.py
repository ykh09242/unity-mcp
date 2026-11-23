"""Exercise calibration isolation independently of any multilingual ranking result."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.discovery_evaluation_fixture import Request
from tools.experiments.discovery_family_split import partition


def test_positive_and_negative_declarations_and_query_duplicates_are_withheld_together():
    keys = (
        ("tool", "get_game_view_size", ""),
        ("tool", "manage_editor", "set_game_view_size"),
        ("resource", "mcpforunity://editor/game-view-size", ""),
        ("resource", "mcpforunity://project/layers", ""),
        ("tool", "inspect_editor_state", ""),
        ("tool", "other", "alias"),
        ("tool", "other", "second_alias"),
        ("tool", "other", "read"),
    )
    documents = {key: (key[1],) for key in keys}
    read = Request("und", "read", "look up", frozenset({"other.read"}), frozenset(), None)
    mixed = replace(
        read,
        query="shared positive",
        expected=frozenset({"other.alias", "mcpforunity://project/layers"}),
    )
    cross_owner = replace(
        read, query="other view", expected=frozenset({"other.second_alias", "other.alias"})
    )
    wrong = replace(read, query="Ａ  query", wrong_effect=frozenset({"manage_editor.add_layer"}))
    requests = (read, mixed, cross_owner, wrong, replace(read, query="a Query"))

    retained, eligible, held = partition(documents, requests, "manage_editor")

    assert tuple(retained) == (keys[-1],)
    assert eligible == (read,)
    assert {"other.second_alias", "mcpforunity://editor/state", "manage_editor.add_layer"} <= held
    assert partition(documents, requests) == (documents, requests, frozenset())


def test_forwarded_and_hidden_scene_selectors_share_the_split():
    documents = {
        ("tool", "scene_lifecycle", "set_active_scene"): ("focused",),
        ("tool", "scene_inspect", "get_active"): ("focused read",),
        ("tool", "read_console", "get"): ("console",),
    }
    request = Request("und", "read", "query", frozenset({"read_console.get"}), frozenset(), None)
    wrong = replace(request, query="mixed", wrong_effect=frozenset({"manage_scene.close_scene"}))
    kept, cases, _ = partition(documents, (request, wrong), "manage_scene")
    assert list(kept) == [("tool", "read_console", "get")]
    assert cases == (request,)
    with pytest.raises(ValueError, match="Unknown calibration family"):
        partition(documents, (request,), "missing")


def test_an_additional_registered_owner_needs_no_benchmark_family_definition():
    documents = {("tool", name, "list"): (name,) for name in ("first", "another")}
    requests = tuple(
        Request("und", "read", route, frozenset({route}), frozenset(), None)
        for route in ("first.list", "another.list")
    )
    kept, cases, _ = partition(documents, requests, "another")
    assert list(kept) == [("tool", "first", "list")]
    assert cases == requests[:1]
