"""The public MCP and offline CLI use the same complete discovery catalog."""

import json

from click.testing import CliRunner
from fastmcp import Client, FastMCP
import pytest


@pytest.fixture
def server(monkeypatch, request):
    from core.config import config
    from services.tools import register_all_tools

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("action-catalog-tests", dereference_schemas=getattr(request, "param", True))
    register_all_tools(server, project_scoped_tools=False)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_catalog_and_describe_preserve_inventory_and_schema(server, mode):
    async with Client(server, mode=mode) as client:
        before = {tool.name for tool in await client.list_tools()}
        index = (await client.call_tool("manage_tools", {"action": "catalog"})).structured_content
        names = {name for group in index["groups"] for name in group["tools"]}
        assert names == set(server._tool_discovery.definitions)
        assert "execute_custom_tool" not in names
        assert "manage_shader" in names and "manage_shader" not in before
        page = (
            await client.call_tool(
                "manage_tools",
                {
                    "action": "catalog",
                    "tool": "manage_shader",
                    "limit": 20,
                },
            )
        ).structured_content
        assert page["tool"]["advertised"] is False
        assert "inspect_graph" in {action["action"] for action in page["actions"]}
        result = (
            await client.call_tool(
                "manage_tools",
                {
                    "action": "describe",
                    "tool": "manage_shader",
                    "operation": "inspect_graph",
                },
            )
        ).structured_content
        assert result["operation"]["action"] == "inspect_graph"
        assert result["tool"]["input_schema"]["properties"]["action"]["const"] == "inspect_graph"
        assert result["catalog"]["advertised"] is False
        assert "prepare_call" not in result["operation"]
        assert result["catalog"]["actions"] == page["tool"]["actions"]
        assert {tool.name for tool in await client.list_tools()} == before
        original = server._tool_discovery.definitions["manage_shader"].parameters
        assert "const" not in original["properties"]["action"]

        graph = (
            await client.call_tool(
                "manage_tools",
                {"action": "describe", "tool": "manage_reference_graph", "operation": "path"},
            )
        ).structured_content
        schema = graph["tool"]["input_schema"]
        assert "options" in schema["required"]
        assert schema["properties"]["options"]["required"] == ["node", "target"]
        assert graph["operation"]["options_schema"]["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "catalog", "group": "core", "tool": "manage_shader"},
        {"action": "catalog", "query": "shader"},
        {"action": "catalog", "operation": "read"},
        {"action": "catalog", "tool": "unknown"},
        {"action": "catalog", "offset": 1},
        {"action": "search", "query": "shader", "offset": 1},
        {"action": "activate", "tool": "manage_shader", "operation": "read"},
        {"action": "describe", "tool": "manage_shader", "operation": "invented"},
        {"action": "describe", "tool": "manage_shader", "operation": "   "},
    ],
)
async def test_bad_selection_fails_without_activation(server, arguments):
    async with Client(server, mode="legacy") as client:
        before = {tool.name for tool in await client.list_tools()}
        result = (await client.call_tool("manage_tools", arguments)).structured_content
        assert result["success"] is False
        assert {tool.name for tool in await client.list_tools()} == before


def test_catalog_permission_is_read_only():
    from services.operation_permissions import Permission, required_permissions

    assert required_permissions("manage_tools", {"action": "catalog"}) == {Permission.READ}


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_describe_observes_the_same_tool_list_policy_as_prepare(server, denied):
    from fastmcp.exceptions import AuthorizationError
    from fastmcp.server.middleware import Middleware
    from transport.protocol import is_internal_catalog_list

    @server.tool
    def hidden_probe():
        return True

    class Policy(Middleware):
        async def on_list_tools(self, context, call_next):
            if denied and is_internal_catalog_list(context):
                raise AuthorizationError("Inventory unavailable")
            return [
                tool
                for tool in await call_next(context)
                if tool.name not in {"search_unity_tools", "hidden_probe"}
            ]

    server.add_middleware(Policy())
    async with Client(server, mode="legacy") as client:
        reply = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "search_unity_tools"}
        )
        description = reply.structured_content
        assert description["catalog"]["advertised"] is (None if denied else False)
        assert "prepare_call" not in description["operation"]
        hidden = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "hidden_probe"}
        )
        assert hidden.structured_content["success"] is False
        assert "tool" not in hidden.structured_content


def test_large_custom_input_retains_its_exact_constraints_at_the_returned_schema_pointer():
    from jsonschema import Draft202012Validator
    from services.registry.action_catalog import describe_action

    field = "sensor/scale~%"
    choices = [f"channel-{n}" for n in range(300)]
    schema = {"type": "object", "properties": {field: {"enum": choices}}}
    result = describe_action({"name": "selector", "kwargs": {}}, {"inputSchema": schema})
    reference = result["operation"]["input_fields"][field]
    assert set(reference) == {"$ref"}
    validator = Draft202012Validator(
        {
            **result["tool"]["inputSchema"],
            "properties": {**schema["properties"], "value": reference},
        }
    )
    assert validator.is_valid({"value": choices[-1]})
    assert not validator.is_valid({"value": "unregistered"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_described_inputs_lead_to_a_callable_draft(server, mode):
    from typing import Literal

    @server.tool
    def sample_channel(
        action: Literal["sample", "status"], options: dict, label: str | None = None
    ):
        return {"observed": options["enabled"], "label": label}

    @server.tool
    def sample_offset(offset: int, enabled: bool = True):
        return {"observed": offset if enabled else None}

    async with Client(server, mode=mode) as client:
        for name, op, values, expected in (
            (
                "sample_channel",
                "sample",
                {"options": {"enabled": False}, "label": None},
                {"observed": False, "label": None},
            ),
            ("sample_offset", None, {"offset": 0, "enabled": False}, {"observed": None}),
        ):
            selected = {"operation": op} if op else {}
            result = await client.call_tool(
                "manage_tools", {"action": "describe", "tool": name, **selected}
            )
            card = result.structured_content["operation"]
            required = "options" if op else "offset"
            assert card["input_fields"][required]["type"] == ("object" if op else "integer")
            followup = card["prepare_call"]
            draft = followup["arguments"]["arguments"]
            assert draft == ({"action": op} if op else {})
            incomplete = await client.call_tool(followup["tool"], followup["arguments"])
            assert incomplete.structured_content["ready"] is False
            draft.update(values)
            if op:
                wrong = await client.call_tool(
                    followup["tool"],
                    {**followup["arguments"], "arguments": {**draft, "action": "status"}},
                )
                assert wrong.structured_content["ready"] is False
            prepared = await client.call_tool(followup["tool"], followup["arguments"])
            call = prepared.structured_content["prepared_call"]
            assert call == {"tool": name, "arguments": draft}
            assert (
                await client.call_tool(call["tool"], call["arguments"])
            ).structured_content == expected


@pytest.mark.asyncio
async def test_selected_material_fields_explain_nested_patch_and_addressing(server):
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool(
            "manage_tools",
            {"action": "describe", "tool": "manage_material", "operation": "set_render_state"},
        )
        fields = result.structured_content["operation"]["input_fields"]
        assert {"material_path", "target", "slot", "search_method", "mode", "render_state"} == set(
            fields
        )
        assert "render_queue" in fields["render_state"]["description"]
        assert any(branch.get("type") == "object" for branch in fields["render_state"]["anyOf"])


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [True, False], indirect=True, ids=["inlined", "references"])
async def test_local_schema_alternatives_keep_their_nested_inputs_and_required_values(server):
    from fastmcp.tools import Tool
    from jsonschema import Draft202012Validator

    def inspect_port(action: str, probe=None):
        return {"probe": probe}

    definition = Tool.from_function(inspect_port)
    definition.parameters = {
        "type": "object",
        "properties": {"action": True},
        "$defs": {
            "Read": {
                "properties": {"action": {"const": "read"}, "probe": {"$ref": "#/$defs/Probe"}},
                "required": ["action", "probe"],
            },
            "Probe": {
                "type": "object",
                "properties": {"channel": {"type": "integer", "minimum": 1}},
                "required": ["channel"],
            },
        },
        "oneOf": [
            {"$ref": "#/$defs/Read"},
            {"properties": {"action": {"const": "status"}}, "required": ["action"]},
        ],
    }
    server.add_tool(definition)
    async with Client(server, mode="legacy") as client:
        public = next(t.input_schema for t in await client.list_tools() if t.name == "inspect_port")
        description = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "inspect_port", "operation": "read"}
        )
        result = description.structured_content
        operation = result["operation"]
        assert operation["required_parameters"] == ["probe"]
        assert result["tool"]["input_schema"]["oneOf"] == public["oneOf"]
        field = Draft202012Validator(
            {"$defs": public.get("$defs", {}), **operation["input_fields"]["probe"]}
        )
        assert field.is_valid({"channel": 1}) and not field.is_valid({"channel": 0})
        assert operation["input_variants"] == [
            {"schema_path": "#/oneOf/0", "required_parameters": ["probe"], "parameters": ["probe"]}
        ]
        prepare = operation["prepare_call"]
        for channel, ready in ((0, False), (1, True)):
            prepare["arguments"]["arguments"]["probe"] = {"channel": channel}
            check = (
                await client.call_tool(prepare["tool"], prepare["arguments"])
            ).structured_content
            assert check["ready"] is ready
            if ready:
                call = check["prepared_call"]
                assert (
                    await client.call_tool(call["tool"], call["arguments"])
                ).structured_content == {"probe": {"channel": 1}}


def test_every_action_card_binds_a_real_schema_property(server):
    from jsonschema import Draft202012Validator
    from services.registry import get_registered_tools
    from services.registry.action_catalog import declared_actions, describe_action

    definitions = server._tool_discovery.definitions
    for entry in get_registered_tools():
        if entry["name"] not in definitions:
            continue
        descriptor = (
            definitions[entry["name"]].to_mcp_tool().model_dump(mode="json", exclude_none=True)
        )
        for action in declared_actions(entry) or [None]:
            result = describe_action(entry, descriptor, action)
            schema = result["tool"]["input_schema"]
            Draft202012Validator.check_schema(schema)
            card = result["operation"]
            assert set(card["required_parameters"]) <= schema["properties"].keys()
            if action is not None:
                assert set(card["required_parameters"]) <= set(schema["required"])
            assert set(card["forbidden_parameters"]) <= schema["properties"].keys()
            if action is not None:
                validator = Draft202012Validator(schema["properties"]["action"])
                assert validator.is_valid(action), (entry["name"], action)
                assert not validator.is_valid("unknown-action")


def test_cli_browses_offline_and_preserves_pagination(monkeypatch):
    from cli.main import cli
    import cli.utils.connection as connection

    monkeypatch.setattr(connection, "_auth_headers", lambda *_: pytest.fail("catalog read auth"))
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "tool",
            "catalog",
            "--tool",
            "manage_shader",
            "--limit",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scope"] == "local_catalog"
    assert payload["tool"]["advertised"] is None
    assert payload["next_offset"] == 2
    assert len(payload["actions"]) == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["--group", "core", "--tool", "manage_shader"],
        ["--tool", "invented"],
        ["--limit", "21"],
        ["--offset", "-1"],
    ],
)
def test_cli_refuses_bad_catalog_selections(arguments):
    from cli.main import cli

    result = CliRunner().invoke(cli, ["tool", "catalog", *arguments])
    assert result.exit_code == 2, result.output
