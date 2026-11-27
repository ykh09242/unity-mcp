"""Action choices from the schema actually advertised to the MCP connection."""

import pytest
from fastmcp import Client
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from jsonschema import Draft202012Validator

from .test_action_catalog_entrypoints import server as server  # noqa: PLC0414


async def _describe(client, name, **selection):
    return (
        await client.call_tool(
            "manage_tools",
            {"action": "describe", "tool": name, **selection},
            raise_on_error=False,
        )
    ).structured_content


def _action_schema(shape):
    enum = {"enum": ["public", "excluded"]}
    exclude = {"not": {"const": "excluded"}}
    action_is_excluded = {"properties": {"action": {"const": "excluded"}}, "required": ["action"]}
    schema = {
        "type": "object",
        "properties": {"action": enum, "payload": {"type": "string"}},
        "required": ["action"],
    }
    if shape == "field":
        schema["properties"]["action"] = {**enum, **exclude}
    elif shape == "conditional":
        schema["allOf"] = [{"if": action_is_excluded, "then": False}]
    elif shape == "intersection":
        schema["properties"]["action"] = {"type": "string"}
        schema["allOf"] = [
            {"properties": {"action": enum}},
            {"properties": {"action": exclude}},
        ]
    elif shape == "union":
        schema["properties"].pop("action")
        schema["oneOf"] = [{"$ref": "#/$defs/Public"}, {"$ref": "#/$defs/Excluded"}]
        schema["not"] = action_is_excluded
        schema["$defs"] = {
            "Selector": {"const": "public"},
            "Public": {
                "properties": {"action": {"$ref": "#/$defs/Selector"}},
                "required": ["action", "payload"],
            },
            "Excluded": action_is_excluded,
        }
    elif shape == "shared_union":
        action = {"action": {"const": "public"}}
        schema["oneOf"] = [
            {"properties": {**action, "payload": {"type": "string"}}, "required": ["payload"]},
            {"properties": {**action, "alternate": {"type": "string"}}, "required": ["alternate"]},
        ]
    elif shape in {"payload_types", "pure_duplicates"}:
        schema["properties"] = {"action": {"enum": ["public", "excluded", "list"]}, "payload": {}}
        public = {"action": {"const": "public"}}
        if shape == "payload_types":
            schema["oneOf"] = [
                {"properties": {**public, "payload": {"type": kind}}, "required": ["payload"]}
                for kind in ("string", "object")
            ]
        else:
            schema["oneOf"] = [{"properties": public}] * 2
        schema["oneOf"].append(
            {"properties": {"action": {"const": "list"}, "payload": {"type": "string"}}}
        )
    elif shape == "external":
        schema["allOf"] = [{"$ref": "https://example.invalid/action-schema"}]
    elif shape == "scoped_ref":
        schema["$defs"] = {"Action": {"const": "public"}}
        schema["allOf"] = [
            {
                "$id": "nested-action",
                "$defs": {"Action": {"const": "excluded"}},
                "properties": {"action": {"$ref": "#/$defs/Action"}},
            }
        ]
    else:
        schema.update(
            required=["action", "payload"],
            **{
                "if": {"properties": {"payload": {"const": "text"}}, "required": ["payload"]},
                "then": {"properties": {"action": {"const": "public"}}},
                "else": {"properties": {"action": {"const": "excluded"}}},
            },
        )
    return schema


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [False], indirect=True)
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("shape", ["field", "conditional", "intersection", "union", "shared_union"])
async def test_tool_only_describe_offers_only_schema_admitted_actions(server, mode, shape):
    def sample_action(action: str, payload: str = "", alternate: str = "") -> str:
        return f"{action}:{payload or alternate}"

    tool = Tool.from_function(sample_action)
    server.add_tool(tool.model_copy(update={"parameters": _action_schema(shape)}))
    async with Client(server, mode=mode) as client:
        advertised = next(
            item.input_schema for item in await client.list_tools() if item.name == tool.name
        )
        browse = await _describe(client, tool.name)

        choices = browse["operation_choices"]
        assert [choice["arguments"]["operation"] for choice in choices] == ["public"]
        selected = (
            await client.call_tool(choices[0]["tool"], choices[0]["arguments"])
        ).structured_content
        assert selected["operation"]["action"] == "public"
        bound = selected["tool"]["input_schema"]
        valid = {"action": "public", "payload": "text"}
        excluded = {**valid, "action": "excluded"}
        for schema in (advertised, bound):
            validator = Draft202012Validator(schema)
            assert validator.is_valid(valid) and not validator.is_valid(excluded)
            if shape in {"union", "shared_union"}:
                assert not validator.is_valid({"action": "public"})
            if shape == "shared_union":
                assert validator.is_valid({"action": "public", "alternate": "note"})
                assert not validator.is_valid({**valid, "alternate": "extra"})
        assert (await _describe(client, tool.name, operation="excluded"))["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [False], indirect=True)
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_shared_action_accepts_either_published_payload_type_and_rejects_invalid_drafts(
    server, mode
):
    def sample_action(action: str, payload: str | dict[str, str] = "") -> str | dict[str, str]:
        return payload

    tool = Tool.from_function(sample_action)
    server.add_tool(tool.model_copy(update={"parameters": _action_schema("payload_types")}))
    async with Client(server, mode=mode) as client:
        choices = (await _describe(client, tool.name))["operation_choices"]
        assert [choice["arguments"]["operation"] for choice in choices] == ["public", "list"]
        selected = (
            await client.call_tool(choices[0]["tool"], choices[0]["arguments"])
        ).structured_content
        prepare = selected["operation"]["prepare_call"]
        for payload, expected in (("text", True), ({"label": "text"}, True), (42, False)):
            draft = {"action": "public", "payload": payload}
            result = (
                await client.call_tool(
                    prepare["tool"],
                    {**prepare["arguments"], "arguments": draft},
                    raise_on_error=False,
                )
            ).structured_content
            assert result["ready"] is expected
            if expected:
                prepared = result["prepared_call"]
                assert prepared["arguments"] == draft
                called = (
                    await client.call_tool(prepared["tool"], prepared["arguments"])
                ).structured_content
                assert called == {"result": payload}


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [False], indirect=True)
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_selector_only_duplicates_still_violate_one_of_with_a_payload_alternative(
    server, mode
):
    def sample_duplicate(action: str, payload: str = "") -> str:
        return f"{action}:{payload}"

    tool = Tool.from_function(sample_duplicate)
    server.add_tool(tool.model_copy(update={"parameters": _action_schema("pure_duplicates")}))
    async with Client(server, mode=mode) as client:
        choices = (await _describe(client, tool.name))["operation_choices"]
        assert [choice["arguments"]["operation"] for choice in choices] == ["list"]
        assert (await _describe(client, tool.name, operation="public"))["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [False], indirect=True)
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("shape", ["external", "opaque_condition", "scoped_ref"])
async def test_unresolved_action_constraints_do_not_suggest_automatic_choices(server, mode, shape):
    def sample_opaque(action: str, payload: str = "") -> str:
        return f"{action}:{payload}"

    tool = Tool.from_function(sample_opaque)
    schema = _action_schema(shape)
    server.add_tool(tool.model_copy(update={"parameters": schema}))
    async with Client(server, mode=mode) as client:
        browse = await _describe(client, tool.name)

        assert not browse.get("operation_choices")
        assert "action" in browse["tool"]["input_schema"]["required"]
        assert (await _describe(client, tool.name, operation="public"))["success"] is False
        prepared = (
            await client.call_tool(
                "manage_tools",
                {"action": "prepare", "tool": tool.name, "arguments": {}},
                raise_on_error=False,
            )
        ).structured_content
        assert prepared["ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tool_only_describe_does_not_restore_an_action_removed_from_a_registered_projection(
    server, mode
):
    class ExcludeSelection(Middleware):
        async def on_list_tools(self, context, call_next):
            projected = []
            for tool in await call_next(context):
                if tool.name == "manage_tools":
                    schema = tool.parameters
                    fields = schema["properties"]
                    narrowed = {**fields["action"], "not": {"const": "activate"}}
                    tool = tool.model_copy(
                        update={
                            "parameters": {**schema, "properties": {**fields, "action": narrowed}}
                        }
                    )
                projected.append(tool)
            return projected

    server.add_middleware(ExcludeSelection())
    async with Client(server, mode=mode) as client:
        browse = await _describe(client, "manage_tools", limit=20)

        choices = {choice["arguments"]["operation"] for choice in browse["operation_choices"]}
        assert "activate" not in choices and "list_groups" in choices
        assert (await _describe(client, "manage_tools", operation="activate"))["success"] is False
