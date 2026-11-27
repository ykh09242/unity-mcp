"""Describe and prepare exact input choices through the public MCP tool."""

from copy import deepcopy
from typing import Literal

import pytest
from fastmcp import Client
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from jsonschema import Draft202012Validator


@pytest.fixture
def server(monkeypatch):
    from core.config import config
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    return create_mcp_server(False)


async def _manage(client, **arguments):
    return (
        await client.call_tool("manage_tools", arguments, raise_on_error=False)
    ).structured_content


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_serialized_object_fields_description_prepares_the_real_input_field(server, mode):
    async with Client(server, mode=mode) as client:
        name = "inspect_serialized_object"
        advertised = next(
            tool.input_schema for tool in await client.list_tools() if tool.name == name
        )
        unbound = await _manage(client, action="describe", tool=name)
        assert unbound["operation"]["action"] is None
        assert unbound["tool"]["input_schema"] == advertised
        assert unbound["input_choices"]["mode"] == ["fields", "value", "array"]

        description = await _manage(client, action="describe", tool=name, operation="fields")

        card = description["operation"]
        assert card["action"] is None
        assert card["input_variant"] == {"selector": "mode", "value": "fields"}
        assert description["catalog"]["actions"] == []
        schema = description["tool"]["input_schema"]
        assert schema["properties"]["mode"]["const"] == "fields"
        assert "action" not in schema["properties"]
        prepare_call = card["prepare_call"]
        allowed = {**prepare_call["arguments"]["arguments"], "instance_id": 42}
        assert allowed == {"instance_id": 42, "mode": "fields"}
        assert Draft202012Validator(advertised).is_valid(allowed)
        assert not Draft202012Validator(schema).is_valid({**allowed, "start_index": 1})
        arguments = {**prepare_call["arguments"], "arguments": allowed}
        prepared = (await client.call_tool(prepare_call["tool"], arguments)).structured_content
        assert prepared["prepared_call"] == {"tool": name, "arguments": allowed}
        rejected = await _manage(
            client, **{**arguments, "arguments": {**allowed, "mode": "value", "property_path": "x"}}
        )
        assert rejected["ready"] is False
        assert rejected["validation_errors"] == [{"path": "/mode", "category": "const"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_unseen_input_variant_uses_its_local_reference_and_preserves_constraints(
    server, mode
):
    from fastmcp.server.middleware.dereference import DereferenceRefsMiddleware

    server.middleware[:] = [
        middleware
        for middleware in server.middleware
        if not isinstance(middleware, DereferenceRefsMiddleware)
    ]

    def sample_capture(
        encoding: Literal["brief", "expanded"] = "brief", destination: str = ""
    ) -> str:
        return f"{encoding}:{destination}"

    tool = Tool.from_function(sample_capture)
    schema = {
        "type": "object",
        "properties": {
            "encoding": {
                "anyOf": [{"$ref": "#/$defs/Encoding"}, {"type": "null"}],
                "default": "brief",
            },
            "destination": {"type": "string"},
        },
        "$defs": {
            "Encoding": {
                "type": "string",
                "enum": ["brief", "expanded", "blocked"],
                "not": {"const": "blocked"},
            }
        },
        "if": {"required": ["encoding"], "properties": {"encoding": {"const": "expanded"}}},
        "then": {"required": ["destination"]},
    }
    server.add_tool(tool.model_copy(update={"parameters": schema}))

    async with Client(server, mode=mode) as client:
        listed = next(
            item.input_schema for item in await client.list_tools() if item.name == tool.name
        )
        assert listed["properties"]["encoding"]["anyOf"][0] == {"$ref": "#/$defs/Encoding"}
        request = {"tool": tool.name, "operation": "expanded"}
        description = await _manage(client, action="describe", **request)

        card = description["operation"]
        assert card["input_variant"] == {"selector": "encoding", "value": "expanded"}
        assert description["input_choices"] == {"encoding": ["brief", "expanded"]}
        incomplete = await _manage(client, action="prepare", **request, arguments={})
        assert incomplete["ready"] is False
        ready = await _manage(
            client, action="prepare", **request, arguments={"destination": "summary"}
        )
        prepared = ready["prepared_call"]
        assert prepared["arguments"] == {"encoding": "expanded", "destination": "summary"}
        result = await client.call_tool(prepared["tool"], prepared["arguments"])
        assert result.structured_content["result"] == "expanded:summary"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "operation", "choices"),
    [
        ("sample_ambiguous", "shared", {"formatting": ["shared"], "order": ["shared"]}),
        ("sample_ambiguous", "Shared", {"formatting": ["shared"], "order": ["shared"]}),
        ("sample_action", "compact", None),
        ("sample_unbounded", "text", None),
        ("sample_hidden", "hidden", None),
        ("inspect_serialized_object", "fields", None),
        ("sample_external", "external", None),
    ],
)
async def test_unresolved_variants_report_only_visible_bounded_choices(
    server, name, operation, choices
):
    @server.tool
    def sample_ambiguous(formatting: Literal["shared"], order: Literal["shared"]) -> str:
        raise AssertionError("Description must not call the target")

    @server.tool
    def sample_action(action: Literal["first", "second"], formatting: Literal["compact"]) -> str:
        raise AssertionError("Description must not call the target")

    @server.tool
    def sample_unbounded(formatting: Literal["text"] | str) -> str:
        raise AssertionError("Description must not call the target")

    @server.tool
    def sample_hidden(formatting: Literal["hidden"]) -> str:
        raise AssertionError("Description must not call the target")

    def external_probe(formatting: str) -> str:
        raise AssertionError("Description must not call the target")

    external = Tool.from_function(external_probe, name="sample_external")
    server.add_tool(
        external.model_copy(
            update={
                "parameters": {
                    "type": "object",
                    "properties": {"formatting": {"$ref": "https://example.invalid/schema"}},
                }
            }
        )
    )

    class FilterHidden(Middleware):
        async def on_list_tools(self, context, call_next):
            return [
                tool
                for tool in await call_next(context)
                if tool.name not in {"sample_hidden", "inspect_serialized_object"}
            ]

    server.add_middleware(FilterHidden())
    async with Client(server, mode="legacy") as client:
        response = await _manage(client, action="describe", tool=name, operation=operation)

    assert response["success"] is False
    assert response.get("input_choices") == choices
    assert "operation" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_describe_and_prepare_follow_the_replacement_descriptor(server, mode):
    target = "inspect_serialized_object"

    def replacement(representation: Literal["outline", "summary"]) -> str:
        return representation

    replacement_tool = Tool.from_function(
        replacement, name=target, description="Use the wire display representation."
    )

    class ReplaceListDescriptor(Middleware):
        async def on_list_tools(self, context, call_next):
            return [
                replacement_tool if tool.name == target else tool
                for tool in await call_next(context)
            ]

    server.add_middleware(ReplaceListDescriptor())
    async with Client(server, mode=mode) as client:
        description = await _manage(client, action="describe", tool=target, operation="outline")

        assert description["operation"]["input_variant"] == {
            "selector": "representation",
            "value": "outline",
        }
        assert description["operation"]["required_parameters"] == ["representation"]
        assert description["operation"]["purpose"] == replacement_tool.description
        assert description["input_choices"] == {"representation": ["outline", "summary"]}
        call = description["operation"]["prepare_call"]
        result = (await client.call_tool(call["tool"], call["arguments"])).structured_content
        assert result["ready"] is True
        assert result["prepared_call"]["arguments"] == {"representation": "outline"}
        assert "execution" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_describe_does_not_offer_a_choice_removed_from_the_projected_schema(server, mode):
    target = "inspect_serialized_object"

    class ProjectListDescriptor(Middleware):
        async def on_list_tools(self, context, call_next):
            listed = []
            for tool in await call_next(context):
                if tool.name == target:
                    schema = deepcopy(tool.parameters)
                    schema["properties"]["mode"] = {"const": "array"}
                    tool = tool.model_copy(update={"parameters": schema})
                listed.append(tool)
            return listed

    server.add_middleware(ProjectListDescriptor())
    async with Client(server, mode=mode) as client:
        request = {"action": "describe", "tool": target}
        rejected = await _manage(client, **request, operation="fields")

        assert rejected["success"] is False
        assert rejected["input_choices"] == {"mode": ["array"]}
        description = await _manage(client, **request, operation="array")
        call = description["operation"]["prepare_call"]["arguments"]
        draft = {**call["arguments"], "instance_id": 42, "property_path": "items"}
        ready = await _manage(client, **{**call, "arguments": draft})
        assert ready["prepared_call"]["arguments"] == {
            "instance_id": 42,
            "mode": "array",
            "property_path": "items",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize(
    "composition", ["field_ambiguous", "field_intersection", "root", "root_intersection", "open"]
)
async def test_composed_selectors_never_make_an_ambiguous_value_look_unique(
    server, mode, composition
):
    def composed(formatting: str = "brief", order: str = "default") -> str:
        return f"{formatting}:{order}"

    tool = Tool.from_function(composed)
    schema = {
        "type": "object",
        "properties": {"formatting": {"enum": ["brief", "shared"]}},
    }
    order = {"enum": ["default", "shared"]}
    if composition == "root":
        schema["allOf"] = [{"properties": {"order": order}}]
    elif composition == "root_intersection":
        schema["properties"]["order"] = order
        schema["allOf"] = [{"properties": {"order": {"const": "default"}}}]
    elif composition == "open":
        schema["properties"]["order"] = {"anyOf": [order, {"type": "string"}]}
    else:
        restriction = (
            {"const": "default"} if composition == "field_intersection" else {"type": "string"}
        )
        schema["properties"]["order"] = {"allOf": [order, restriction]}
    server.add_tool(tool.model_copy(update={"parameters": schema}))

    async with Client(server, mode=mode) as client:
        result = await _manage(client, action="describe", tool=tool.name, operation="shared")

    if composition in {"field_intersection", "root_intersection"}:
        assert result["operation"]["input_variant"] == {"selector": "formatting", "value": "shared"}
        assert result["input_choices"] == {"formatting": ["brief", "shared"], "order": ["default"]}
    else:
        assert result["success"] is False
        assert "operation" not in result
        if composition == "field_ambiguous":
            assert result["input_choices"] == {
                "formatting": ["brief", "shared"],
                "order": ["default", "shared"],
            }
