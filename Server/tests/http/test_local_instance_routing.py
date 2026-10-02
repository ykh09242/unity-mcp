"""Explicit Unity instance hashes are authoritative on local REST routes."""

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

import main
from core.config import config
from main import create_mcp_server
from services.custom_tool_service import CustomToolService
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub


@pytest.fixture
def routing_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "routing-test-token")
    sessions = SessionList(sessions={
        sid: SessionDetails(project="SameName", hash=project_hash, unity_version="6000", connected_at="now")
        for sid, project_hash in (("first", "aaaa1111"), ("second", "bbbb2222"))
    })
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=sessions))
    monkeypatch.setattr(main, "resolve_project_id_for_unity_instance", lambda instance: instance)
    return TestClient(create_mcp_server(False).http_app(), headers={"X-Unity-MCP-Token": "routing-test-token"})


@pytest.mark.parametrize("selector, expected", [("SameName@bbbb2222", "second"), ("bbbb2222", "second"), ("SameName", "first")])
def test_command_routes_by_explicit_hash_with_duplicate_names(routing_client: TestClient, monkeypatch: pytest.MonkeyPatch, selector: str, expected: str) -> None:
    # Given same-name sessions and a requested instance.
    dispatch = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginHub, "send_command", dispatch)
    # When the real local REST route receives a command.
    response = routing_client.post("/api/command", json={"type": "manage_scene", "params": {}, "unity_instance": selector})
    # Then the hash selects its matching session, preserving bare-name selection.
    assert response.status_code == 200
    dispatch.assert_awaited_once_with(expected, "manage_scene", {})


def test_custom_tools_routes_by_explicit_hash_with_duplicate_names(routing_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given same-name sessions with separate custom tool inventories.
    monkeypatch.setattr(main, "resolve_project_id_for_unity_instance", lambda instance: instance)
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(CustomToolService.get_instance(), "list_registered_tools", listing)
    # When the real local REST route lists tools for the second hash.
    response = routing_client.get("/api/custom-tools", params={"instance": "SameName@bbbb2222"})
    # Then the custom tool service receives that concrete hash.
    assert response.status_code == 200
    assert response.json()["project_id"] == "bbbb2222"
    listing.assert_awaited_once_with("bbbb2222")


@pytest.mark.parametrize("route", ["command", "custom-tools"])
def test_missing_explicit_hash_does_not_fall_back_to_name(routing_client: TestClient, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    # Given a name that exists but an explicit hash that does not.
    dispatch = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginHub, "send_command", dispatch)
    # When a real local REST request supplies that stale target.
    if route == "command":
        response = routing_client.post("/api/command", json={"type": "manage_scene", "unity_instance": "SameName@missing"})
    else:
        response = routing_client.get("/api/custom-tools", params={"instance": "SameName@missing"})
    # Then it fails rather than silently selecting the same-name project.
    assert response.status_code == 404
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("route", ["command", "custom-tools"])
def test_bare_hash_takes_precedence_over_another_project_name(routing_client: TestClient, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    # Given a project's name that collides with another project's hash.
    sessions = SessionList(sessions={
        sid: SessionDetails(project=project, hash=project_hash, unity_version="6000", connected_at="now")
        for sid, project, project_hash in (("first", "bbbb2222", "aaaa1111"), ("second", "Other", "bbbb2222"))
    })
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=sessions))
    dispatch = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginHub, "send_command", dispatch)
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(CustomToolService.get_instance(), "list_registered_tools", listing)
    # When a real local REST request identifies the bare exact hash.
    if route == "command":
        response = routing_client.post("/api/command", json={"type": "manage_scene", "unity_instance": "bbbb2222"})
    else:
        response = routing_client.get("/api/custom-tools", params={"instance": "bbbb2222"})
    # Then hash matching wins across the full catalog before any name fallback.
    assert response.status_code == 200
    if route == "command":
        dispatch.assert_awaited_once_with("second", "manage_scene", {})
    else:
        listing.assert_awaited_once_with("bbbb2222")
