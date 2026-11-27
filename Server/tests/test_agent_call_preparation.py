"""Build calls using the current connection's public schema, before Unity dispatch."""

import json
from copy import deepcopy
from typing import Annotated, Literal

import pytest
from fastmcp import Client
from fastmcp.tools import FunctionTool
from jsonschema import Draft202012Validator

from core.config import config


def test_unseen_family_keeps_identical_flat_and_selected_constraints():
    from services.registry.action_contracts import (
        bind_parameter_scopes,
        conflicting_action_fields,
        input_actions,
        parameter_conditions,
        publish_parameter_scopes,
        required_for_actions,
        used_by_actions,
    )
    from services.registry.call_preparation import prepare_tool_call

    def sample(
        action: Literal["locate", "measure"],
        label: Annotated[str | None, required_for_actions("locate"), "Name in the source."] = None,
        detail: Annotated[bool | int, input_actions("measure", inactive_value=False)] = False,
        style: Annotated[str | None, used_by_actions("measure")] = None,
    ) -> dict:
        return {}

    flat = publish_parameter_scopes(FunctionTool.from_function(sample).parameters, sample)
    flat["allOf"] = parameter_conditions(flat)
    bound = deepcopy(flat)
    bind_parameter_scopes(bound, "locate")
    assert bound["properties"]["label"]["description"].endswith("Name in the source.")
    assert "default" not in bound["properties"]["label"]
    assert "Used by measure" in flat["properties"]["style"]["description"]
    assert "const" not in bound["properties"]["style"]
    assert "const" not in flat["properties"]["detail"]
    for args, allowed in [
        ({"label": "point"}, True),
        ({"label": "point", "detail": False, "style": "old"}, True),
        ({"label": "point", "detail": 0}, False),
        ({"label": None}, False),
        ({}, False),
    ]:
        draft = {"action": "locate", **args}
        assert Draft202012Validator(flat).is_valid(draft) is allowed
        assert Draft202012Validator(bound).is_valid(draft) is allowed
        result = prepare_tool_call("sample", bound, draft)
        assert result["ready"] is allowed
        if type(args.get("detail")) is int:
            assert result["action_field_conflicts"] == [
                {"field": "detail", "applies_to": ["measure"]}
            ]
    assert not conflicting_action_fields(flat, {"action": "locate", "detail": False})
    # An existing constant remains in force even when Python treats it as equal
    # to the selected neutral value (0 and false have different JSON types).
    bound["properties"]["detail"]["const"] = 0
    bind_parameter_scopes(bound, "locate")
    assert not Draft202012Validator(bound["properties"]["detail"]).is_valid(False)
    # A nullable ref need not expose a literal anyOf branch on the field.
    referenced = {
        "$defs": {"label": {"type": ["string", "null"]}},
        "properties": {
            "label": {"$ref": "#/$defs/label", "x-unity-mcp-required-for-actions": ["locate"]}
        },
    }
    bind_parameter_scopes(referenced, "locate")
    assert not Draft202012Validator(referenced).is_valid({"label": None})
    assert Draft202012Validator(referenced).is_valid({"label": "landmark"})


@pytest.mark.asyncio
async def test_publication_covers_all_declared_action_scopes(server):
    from services.registry import get_registered_tools
    from services.registry.action_catalog import declared_actions, describe_action
    from services.registry.action_contracts import required_action_parameters, scoped_parameters

    entries = {entry["name"]: entry for entry in get_registered_tools()}
    examined = set()
    async with Client(server, mode="legacy") as client:
        for definition in await client.list_tools():
            schema = definition.input_schema
            scopes = scoped_parameters(schema)
            required = required_action_parameters(schema)
            if not (scopes or required):
                continue
            entry = {**entries[definition.name], "input_schema": schema}
            for action in declared_actions(entry):
                contract = describe_action(entry, {"input_schema": schema}, action)
                selected = contract["tool"]["input_schema"]
                for name, actions in scopes.items():
                    raw = schema["properties"][name]
                    assert all(choice in raw["description"] for choice in actions)
                    if action not in actions:
                        field = selected["properties"][name]
                        assert field["const"] == raw.get("x-unity-mcp-inactive-value")
                        assert name not in contract["operation"]["input_fields"]
                for name, actions in required.items():
                    if action in actions:
                        assert name in selected["required"]
                        assert not Draft202012Validator(selected["properties"][name]).is_valid(None)
            examined.add(definition.name)
    assert len(examined) > 1


@pytest.fixture
def server(monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    return create_mcp_server(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_debug_detail_requires_an_explicit_advertised_choice(server, mode):
    marker = "caller-routing-fixture"
    meta = {"client-setting": marker}
    async with Client(server, mode=mode) as client:
        tool = next(t for t in await client.list_tools() if t.name == "debug_request_context")
        option = tool.input_schema["properties"]["detail"]
        assert option["default"] == "routing"
        assert set(option["enum"]) == {"routing", "client", "server"}
        for args in ({}, {"detail": "client"}, {"detail": "server"}):
            data = (await client.call_tool(tool.name, args, meta=meta)).structured_content["data"]
            assert (marker in str(data)) is (args.get("detail") == "client")
            assert ("cwd" in data["server"]) is (args.get("detail") == "server")
            assert data["editor_route"]["edit_authorization"] == "not_tested"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_prepare_reports_the_concrete_editor_operation_without_granting_it(server, mode):
    async with Client(server, mode=mode) as client:
        for name, draft, grant in (
            (
                "ui_source",
                {
                    "action": "patch_source",
                    "path": "Assets/Demo.uxml",
                    "source_selector": "#caption",
                    "source_changes": {"tooltip": "x"},
                },
                "read",
            ),
            (
                "ui_source",
                {
                    "action": "patch_source",
                    "path": "Assets/Demo.uxml",
                    "source_selector": "#caption",
                    "source_changes": {"tooltip": "x"},
                    "preview": False,
                    "expected_source_hash": "a" * 64,
                },
                "edit",
            ),
            ("scene_inspect", {"action": "get_active"}, "read"),
            ("get_tests", {}, "read"),
            (
                "unity_docs",
                {
                    "action": "get_package_doc",
                    "package": "com.unity.ugui",
                    "page": "index",
                    "pkg_version": "2.0",
                },
                "read",
            ),
            ("manage_tools", {"action": "read_skill"}, "read"),
        ):
            prepared = (
                await client.call_tool(
                    "manage_tools", {"action": "prepare", "tool": name, "arguments": draft}
                )
            ).structured_content
            assert prepared["ready"] and prepared["prepared_call"]["arguments"] == draft
            assert prepared["execution"] == {
                "via": "tools/call",
                "handler": "mcp_server",
                "request_unity_instance": None,
                "authorization": "checked_on_invocation",
                "required_server_permissions": [grant],
                "permission_check": {
                    "scope": "public_operation_grants",
                    "mode": "local",
                    "requester": "not_enforced",
                    "selected_target": "not_enforced",
                    "host_approval": "not_checked",
                    "editor_approval": "not_checked",
                },
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_prepared_search_is_directly_callable_on_its_original_entry_point(server, mode):
    original = "  ¿Dónde está el editor de escenas?  "
    async with Client(server, mode=mode) as client:
        for name, entry in (("search_unity_tools", {}), ("manage_tools", {"action": "search"})):
            draft = {**entry, "catalog_query": "scene", "limit": 50}
            response = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": name, "arguments": draft, "query": original},
            )
            result = response.structured_content
            assert result["ready"] is True and result["scope"] == "input_schema"
            prepared = result["prepared_call"]
            assert prepared == {
                "tool": name,
                "arguments": {**draft, "limit": 20, "query": original},
            }
            assert {item["field"] for item in result["adjustments"]} == {"query", "limit"}
            reply = await client.call_tool(prepared["tool"], prepared["arguments"])
            assert reply.structured_content["matched_query"] == "scene"


@pytest.mark.asyncio
async def test_preparation_keeps_validation_failures_and_host_filter_explicit(server):
    async with Client(server, mode="legacy") as client:
        for name, draft, extra in (
            ("search_unity_tools", {"catalog_query": "scene"}, {}),
            (
                "search_unity_tools",
                {"selection": "agent", "query": "scene", "catalog_query": "scene"},
                {},
            ),
            ("manage_tools", {"action": "search", "query": " \t"}, {}),
            ("manage_tools", {"action": "search", "query": "camera", "group": "core"}, {}),
            ("manage_tools", {"action": "search", "query": "camera", "tool": "something"}, {}),
            ("manage_tools", {"action": "search", "query": "camera", "arguments": {}}, {}),
            ("manage_tools", {"action": "search", "query": "camera", "include_schema": True}, {}),
            ("manage_tools", {"action": "search", "query": "camera", "operation": "edit"}, {}),
            ("manage_tools", {"action": "activate", "group": "ui", "limit": 100}, {}),
            (
                "manage_tools",
                {"action": "catalog", "query": "camera", "group": "core", "tool": "manage_scene"},
                {},
            ),
            ("manage_tools", {"action": "catalog", "tool": " ", "operation": "read"}, {}),
            ("manage_tools", {"action": "catalog", "tool": "x" * 129, "operation": "read"}, {}),
            ("manage_tools", {"action": "catalog", "tool": "read_console", "operation": " "}, {}),
            (
                "manage_tools",
                {"action": "catalog", "query": "camera", "selection": "agent", "intent": "edit"},
                {},
            ),
            ("search_unity_tools", {"query": "scene", "offset": 1}, {}),
            ("inspect_serialized_object", {"instance_id": 0}, {}),
            ("inspect_serialized_object", {"instance_id": 1, "mode": "value", "page_size": 80}, {}),
            ("inspect_serialized_object", {"instance_id": 1, "page_size": -2}, {}),
            ("inspect_serialized_object", {"instance_id": 1, "page_size": 4, "typo": 3}, {}),
            ("search_unity_tools", {"query": "scene"}, {"host_tool_names": []}),
            ("search_unity_tools", {"query": "scene"}, {"group": "core"}),
            ("search_unity_tools", {"query": "scene", "unity_instance": "other"}, {}),
        ):
            response = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": name, "arguments": draft, **extra},
                raise_on_error=False,
            )
            result = response.structured_content or json.loads(response.content[0].text)
            assert result["success"] is False and result["ready"] is False, (name, draft)
            assert "prepared_call" not in result


def test_preparation_only_adjusts_registered_advisory_inputs_and_resolves_local_refs():
    from services.registry.call_preparation import PAGE_SIZE, prepare_tool_call

    schema = {
        "type": "object",
        "required": ["count"],
        "properties": {
            "count": {"$ref": "#/$defs/count"},
            "page": {"type": "integer", "maximum": 3, PAGE_SIZE: True},
        },
        "$defs": {"count": {"type": "integer", "maximum": 3}},
    }
    valid = prepare_tool_call("example", schema, {"count": 1, "page": 8}, registered=True)
    assert valid["prepared_call"]["arguments"] == {"count": 1, "page": 3}
    for args, registered in (
        ({"count": 4}, True),
        ({"count": 1, "page": 4}, False),
        ({"count": True}, True),
    ):
        assert prepare_tool_call("example", schema, args, registered=registered)["ready"] is False
    remote = {"type": "object", "properties": {"id": {"$ref": "https://invalid.example/schema"}}}
    assert prepare_tool_call("example", remote, {"id": 1})["ready"] is False


@pytest.mark.asyncio
async def test_action_inputs_inherit_the_native_contract_in_both_public_bindings(server):
    async with Client(server, mode="legacy") as client:
        for name in ("manage_scene", "scene_lifecycle"):
            binding = {"action": "prepare", "tool": name, "operation": "set_active_scene"}
            base = {"scene_path": "Assets/Scenes/Example.unity"}
            invalid = await client.call_tool(
                "manage_tools",
                {**binding, "arguments": {**base, "scene_handle": 7}},
                raise_on_error=False,
            )
            denied = invalid.structured_content or json.loads(invalid.content[0].text)
            assert denied["ready"] is False and "prepared_call" not in denied
            description = await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": name, "operation": "set_active_scene"},
            )
            assert set(description.structured_content["operation"]["forbidden_parameters"]) >= {
                "scene_handle",
                "verify",
            }
            catalog = await client.call_tool(
                "manage_tools", {"action": "catalog", "tool": name, "limit": 20}
            )
            card = next(
                row
                for row in catalog.structured_content["actions"]
                if row["action"] == "set_active_scene"
            )
            assert "scene_handle" in card["forbidden_parameters"]
            valid = await client.call_tool("manage_tools", {**binding, "arguments": base})
            assert valid.structured_content["prepared_call"]["arguments"] == {
                **base,
                "action": "set_active_scene",
            }
            valid = await client.call_tool(
                "manage_tools", {**binding, "operation": "save", "arguments": {"scene_handle": 7}}
            )
            assert valid.structured_content["ready"] is True


@pytest.mark.asyncio
async def test_standard_pagination_uses_the_visible_tools_own_limit(server):
    async with Client(server, mode="legacy") as client:
        for name, arguments, cap in (
            ("get_tests", {}, 200),
            ("get_test_job", {"job_id": "example", "include_details": True}, 100),
        ):
            result = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": name, "arguments": {**arguments, "page_size": 900}},
            )
            prepared = result.structured_content
            assert prepared["prepared_call"]["arguments"] == {**arguments, "page_size": cap}
            assert prepared["adjustments"] == [
                {"field": "page_size", "source": "page_maximum", "value": cap}
            ]


@pytest.mark.asyncio
async def test_agent_directory_provides_followups_for_the_same_scope(server):
    async with Client(server, mode="legacy") as client:
        for name, entry in (("manage_tools", {"action": "search"}), ("search_unity_tools", {})):
            original = "  Which resource is available?  "
            arguments = {
                **entry,
                "selection": "agent",
                "query": original,
                "limit": 1,
                "host_tool_names": [],
                "route_surfaces": "resources",
            }
            first = (await client.call_tool(name, arguments)).structured_content
            assert "selectable_catalog" not in first
            assert first["next_call"] == {"tool": name, "arguments": {**arguments, "offset": 1}}
            assert (
                await client.call_tool(name, first["next_call"]["arguments"])
            ).structured_content["offset"] == 1
            open_family = first["open_family"]
            bound = {**open_family["arguments"], "family": first["families"][0]["family"]}
            assert open_family["tool"] == name and bound["host_tool_names"] == []
            page = (await client.call_tool(name, bound)).structured_content
            assert page["operations"][0]["kind"] in {"resource", "resource_template"}
        directory = (
            await client.call_tool("search_unity_tools", {"selection": "agent", "query": original})
        ).structured_content
        assert directory["selectable_catalog"] == {
            "tool": "manage_tools",
            "arguments": {"action": "catalog"},
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_family_operations_offer_callable_input_contracts(server, mode):
    from jsonschema import Draft202012Validator

    async with Client(server, mode=mode) as client:
        for search, entry in (("search_unity_tools", {}), ("manage_tools", {"action": "search"})):
            page = (
                await client.call_tool(
                    search, {**entry, "selection": "agent", "family": "manage_ugui", "limit": 20}
                )
            ).structured_content
            row = next(item for item in page["operations"] if item["action"] == "get_hierarchy")
            describe = row["describe_call"]
            result = (
                await client.call_tool(describe["tool"], describe["arguments"])
            ).structured_content
            assert result["operation"]["action"] == "get_hierarchy"
            assert "target" in result["operation"]["required_parameters"]
            assert set(result["operation"]["optional_parameters"]) == {
                "include_inactive",
                "max_nodes",
            }
            schema = result["tool"]["input_schema"]
            assert schema["properties"]["max_depth"]["const"] is None
            assert schema["properties"]["strict_read"]["const"] is False
            assert "Omit for get_hierarchy" in schema["properties"]["max_depth"]["description"]
            assert Draft202012Validator(schema).is_valid(
                {"action": "get_hierarchy", "target": 17, "strict_read": False}
            )

        advertised = {t.name: t.input_schema for t in await client.list_tools()}
        depth = advertised["manage_ugui"]["properties"]["max_depth"]
        depth_actions = depth["x-unity-mcp-input-actions"]
        assert f"Set only for {', '.join(depth_actions)}" in depth["description"]
        assert "const" not in depth
        hierarchy = {"action": "get_hierarchy", "target": 17}
        for draft in (
            {"action": "get_hierarchy"},
            {**hierarchy, "strict_read": True},
            {**hierarchy, "max_depth": 8},
        ):
            assert not Draft202012Validator(advertised["manage_ugui"]).is_valid(draft)
            prepared = await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": "manage_ugui", "arguments": draft},
                raise_on_error=False,
            )
            assert prepared.structured_content["ready"] is False
            if "max_depth" in draft:
                assert prepared.structured_content["action_field_conflicts"] == [
                    {"field": "max_depth", "applies_to": depth_actions}
                ]
                response = await client.call_tool("manage_ugui", draft, raise_on_error=False)
                assert response.is_error
                assert response.structured_content["message"] == "invalid_projection_request"
                assert (
                    f"max_depth applies only to {', '.join(depth_actions)}"
                    in response.structured_content["hint"]
                )
        filtered = await client.call_tool(
            "search_unity_tools",
            {"selection": "agent", "family": "manage_ugui", "host_tool_names": ["manage_ugui"]},
        )
        assert all("describe_call" not in row for row in filtered.structured_content["operations"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "operation", "required", "optional", "bad_draft"),
    [
        (
            "manage_ugui",
            "create_control",
            {"parent", "name", "element_type"},
            {"properties", "include_inactive"},
            {},
        ),
        (
            "manage_audio",
            "read_import_settings",
            {"target"},
            {"platform"},
            {"target": "Assets/Test.wav", "settings": {"preload_audio_data": False}},
        ),
        (
            "manage_material",
            "set_render_state",
            {"mode", "render_state"},
            {"material_path", "target", "slot", "search_method"},
            {"material_path": "Assets/Test.mat"},
        ),
    ],
)
async def test_describe_and_prepare_share_the_selected_operations_inputs(
    server, tool, operation, required, optional, bad_draft
):
    async with Client(server, mode="legacy") as client:
        selector = {"tool": tool, "operation": operation}
        description = await client.call_tool("manage_tools", {"action": "describe", **selector})
        card = description.structured_content["operation"]
        assert required <= set(card["required_parameters"])
        assert set(card["optional_parameters"]) == optional
        prepared = await client.call_tool(
            "manage_tools",
            {"action": "prepare", **selector, "arguments": bad_draft},
            raise_on_error=False,
        )
        assert prepared.structured_content["ready"] is False
