"""Separate server compositions retain their own routes and cleanup owners."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp.tools.base import Tool
from starlette.testclient import TestClient

import main
from core.config import config
from models.models import MCPResponse
from services.custom_tool_service import CustomToolService
from services.state.external_changes_scanner import external_changes_scanner
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub


@pytest.fixture
def servers(monkeypatch, prepared_tool_factory):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "runtime-fixture")
    monkeypatch.setattr(Tool, "from_function", prepared_tool_factory)
    monkeypatch.setattr(main, "resolve_project_id_for_unity_instance", lambda instance: instance)
    monkeypatch.setattr(
        PluginHub,
        "get_sessions",
        AsyncMock(
            return_value=SessionList(
                sessions={
                    "owned": SessionDetails(
                        project="Fixture",
                        hash="owned-hash",
                        unity_version="6000",
                        connected_at="now",
                    )
                }
            )
        ),
    )
    return main.create_mcp_server(False), main.create_mcp_server(False)


@pytest.mark.parametrize("route", ["list", "execute"])
def test_local_routes_keep_their_service_after_another_server_is_composed(
    servers, monkeypatch, route
):
    first, second = servers
    assert first.runtime.custom_tools is not second.runtime.custom_tools
    assert CustomToolService.get_instance() is second.runtime.custom_tools
    services = [server.runtime.custom_tools for server in servers]
    for index, service in enumerate(services):
        monkeypatch.setattr(
            service, "list_registered_tools", AsyncMock(return_value=[{"name": f"owner-{index}"}])
        )
        monkeypatch.setattr(
            service,
            "execute_tool",
            AsyncMock(return_value=MCPResponse(success=True, data={"owner": index})),
        )
    for index, server in enumerate(servers):
        client = TestClient(server.http_app(), headers={"X-Unity-MCP-Token": "runtime-fixture"})
        if route == "list":
            response = client.get("/api/custom-tools")
            assert response.status_code == 200
            assert response.json()["tools"] == [{"name": f"owner-{index}"}]
        else:
            response = client.post(
                "/api/command",
                json={"type": "execute_custom_tool", "params": {"tool_name": "OwnedTool"}},
            )
            assert response.status_code == 200
            assert response.json()["data"] == {"owner": index}
    for service in services:
        if route == "list":
            service.list_registered_tools.assert_awaited_once_with("owned-hash")
        else:
            service.execute_tool.assert_awaited_once_with(
                "owned-hash", "OwnedTool", "owned-hash", {}
            )


@pytest.mark.asyncio
async def test_runtime_replacement_and_cleanup_share_one_instance_owned_store(servers, monkeypatch):
    first, second = servers
    old_graph = first.runtime.reference_graph
    graphs = [SimpleNamespace(aclose=AsyncMock()) for _ in servers]
    states = [SimpleNamespace(clear=Mock()) for _ in servers]
    receipts = [SimpleNamespace(close=Mock()) for _ in servers]
    inputs = [SimpleNamespace(close=Mock()) for _ in servers]
    for index, server in enumerate(servers):
        # Existing installers and embeddings may still replace the old attributes.
        server._reference_graph_service = graphs[index]
        server._execution_receipts = receipts[index]
        server.runtime.session_state = states[index]
        server.runtime.scene_inputs = inputs[index]
    assert first.runtime.reference_graph is graphs[0]
    assert first.runtime.reference_graph is not old_graph
    assert first.runtime.execution_receipts is receipts[0]
    monkeypatch.setattr(PluginHub, "begin_shutdown", lambda: AsyncMock())
    monkeypatch.setattr(external_changes_scanner, "begin_shutdown", lambda: AsyncMock())
    monkeypatch.setattr(main, "begin_telemetry_shutdown", lambda _owner: Mock())
    await main._shutdown_server_resources(
        first, None, [], None, SimpleNamespace(aclose=AsyncMock())
    )
    graphs[0].aclose.assert_awaited_once()
    states[0].clear.assert_called_once()
    receipts[0].close.assert_called_once()
    inputs[0].close.assert_called_once()
    graphs[1].aclose.assert_not_awaited()
    states[1].clear.assert_not_called()
    receipts[1].close.assert_not_called()
    inputs[1].close.assert_not_called()
