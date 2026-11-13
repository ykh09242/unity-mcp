"""Complete action discovery is independent of query wording and editor access."""

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def entries():
    import services.tools as package
    from services.registry import get_registered_tools
    from utils.module_discovery import discover_modules

    list(discover_modules(Path(package.__file__).parent, package.__name__))
    return get_registered_tools()


def test_catalog_traversal_covers_every_registered_tool_and_action(entries):
    from services.registry.action_catalog import CatalogRequest, browse_catalog, declared_actions

    index = browse_catalog(CatalogRequest(), entries)
    assert index["selection"] == "agent"
    assert index["scope"] == "local_catalog"
    assert {name for group in index["groups"] for name in group["tools"]} == {
        entry["name"] for entry in entries
    }
    observed = set()
    for entry in entries:
        offset = 0
        while offset is not None:
            page = browse_catalog(
                CatalogRequest(tool=entry["name"], offset=offset, limit=3), entries
            )
            assert page["tool"]["advertised"] is None
            assert page["tool"]["readiness"] == "unobserved"
            assert len(page["actions"]) <= 3
            for action in page["actions"]:
                key = (action["tool"], action["action"])
                assert key not in observed
                observed.add(key)
                assert action["purpose"]
                assert action["invoke"] == {
                    "tool": entry["name"],
                    "arguments": {"action": action["action"]} if action["action"] else {},
                }
            offset = page["next_offset"]
    expected = {
        (entry["name"], action)
        for entry in entries
        for action in (declared_actions(entry) or [None])
    }
    assert observed == expected


def test_group_pages_are_stable_and_keep_unadvertised_tools(entries):
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    request = CatalogRequest(group="core", limit=3)
    first = browse_catalog(request, entries, visible_names=set())
    reverse = browse_catalog(request, list(reversed(entries)), visible_names=set())
    assert first == reverse
    assert first["next_offset"] == 3
    assert all(tool["advertised"] is False for tool in first["tools"])
    assert first["scope"] == "mcp_context"


@pytest.mark.parametrize("past_end", [False, True])
def test_tool_pages_construct_only_selected_action_cards(entries, past_end):
    from services.registry import action_catalog

    entry = next(entry for entry in entries if entry["name"] == "manage_vfx")
    actions = action_catalog.declared_actions(entry)
    offset = len(actions) + 1 if past_end else 1
    with patch.object(action_catalog, "action_card", wraps=action_catalog.action_card) as cards:
        page = action_catalog.browse_catalog(
            action_catalog.CatalogRequest(tool=entry["name"], offset=offset, limit=1), entries
        )
    assert cards.call_count == (0 if past_end else 1)
    assert page["total"] == len(actions)
    assert page["offset"] == offset
    assert page["next_offset"] == (None if past_end else offset + 1)
    assert [card["action"] for card in page["actions"]] == actions[offset : offset + 1]
    assert page["tool"]["actions"] == actions
    assert page["tool"]["description"] == action_catalog.tool_description(entry)


@pytest.mark.parametrize("past_end", [False, True])
def test_group_pages_construct_only_selected_tool_summaries(entries, past_end):
    from services.registry import action_catalog

    selected = sorted(
        (entry for entry in entries if entry.get("group") == "core"),
        key=lambda entry: entry["name"],
    )
    offset = len(selected) + 1 if past_end else 1
    with patch.object(
        action_catalog, "tool_capability", wraps=action_catalog.tool_capability
    ) as summaries:
        page = action_catalog.browse_catalog(
            action_catalog.CatalogRequest(group="core", offset=offset, limit=1),
            list(reversed(entries)),
            visible_names=set(),
        )
    assert summaries.call_count == (0 if past_end else 1)
    assert page["total"] == len(selected)
    assert page["offset"] == offset
    assert page["next_offset"] == (None if past_end else offset + 1)
    assert [tool["name"] for tool in page["tools"]] == [
        entry["name"] for entry in selected[offset : offset + 1]
    ]
    assert all(tool["advertised"] is False for tool in page["tools"])


def test_empty_group_and_past_end_actionless_tool_remain_valid(entries):
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    empty = browse_catalog(CatalogRequest(group="core", limit=1), [])
    assert empty["total"] == 0
    assert empty["tools"] == []
    assert empty["next_offset"] is None
    actionless = browse_catalog(CatalogRequest(tool="capture_prefab", offset=1, limit=1), entries)
    assert actionless["total"] == 1
    assert actionless["actions"] == []
    assert actionless["next_offset"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"group": "core", "tool": "manage_shader"},
        {"tool": " "},
        {"offset": 1},
        {"tool": "manage_shader", "offset": -1},
        {"tool": "manage_shader", "offset": True},
        {"limit": 0},
        {"limit": 21},
    ],
)
def test_catalog_rejects_invalid_selectors(arguments):
    from pydantic import ValidationError
    from services.registry.action_catalog import CatalogRequest

    with pytest.raises(ValidationError):
        CatalogRequest(**arguments)


@pytest.mark.parametrize("selector", [{"tool": "invented"}, {"group": "invented"}])
def test_unknown_catalog_selection_is_an_error(entries, selector):
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    with pytest.raises(ValueError, match="Unknown"):
        browse_catalog(CatalogRequest(**selector), entries)


@pytest.mark.parametrize(
    ("tool", "action", "phrase", "required"),
    [
        ("manage_shader", "inspect_graph", "nodes", "path"),
        ("manage_shader", "diagnostics", "cached", "path"),
        ("asset_reference_diagnostics", "get_reverse_references", "refer", "path"),
        ("prefab_contents", "apply_preview", "preview", "preview_id"),
        ("manage_profiler", "window_start", "frame", None),
        ("manage_ugui", "diagnose_raycast", "pointer", None),
    ],
)
def test_action_cards_explain_distinct_outcomes(entries, tool, action, phrase, required):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == tool)
    card = action_card(entry, action)
    assert phrase in card["purpose"].lower()
    assert card["purpose_source"] == "declared"
    if required:
        assert required in card["required_parameters"]


def test_read_actions_and_callback_risks_are_distinct(entries):
    from services.registry.action_catalog import action_card

    shader = next(entry for entry in entries if entry["name"] == "manage_shader")
    read = action_card(shader, "inspect_graph")
    write = action_card(shader, "update")
    assert read["effects"]["readOnlyHint"] is True
    assert read["effects_scope"] == "action"
    assert write["effects"]["readOnlyHint"] is False
    ugui = next(entry for entry in entries if entry["name"] == "manage_ugui")
    assert action_card(ugui, "diagnose_raycast")["effects"]["readOnlyHint"] is False


def test_build_plan_observation_is_distinct_from_guarded_and_unguarded_execution(entries):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == "manage_build")
    plan = action_card(entry, "plan")
    guarded = action_card(entry, "build_from_plan")
    ordinary = action_card(entry, "build")
    assert plan["effects"]["readOnlyHint"] is True
    assert plan["effects_scope"] == "action"
    assert "without saving" in plan["purpose"]
    assert "non-profile" in plan["purpose"]
    assert "not every project input" in plan["purpose"]
    assert "expected_plan_sha256" in guarded["required_parameters"]
    assert "Repeat the plan" in guarded["purpose"]
    assert "reject a mismatched hash before scheduling" in guarded["purpose"]
    assert "job_id" in guarded["purpose"]
    assert "expected_plan_sha256" not in ordinary["required_parameters"]
    assert "without a plan hash guard" in ordinary["purpose"]
    for card in (guarded, ordinary):
        assert card["purpose_source"] == "declared"
        assert card["effects"]["readOnlyHint"] is False
        assert "save assets/scenes" in card["purpose"] or "saves assets/scenes" in card["purpose"]
        assert "callbacks" in card["purpose"]


@pytest.mark.parametrize(
    ("action", "read_selector", "write_selector", "required"),
    [
        ("settings", "value is omitted", "providing value writes", ["property"]),
        ("scenes", "scenes is omitted", "empty array clears", []),
        ("profiles", "profile is omitted", "activate=true", []),
        ("clean_output", "by default", "dry_run=false deletes", ["output_path"]),
    ],
)
def test_mixed_build_actions_retain_conditional_writes_and_conservative_effects(
    entries, action, read_selector, write_selector, required
):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == "manage_build")
    card = action_card(entry, action)
    assert read_selector in card["purpose"]
    assert write_selector in card["purpose"]
    assert card["required_parameters"] == required
    assert card["effects"]["readOnlyHint"] is False
    assert card["effects_scope"] == "tool"


@pytest.mark.parametrize("tool", ["prefab_contents", "manage_prefabs"])
def test_prefab_cards_distinguish_callback_preview_from_token_preview_and_save(entries, tool):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == tool)
    edit = action_card(entry, "modify_contents")
    preview = action_card(entry, "preview_modify_contents")
    apply = action_card(entry, "apply_preview")
    assert "preview_only=true" in edit["purpose"]
    assert "returns no apply token" in edit["purpose"]
    assert "root position, rotation, scale and set_active" in preview["purpose"]
    assert "without saving" in preview["purpose"]
    assert "10 minutes" in preview["purpose"]
    assert apply["required_parameters"] == ["preview_id"]
    assert "save" in apply["purpose"]
    assert "reject drift" in apply["purpose"]
    for card in (edit, preview, apply):
        assert card["purpose_source"] == "declared"
        assert "callbacks" in card["purpose"]
        assert card["effects"]["readOnlyHint"] is False


def test_scene_cards_distinguish_property_edits_from_hierarchy_and_direct_apply(entries):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == "manage_scene_edits")
    preview = action_card(entry, "preview")
    apply = action_card(entry, "apply")
    structure = action_card(entry, "preview_structure")
    apply_structure = action_card(entry, "apply_structure")
    assert preview["required_parameters"] == ["edits"]
    assert "serialized fields" in preview["purpose"]
    assert "expected_old for every field" in apply["purpose"]
    assert apply["required_parameters"] == []  # Token and direct edits are alternatives.
    assert structure["required_parameters"] == ["target", "operations"]
    assert "Transform-only subtrees" in structure["purpose"]
    assert "create/delete/reparent" in structure["purpose"]
    assert apply_structure["required_parameters"] == ["preview_id"]
    assert "hierarchy/state drift" in apply_structure["purpose"]
    assert apply_structure["effects"]["readOnlyHint"] is False


@pytest.mark.parametrize("operation", ["move", "delete"])
def test_asset_preview_and_optional_guarded_mutation_are_explained_on_both_routes(
    entries, operation
):
    from services.registry.action_catalog import action_card

    by_name = {entry["name"]: entry for entry in entries}
    preview = action_card(by_name["asset_reference_diagnostics"], "preview_" + operation)
    mutation = action_card(by_name["asset_edit"], operation)
    for card in (preview, mutation):
        assert "reference_revision" in card["purpose"]
        assert card["purpose_source"] == "declared"
    assert "without " in preview["purpose"]
    assert "scan coverage" in preview["purpose"]
    assert "omitting it performs an unguarded" in mutation["purpose"]
    assert mutation["effects"]["readOnlyHint"] is False
    assert "reference_revision" not in mutation["required_parameters"]
    legacy_preview = action_card(by_name["manage_asset"], "preview_" + operation)
    legacy_mutation = action_card(by_name["manage_asset"], operation)
    assert legacy_preview["purpose"] == preview["purpose"]
    assert legacy_mutation["purpose"] == mutation["purpose"]
    assert legacy_preview["required_parameters"] == preview["required_parameters"]
    assert legacy_mutation["required_parameters"] == mutation["required_parameters"]


def test_search_projects_the_same_metadata_as_catalog(entries):
    from services.registry.action_catalog import tool_capability
    from services.registry.task_search import SearchRequest, search_catalog

    entry = next(entry for entry in entries if entry["name"] == "asset_reference_diagnostics")
    assert search_catalog(SearchRequest(query=entry["name"]), [entry])["results"] == [
        tool_capability(entry)
    ]


def test_describe_binds_action_without_mutating_schema(entries):
    from copy import deepcopy
    from services.registry.action_catalog import describe_action

    entry = next(entry for entry in entries if entry["name"] == "prefab_contents")
    descriptor = {
        "name": entry["name"],
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"enum": ["apply_preview", "modify_contents"]}},
            "allOf": [
                {
                    "if": {"properties": {"action": {"const": "apply_preview"}}},
                    "then": {"required": ["preview_id"]},
                }
            ],
        },
    }
    original = deepcopy(descriptor)
    result = describe_action(entry, descriptor, "apply_preview")
    assert result["tool"]["inputSchema"]["properties"]["action"]["const"] == "apply_preview"
    assert result["tool"]["inputSchema"]["allOf"] == original["inputSchema"]["allOf"]
    assert result["operation"]["invoke"]["arguments"] == {"action": "apply_preview"}
    assert descriptor == original
    with pytest.raises(ValueError, match="Unknown action"):
        describe_action(entry, descriptor, "invented")


def test_actionless_tool_has_one_callable_card(entries):
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    page = browse_catalog(CatalogRequest(tool="capture_prefab"), entries)
    assert len(page["actions"]) == 1
    card = page["actions"][0]
    assert card["action"] is None
    assert "prefab_path" in card["required_parameters"]
    assert card["effects"]["readOnlyHint"] is False


def test_all_guidance_matches_registered_actions_and_parameters(entries):
    from services.registry.action_catalog import TOOL_GUIDANCE, declared_actions

    by_name = {entry["name"]: entry for entry in entries}
    for name, guidance in TOOL_GUIDANCE.items():
        entry = by_name[name]
        assert guidance.actions.keys() <= set(declared_actions(entry))
        parameters = inspect.signature(entry["func"]).parameters
        for action in guidance.actions.values():
            assert set(action.required) <= parameters.keys()
