"""Build calls using the current connection's public schema, before Unity dispatch."""

import json

import pytest
from fastmcp import Client

from core.config import config


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
            ("manage_tools", {"action": "catalog", "query": "camera", "group": "core"}, {}),
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
            assert Draft202012Validator(schema).is_valid(
                {"action": "get_hierarchy", "target": 17, "strict_read": False}
            )

        advertised = {t.name: t.input_schema for t in await client.list_tools()}
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
