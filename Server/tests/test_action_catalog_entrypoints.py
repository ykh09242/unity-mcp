"""The public MCP and offline CLI use the same complete discovery catalog."""

import json

from click.testing import CliRunner
from fastmcp import Client, FastMCP
import pytest


@pytest.fixture
def server(monkeypatch):
    from core.config import config
    from services.tools import register_all_tools

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("action-catalog-tests")
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
        assert {tool.name for tool in await client.list_tools()} == before
        original = server._tool_discovery.definitions["manage_shader"].parameters
        assert "const" not in original["properties"]["action"]


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
