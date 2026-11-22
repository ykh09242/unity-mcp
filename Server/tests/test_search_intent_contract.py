"""Exercise search advice and candidate selection through real MCP messages."""

import pytest
from fastmcp import Client

from core.config import config


@pytest.fixture
def search_server(monkeypatch):
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
async def test_search_inputs_explain_their_role_in_advertised_wire_schemas(search_server, mode):
    async with Client(search_server, mode=mode) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in ("search_unity_tools", "manage_tools"):
        properties = tools[name].input_schema["properties"]
        for field in ("query", "catalog_query", "intent", "observation_source", "required_values"):
            assert properties[field].get("description"), (name, field)
        for field in ("query", "catalog_query"):
            options = properties[field]["anyOf"]
            assert (
                next(option for option in options if option["type"] == "string")["maxLength"] == 512
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "entry_arguments", "query", "catalog_query", "intent", "tool", "operation"),
    [
        (
            "search_unity_tools",
            {},
            "Öğeyi tıkla",
            "ui_click",
            "interact",
            "manage_input",
            "ui_click",
        ),
        (
            "manage_tools",
            {"action": "search"},
            "既存のシーンを開く",
            "manage_scene load",
            "edit",
            "manage_scene",
            "load",
        ),
    ],
)
async def test_evidence_constrained_action_miss_offers_an_explicit_action_only_retry(
    search_server, name, entry_arguments, query, catalog_query, intent, tool, operation
):
    arguments = {
        **entry_arguments,
        "query": query,
        "catalog_query": catalog_query,
        "intent": intent,
        "observation_source": "editor_live",
        "required_values": ["active_scene.path"],
        "route_surfaces": "tools",
        "limit": 1,
    }
    async with Client(search_server, mode="2026-07-28") as client:
        miss = (await client.call_tool(name, arguments)).structured_content
        assert miss["query"] == query
        assert miss["intent"] == intent
        assert miss["required_values"] == ["active_scene.path"]
        assert miss["observation_source"] == "editor_live"
        assert miss["matching"] == "observation"
        assert miss["route_groups"] == []
        assert miss["no_match"]["undeclared_values"] == ["active_scene.path"]

        retry = miss["retry_search"]
        assert retry["arguments"] == {
            "query": query,
            "catalog_query": catalog_query,
            "intent": intent,
        }
        assert set(retry["omit"]) == {"observation_source", "required_values"}
        action_arguments = {**arguments, **retry["arguments"]}
        for field in retry["omit"]:
            action_arguments.pop(field)
        result = (await client.call_tool(name, action_arguments)).structured_content

    assert result["query"] == query
    assert (result["matching"], result["matched_query"]) == ("lexical", catalog_query)
    route = result["route_groups"][0]["representative"]
    assert (route["tool"], route["operation"], route["intent"]) == (tool, operation, intent)
    assert "retry_search" not in result
