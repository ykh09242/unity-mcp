"""Canonical declarations describe visible forwarding selectors without widening their contract."""

from types import SimpleNamespace

import pytest

from services.registry import action_catalog, semantic_operations as ranking, task_search
from services.registry.action_guidance_types import ActionGuidance, ToolGuidance


def test_new_forwarder_gets_source_meaning_and_keeps_its_own_parameters(monkeypatch):
    source, adapter = "sample_vault", "vault_doors"
    edit = ActionGuidance("Seal a vault door.", required=("master_key",), intent="edit")
    monkeypatch.setitem(
        action_catalog.TOOL_GUIDANCE, source, ToolGuidance(actions={"seal": edit, "erase": edit})
    )
    monkeypatch.setitem(
        action_catalog._CAPABILITIES, adapter, SimpleNamespace(source=source, actions=("seal",))
    )
    entry = {"name": adapter, "description": "Use the selected vault door.", "kwargs": {}}
    inherited = action_catalog.action_guidance(entry, "seal")
    assert (inherited.purpose, inherited.intent, inherited.required) == (edit.purpose, "edit", ())
    assert action_catalog.action_guidance(entry, "erase") is None
    assert action_catalog.action_guidance(entry, "seal", inherit=False) is None
    entry["input_schema"] = {
        "oneOf": [
            {
                "properties": {"action": {"const": "seal"}},
                "required": ["action", "door"],
                "description": "Seal the selected door.",
            }
        ]
    }
    wire = action_catalog.action_guidance(entry, "seal")
    assert (wire.purpose, wire.intent, wire.required) == (
        "Seal the selected door.",
        "edit",
        ("door",),
    )


def test_explicit_action_resolves_the_visible_binding_without_adding_its_owner():
    owned = ranking.Operation("archive", "restore_entry", "Restore an archived entry.", ())
    other = ranking.Operation("archive", "erase_entry", "Erase the chosen entry.", ())
    restore = ("tool", "archive_editor", "restore_entry")
    erase = ("tool", "archive_editor", "erase_entry")
    unrelated = ("tool", "browser", "")
    catalog = {
        restore: ("Restore the entry.",),
        erase: ("Erase the entry.",),
        unrelated: ("Open a browser.",),
    }

    def local(query, candidates):
        assert list(candidates) == [restore]
        return "bundle", {restore: 0.48}

    baseline = "bundle", {erase: 0.5, unrelated: 0.47}
    _, result = ranking.rank_operations(
        "Restore the archive entry.", catalog, {restore: owned, erase: other}, baseline, local
    )
    assert max(result, key=result.__getitem__) == restore
    assert set(result) <= catalog.keys() and result[unrelated] == 0.47


@pytest.mark.parametrize("operation", ["close_scene", "create", "move_to_scene"])
def test_focused_scene_edits_survive_the_explicit_intent_filter(operation):
    name = "scene_lifecycle"
    item = {"name": name, "group": "core", "description": "", "kwargs": {}}
    response = task_search.search_catalog(
        task_search.SearchRequest(query=operation, intent="edit", route_surfaces="tools"),
        [item],
        visible_names={name},
    )
    first = response["route_groups"][0]["representative"]
    assert (first["tool"], first["operation"], first["intent"]) == (name, operation, "edit")
