from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp import FastMCP

from core.config import config
from models.models import MCPResponse, ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from services.resources.custom_tools import get_custom_tools
from services.tools.execute_custom_tool import execute_custom_tool
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
import services.custom_tool_service as module


class _DummyMcp:
    def custom_route(self, _path, methods=None):  # noqa: ARG002
        def _decorator(fn):
            return fn

        return _decorator


class _RecordingMcp(_DummyMcp):
    def __init__(self):
        self.routes: list[str] = []

    def custom_route(self, path, methods=None):  # noqa: ARG002
        self.routes.append(path)
        return super().custom_route(path, methods)


def test_register_tools_route_is_not_exposed_in_remote_hosted_mode(monkeypatch):
    """The REST route has no API-key check; the plugin registers tools over the hub
    WebSocket instead, so a hosted server must not offer it to unauthenticated callers."""
    monkeypatch.setattr(config, "http_remote_hosted", True)
    hosted = _RecordingMcp()
    CustomToolService(hosted)
    assert "/register-tools" not in hosted.routes

    monkeypatch.setattr(config, "http_remote_hosted", False)
    local = _RecordingMcp()
    CustomToolService(local)
    assert local.routes == ["/register-tools"]


@pytest.mark.asyncio
async def test_list_registered_tools_threads_user_id_to_plugin_hub():
    service = CustomToolService(_DummyMcp())

    with patch("services.custom_tool_service.PluginHub.get_tools_for_project", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        await service.list_registered_tools("project-hash", user_id="user-1")

    mock_get.assert_awaited_once_with("project-hash", user_id="user-1")


@pytest.mark.asyncio
async def test_get_tool_definition_threads_user_id_to_plugin_hub():
    service = CustomToolService(_DummyMcp())

    with patch("services.custom_tool_service.PluginHub.get_tool_definition", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        await service.get_tool_definition("project-hash", "my_tool", user_id="user-1")

    mock_get.assert_awaited_once_with("project-hash", "my_tool", user_id="user-1")


@pytest.mark.asyncio
async def test_same_named_tools_use_selected_projects_polling_metadata(monkeypatch):
    # Given: projects A and B advertise different runtime metadata for one name.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    service = CustomToolService(FastMCP("project-runtime-metadata"))
    first = ToolDefinitionModel(name="fixture_job", requires_polling=False)
    selected = ToolDefinitionModel(name="fixture_job", requires_polling=True,
                                   poll_action="check_b", max_poll_seconds=30)
    for session, project, project_hash, definition in [
        ("a-session", "A", "a-hash", first), ("b-session", "B", "b-hash", selected),
    ]:
        await registry.register(session, project, project_hash, "6000.0")
        await registry.register_tools_for_session(session, [definition])
        service.register_global_tools([definition])
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    send = AsyncMock(side_effect=[
        {"_mcp_status": "pending", "data": {"job_id": "b-job"}},
        {"_mcp_status": "complete", "data": {"job_id": "b-job", "value": 42}},
    ])
    monkeypatch.setattr(module, "send_with_unity_instance", send)

    # When: the second project's tool executes after the first global registration.
    result = await service.execute_tool("b-hash", "fixture_job", "B@b-hash", {"action": "start"})

    # Then: B's polling contract determines when and how the result is returned.
    assert result.data == {"job_id": "b-job", "value": 42}
    assert send.await_count == 2
    assert send.call_args.args[3] == {"action": "check_b", "job_id": "b-job"}
    assert service._active_polls == 0


@pytest.mark.asyncio
async def test_explicit_project_definition_keeps_precedence(monkeypatch):
    # Given: a project explicitly registers an override for a global tool name.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    service = CustomToolService(_DummyMcp())
    definition = ToolDefinitionModel(name="fixture_job", requires_polling=True)
    service._register_tool("project", definition)
    lookup = AsyncMock()
    monkeypatch.setattr(PluginHub, "get_tool_definition", lookup)
    # When: runtime metadata is resolved for that project.
    result = await service.get_tool_definition("project", "fixture_job")
    # Then: the explicit project registration remains authoritative.
    assert result is definition
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_definition_remains_fallback_for_missing_project_tool(monkeypatch):
    # Given: a global-only tool exists without a selected project's definition.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    service = CustomToolService(FastMCP("global-runtime-fallback"))
    definition = ToolDefinitionModel(name="fixture_global")
    service.register_global_tools([definition])
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(PluginHub, "get_tool_definition", lookup)
    # When: runtime metadata is resolved for an unregistered project.
    result = await service.get_tool_definition("project", "fixture_global", user_id="user-a")
    # Then: the global tool remains usable after checking the selected project.
    assert result is definition
    lookup.assert_awaited_once_with("project", "fixture_global", user_id="user-a")


@pytest.mark.asyncio
async def test_execute_tool_threads_user_id_to_definition_lookup_and_transport():
    service = CustomToolService(_DummyMcp())
    definition = ToolDefinitionModel(name="my_tool", description="My tool", requires_polling=False)

    with patch.object(service, "get_tool_definition", new_callable=AsyncMock) as mock_get_definition:
        with patch("services.custom_tool_service.send_with_unity_instance", new_callable=AsyncMock) as mock_send:
            mock_get_definition.return_value = definition
            mock_send.return_value = {"success": True, "message": "ok"}

            await service.execute_tool(
                "project-hash",
                "my_tool",
                "Project@project-hash",
                {"x": 1},
                user_id="user-1",
            )

    mock_get_definition.assert_awaited_once_with("project-hash", "my_tool", user_id="user-1")
    mock_send.assert_awaited_once()
    assert mock_send.call_args.kwargs["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_execute_custom_tool_threads_user_id_from_context(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)

    ctx = Mock()
    state = {"unity_instance": "Project@project-hash", "user_id": "user-1"}
    ctx.get_state = AsyncMock(side_effect=lambda key, default=None: state.get(key, default))

    service = Mock()
    service.execute_tool = AsyncMock(return_value=MCPResponse(success=True, message="ok"))

    with patch("services.tools.execute_custom_tool.resolve_project_id_for_unity_instance", return_value="project-hash"):
        with patch("services.tools.execute_custom_tool.CustomToolService.get_instance", return_value=service):
            await execute_custom_tool(ctx, "my_tool", {})

    service.execute_tool.assert_awaited_once_with(
        "project-hash",
        "my_tool",
        "Project@project-hash",
        {},
        user_id="user-1",
    )


@pytest.mark.asyncio
async def test_custom_tools_resource_threads_user_id_from_context(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)

    ctx = Mock()
    state = {"unity_instance": "Project@project-hash", "user_id": "user-1"}
    ctx.get_state = AsyncMock(side_effect=lambda key, default=None: state.get(key, default))

    service = Mock()
    service.list_registered_tools = AsyncMock(
        return_value=[ToolDefinitionModel(name="my_tool", description="My tool")]
    )

    with patch("services.resources.custom_tools.resolve_project_id_for_unity_instance", return_value="project-hash"):
        with patch("services.resources.custom_tools.CustomToolService.get_instance", return_value=service):
            await get_custom_tools(ctx)

    service.list_registered_tools.assert_awaited_once_with("project-hash", user_id="user-1")
