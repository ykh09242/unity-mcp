"""Selected native membership is checked again before queued public preflight."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import FastMCP
import pytest

from core.config import config
from services.registry import operation_invocation as invocation
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationAdmission
from services.registry.operation_target import capture_http_target, capture_stdio_target
from tests.timing import await_signal
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from models.models import ToolDefinitionModel


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_withdrawn_selected_builtin_does_not_begin_queued_refresh_preflight(
    monkeypatch, transport
):
    import services.tools.find_gameobjects as search

    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("catalog-recheck")
    definition = ToolDefinitionModel(name="find_gameobjects", description="fixture")
    registry = PluginRegistry()
    await registry.register("owned", "Owned", "hash", "fixture")
    await registry.register_tools_for_session("owned", [definition])
    socket = SimpleNamespace(
        state=SimpleNamespace(
            user_id=None,
            plugin_generation="g",
            plugin_arbitration_generation=None,
            plugin_features=frozenset(),
        )
    )
    hub = SimpleNamespace(
        _registry=registry,
        _connections={"owned": socket},
        _supports=PluginHub._supports,
        _pending_capacity_error=lambda *_: None,
    )
    target = await capture_http_target("owned", hub=hub)
    if transport == "stdio":
        from services.custom_tool_service import CustomToolService
        from transport.legacy import unity_connection as legacy

        monkeypatch.setattr(CustomToolService, "_instance", None)
        monkeypatch.setattr(legacy, "_unity_connection_pool", None)
        service = CustomToolService(server)
        service.replace_project_catalog(
            "hash", [definition], kind="stdio", generation="g", publish_global=False
        )
        connection = legacy.UnityConnection(port=6400, instance_id="Owned@hash")
        connection.session_generation = "g"
        target = capture_stdio_target(connection)
    server.tool(search.find_gameobjects)
    preflight = AsyncMock(side_effect=AssertionError("Withdrawn tool started effectful preflight"))
    monkeypatch.setattr(search, "preflight", preflight)

    class Context:
        fastmcp = server

        async def get_state(self, key, default=None):
            return "Owned@hash" if key == "unity_instance" else default

    async def capture(*args, **kwargs):
        return target

    monkeypatch.setattr(invocation, "capture_target", capture)
    queued = asyncio.Event()
    owner = scheduler()
    wake = owner._wake

    def observe(key, state):
        wake(key, state)
        if state.waiting:
            queued.set()

    monkeypatch.setattr(owner, "_wake", observe)
    async with owner.session(OperationAdmission(target.scope, "exclusive", target.is_current)):
        pending = asyncio.create_task(
            invocation.wrap_tool(search.find_gameobjects, "find_gameobjects", "find_gameobjects")(
                Context(), search_term="Owned"
            )
        )
        await await_signal(queued, "public tool queued after initial native catalog check")
        if transport == "stdio":
            service.replace_project_catalog(
                "hash", [], kind="stdio", generation="g", publish_global=False
            )
        else:
            await registry.register_tools_for_session("owned", [])
    result = await pending
    assert result["data"] == {"reason": "operation_tool_unavailable", "dispatch": "not_dispatched"}
    preflight.assert_not_awaited()
    assert owner.snapshot()["instances"] == 0
