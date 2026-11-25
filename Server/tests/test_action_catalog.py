"""Complete action discovery is independent of query wording and editor access."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.registry import action_catalog, task_search
from services.registry.action_guidance_types import ActionGuidance, ToolGuidance


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


def test_scene_inventory_and_activation_expose_distinct_action_contracts(entries):
    from services.registry.action_catalog import action_card
    from services.registry.task_search import SearchRequest, search_catalog

    by_name = {entry["name"]: entry for entry in entries}
    focused = action_card(by_name["scene_inspect"], "get_loaded_scenes")
    dispatcher = action_card(by_name["manage_scene"], "get_loaded_scenes")
    activate = action_card(by_name["manage_scene"], "set_active_scene")
    focused_activate = action_card(by_name["scene_lifecycle"], "set_active_scene")

    assert focused["intent"] == dispatcher["intent"] == "observe"
    assert focused["purpose"] == dispatcher["purpose"]
    assert activate["intent"] == focused_activate["intent"] == "edit"
    assert activate["purpose"] in focused_activate["purpose"]
    assert activate["purpose"] != dispatcher["purpose"]
    for name in ("scene_lifecycle", "manage_scene"):
        result = search_catalog(
            SearchRequest(query="set_active_scene", intent="edit"), [by_name[name]]
        )
        route = result["route_groups"][0]["representative"]
        assert (route["tool"], route["operation"]) == (name, "set_active_scene")


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
        ("manage_localization", "set_string_entry", "exactly one of key_id or key_name", "value"),
        ("manage_localization", "set_fallbacks", "empty list", "fallback_locale_paths"),
        ("execute_menu_item", "inspect", "mcpforunity://menu-items", "menu_path"),
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


@pytest.mark.parametrize(
    ("tool", "reads"),
    [
        (
            "manage_addressables",
            {
                "capabilities",
                "inspect",
                "preflight",
                "analyze_layout",
                "prefab_status",
                "reconcile_prefab",
            },
        ),
        ("manage_project_auditor", {"availability", "status", "issues"}),
        ("manage_localization", {"capabilities", "list_locales", "list_tables", "inspect"}),
        (
            "manage_navigation",
            {"capabilities", "list", "read", "bake_status", "sample_position", "calculate_path"},
        ),
        ("manage_sprite_atlas", {"capabilities", "read"}),
        ("manage_timeline", {"capabilities", "read", "read_director"}),
        (
            "manage_packages",
            {
                "list_packages",
                "search_packages",
                "get_package_info",
                "ping",
                "status",
                "list_registries",
            },
        ),
        ("execute_menu_item", {"inspect", "exists", "evaluate_availability"}),
        ("import_model", {"search", "preview", "status", "list_providers"}),
        ("manage_recording", {"capabilities", "status"}),
        (
            "manage_play_scenario",
            {
                "get",
                "list",
                "status",
                "reports",
                "suite_get",
                "suite_list",
                "suite_status",
                "suite_reports",
            },
        ),
    ],
)
def test_reviewed_dispatchers_expose_only_their_observation_actions(entries, tool, reads):
    from services.registry.action_catalog import CatalogRequest, browse_catalog

    page = browse_catalog(CatalogRequest(tool=tool, intent="observe"), entries)
    assert {card["action"] for card in page["actions"]} == reads
    assert all(card["purpose_source"] == "declared" for card in page["actions"])


def test_operation_intent_preserves_callback_and_preview_effects(entries):
    from services.registry.action_catalog import action_card

    by_name = {entry["name"]: entry for entry in entries}
    menu = by_name["execute_menu_item"]
    assert action_card(menu, "inspect")["effects"]["readOnlyHint"]
    evaluated = action_card(menu, "evaluate_availability")
    assert evaluated["intent"] == "observe" and not evaluated["effects"]["readOnlyHint"]
    assert "callback" in evaluated["purpose"]
    preview = action_card(by_name["manage_timeline"], "preview_start")
    assert preview["intent"] == "interact" and not preview["effects"]["readOnlyHint"]
    assert "expected_director_revision" in preview["required_parameters"]
    unsupported = action_card(by_name["manage_localization"], "set_asset_entry")
    assert "refus" in unsupported["purpose"].lower()


@pytest.mark.parametrize(
    ("tool", "operation", "value", "field"),
    [
        ("manage_navigation", "capabilities", "navigation.package_available", "package_available"),
        (
            "manage_project_auditor",
            "availability",
            "project_auditor.api_supported",
            "api_supported",
        ),
    ],
)
def test_optional_capabilities_report_only_observed_booleans(
    entries, tool, operation, value, field
):
    from services.registry.action_catalog import action_card
    from services.registry.observation_predicates import evaluate_observation
    from services.registry.task_search import SearchRequest, search_catalog

    query = SearchRequest(
        observation_source="editor_live", required_values=[value], intent="observe"
    )
    for wording in ("지원 여부", "¿Está disponible?", "対応していますか"):
        result = search_catalog(query.model_copy(update={"query": wording}), entries)
        assert [(route["tool"], route["operation"]) for route in result["routes"]] == [
            (tool, operation)
        ]
    card = action_card(next(e for e in entries if e["name"] == tool), operation)
    for data, expected in (
        ({field: False}, True),
        ({field: True}, True),
        ({field: 0}, False),
        ({}, False),
    ):
        assert evaluate_observation(
            card["observation"], {"success": True, "data": data}, [value]
        ) == {value: expected}


def test_asset_discovery_distinguishes_import_load_and_reference_index(entries):
    from services.registry.action_catalog import action_card, tool_capability

    by_name = {entry["name"]: entry for entry in entries}
    for name in ("asset_inspect", "manage_asset"):
        search = action_card(by_name[name], "search")
        assert search["intent"] == "observe"
        assert search["effects"]["readOnlyHint"] is False
        assert "metadata_only" in search["purpose"]
        assert "import" in search["evidence_limit"]
    labels = action_card(by_name["asset_labels"], "get_labels")
    assert labels["intent"] == "observe" and not labels["effects"]["readOnlyHint"]
    assert "load" in labels["evidence_limit"]
    for action in ("preview_move", "get_bundle_assets"):
        assert action_card(by_name["manage_asset"], action)["effects"]["readOnlyHint"]
    for name in ("manage_asset", "asset_reference_diagnostics"):
        forward = action_card(by_name[name], "get_dependencies")
        assert "outside scan_root" in forward["evidence_limit"]
        for action in ("get_reverse_references", "preview_move", "preview_delete"):
            assert (
                "candidates inside scan_root"
                in action_card(by_name[name], action)["evidence_limit"]
            )
    assert not tool_capability(by_name["manage_asset"])["effects"]["readOnlyHint"]
    write = action_card(by_name["manage_asset"], "set_labels")
    assert "revision" in write["purpose"] and write["intent"] == "edit"
    assert write["required_parameters"] == ["labels"]


def test_camera_discovery_separates_package_presence_existing_brain_and_attachment(entries):
    from services.registry.action_catalog import CatalogRequest, action_card, browse_catalog

    by_name = {entry["name"]: entry for entry in entries}
    for name in ("manage_camera", "manage_cinemachine"):
        reads = browse_catalog(CatalogRequest(tool=name, intent="observe"), entries)["actions"]
        assert {"ping", "get_brain_status"} <= {row["action"] for row in reads}
        assert "ensure_brain" not in {row["action"] for row in reads}
        assert action_card(by_name[name], "ping")["effects"]["readOnlyHint"]
        status = action_card(by_name[name], "get_brain_status")
        assert "absent" in status["purpose"] and "blend" in status["purpose"]
        ensure = action_card(by_name[name], "ensure_brain")
        assert "properties.camera" in ensure["purpose"] and not ensure["effects"]["readOnlyHint"]
    capture = action_card(by_name["capture_camera"], "screenshot_multiview")
    assert not capture["effects"]["readOnlyHint"] and "PNG" in capture["purpose"]
    assert "without saving a PNG" in capture["evidence_limit"]
    assert "temporary camera" in capture["evidence_limit"]


def test_ui_discovery_separates_file_panel_and_live_edits(entries):
    from services.registry.action_catalog import action_card

    by_name = {entry["name"]: entry for entry in entries}
    for name in ("manage_ui", "ui_source"):
        read = action_card(by_name[name], "read")
        assert read["effects"]["readOnlyHint"] and "saved" in read["purpose"]
        patch = action_card(by_name[name], "patch_source")
        assert "preview" in patch["purpose"] and "expected_source_hash" in patch["purpose"]
        assert not patch["effects"]["readOnlyHint"]
        assert "expected_source_hash" not in patch["required_parameters"]
    tree = action_card(by_name["ui_inspect"], "get_visual_tree")
    assert tree["intent"] == "observe" and not tree["effects"]["readOnlyHint"]
    assert "callback" in tree["evidence_limit"] and "not_initialized" in tree["evidence_limit"]
    edit = action_card(by_name["ui_element"], "modify_visual_element")
    assert "live" in edit["purpose"] and not edit["effects"]["readOnlyHint"]
    attach = action_card(by_name["ui_document"], "attach_ui_document")
    assert "PanelSettings" in attach["purpose"] and attach["intent"] == "edit"


def test_camera_inventory_search_offers_the_published_resource_to_read_only_clients(entries):
    from services.registry import get_registered_resources
    from services.registry.action_catalog import action_card, tool_capability
    from services.registry.task_search import SearchRequest, search_catalog
    from services.resources import cameras

    uri = "mcpforunity://scene/cameras"
    resources = [row for row in get_registered_resources() if row["func"] is cameras.get_cameras]
    assert [row["uri"] for row in resources] == [uri]
    names = {"manage_camera", "manage_cameras"}
    for entry in (row for row in entries if row["name"] in names):
        card = action_card(entry, "list_cameras")
        assert uri in card["purpose"] and uri in tool_capability(entry)["description"]
        assert "inspect_scene_cameras" in card["purpose"]
        assert "inspect_scene_cameras" in tool_capability(entry)["description"]
        assert not card["effects"]["readOnlyHint"]
    result = search_catalog(
        SearchRequest(query="camera inventory", intent="observe"),
        entries,
        resources=resources,
        visible_names=names,
    )
    group = next(g for g in result["route_groups"] if g["representative"].get("uri") == uri)
    assert {row["tool"] for row in group["alternatives"]} == names
    reads = search_catalog(
        SearchRequest(
            required_values=["unityCameras"], route_surfaces="resources", host_tool_names=[]
        ),
        entries,
        resources=resources,
        visible_names=names,
    )
    assert [g["representative"].get("uri") for g in reads["route_groups"]] == [uri]
    assert "unityCameras" in reads["route_groups"][0]["representative"]["observation"]["values"]


def test_focused_editor_actions_share_the_reviewed_native_contract(entries):
    from services.registry.action_catalog import action_card, declared_actions
    from services.registry.capability_catalog import CAPABILITIES

    by_name = {entry["name"]: entry for entry in entries}
    for capability in CAPABILITIES:
        if capability.source not in {"manage_asset", "manage_camera", "manage_ui"}:
            continue
        for action in declared_actions(by_name[capability.name]):
            focused = action_card(by_name[capability.name], action)
            legacy = action_card(by_name[capability.source], action)
            assert focused["purpose_source"] == "declared"
            assert focused["effects_scope"] == "action"
            for key in ("purpose", "intent", "required_parameters", "effects", "evidence_limit"):
                assert focused.get(key) == legacy.get(key), (capability.name, action, key)


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


def test_build_settings_card_distinguishes_project_and_named_target_properties(entries):
    from services.registry.action_catalog import action_card

    entry = next(entry for entry in entries if entry["name"] == "manage_build")
    settings = action_card(entry, "settings")
    assert (
        "product_name, company_name and version are project-wide PlayerSettings"
        in settings["purpose"]
    )
    assert (
        "bundle_id, scripting_backend, defines and architecture use the selected named build target"
        in settings["purpose"]
    )
    assert settings["required_parameters"] == ["property"]
    assert settings["effects"]["readOnlyHint"] is False


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


def test_reference_graph_cards_expose_nested_inputs_used_by_execution(entries):
    from jsonschema import Draft202012Validator
    from services.registry.action_catalog import action_card, declared_actions, describe_action

    entry = next(entry for entry in entries if entry["name"] == "manage_reference_graph")
    for action in declared_actions(entry):
        assert "options_schema" in action_card(entry, action)

    for name, required in (
        ("query", ["node"]),
        ("path", ["node", "target"]),
        ("diff", ["before", "after"]),
    ):
        card = action_card(entry, name)
        assert card["required_parameters"] == ["options"]
        assert card["options_schema"]["required"] == required
        assert "cursor" in card["options_schema"]["properties"]
        assert card["options_schema"]["additionalProperties"] is False

    mutation = action_card(entry, "begin_mutation")["options_schema"]
    assert mutation["oneOf"] == [{"required": ["paths"]}, {"required": ["guids"]}]
    mutation_validator = Draft202012Validator(mutation)
    assert mutation_validator.is_valid({"guids": ["a" * 32]})
    assert not mutation_validator.is_valid({"guids": [], "paths": []})
    source = Draft202012Validator(action_card(entry, "set_source")["options_schema"])
    version = {"id": "settings", "expected_version": 1}
    assert source.is_valid({**version, "enabled": False})
    assert not source.is_valid(version)
    assert source.is_valid({**version, "config": {"root": "/project", "include": ["*.json"]}})
    descriptor = {
        "inputSchema": {"properties": {"action": {}, "options": {"description": "Graph options"}}}
    }
    bound = describe_action(entry, descriptor, "path")["tool"]["inputSchema"]
    assert "options" in bound["required"]
    assert bound["properties"]["options"]["required"] == ["node", "target"]
    assert bound["properties"]["options"]["description"] == "Graph options"
    assert descriptor["inputSchema"]["properties"]["options"] == {"description": "Graph options"}
    assert (
        "options"
        not in describe_action(entry, descriptor, "runtime_start")["tool"]["inputSchema"][
            "required"
        ]
    )


def test_all_guidance_matches_registered_actions_and_parameters(entries):
    from services.registry.action_catalog import TOOL_GUIDANCE, declared_actions

    by_name = {entry["name"]: entry for entry in entries}
    for name, guidance in TOOL_GUIDANCE.items():
        entry = by_name[name]
        assert guidance.actions.keys() <= set(declared_actions(entry))
        parameters = inspect.signature(entry["func"]).parameters
        for action in guidance.actions.values():
            assert set(action.required) <= parameters.keys()


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


def test_forwarded_actions_preserve_admitted_requirements_and_evidence_limits(entries):
    by_name = {entry["name"]: entry for entry in entries}
    for action, parameter in (("create", "name"), ("move_to_scene", "target")):
        card = action_catalog.action_card(by_name["scene_lifecycle"], action)
        assert parameter in card["required_parameters"]
    for focused, action in (("scene_inspect", "get_hierarchy"), ("scene_validate", "validate")):
        source = action_catalog.operation_owner(focused, action)
        expected = action_catalog.action_card(by_name[source], action)["evidence_limit"]
        assert action_catalog.action_card(by_name[focused], action)["evidence_limit"] == expected


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
