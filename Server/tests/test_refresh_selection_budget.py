"""Public stdio refresh selection and dispatch spend one absolute budget."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
import pytest

from core.config import config
from services.tools import refresh_unity as refresh
from transport import unity_instance_middleware as routing
from transport.plugin_hub import PluginHub


@pytest.fixture
def routed_refresh(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    middleware = routing.UnityInstanceMiddleware()
    monkeypatch.setattr(
        middleware, "_resolve_enabled_tool_names_for_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    server = FastMCP("refresh-selection-budget")
    server.add_middleware(middleware)
    server.tool(refresh.refresh_unity)
    return SimpleNamespace(server=server, middleware=middleware, send=send)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection_seconds", [0.25, 2.0])
async def test_public_refresh_includes_middleware_selection(
    monkeypatch, routed_refresh, selection_seconds
):
    started = asyncio.get_running_loop().time()
    now = [started]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(routing, "time", clock)
    monkeypatch.setattr(refresh, "time", clock)

    async def discover(_ctx):
        now[0] += selection_seconds
        return [SimpleNamespace(id="Owned@abc123", hash="abc123", port=6400)]

    monkeypatch.setattr(routed_refresh.middleware, "_discover_instances", discover)
    send = routed_refresh.send
    async with Client(routed_refresh.server) as client:
        result = await client.call_tool(
            "refresh_unity",
            {"unity_instance": "abc", "timeout_seconds": 1.0, "wait_for_ready": False},
        )
    if selection_seconds >= 1:
        payload = result.structured_content.get("result", result.structured_content)
        assert payload["success"] is False
        assert payload["data"]["reason"] == "command_deadline_exceeded"
        send.assert_not_awaited()
    else:
        send.assert_awaited_once()
        assert send.await_args.args[3]["timeout_seconds"] == pytest.approx(1 - selection_seconds)
        assert send.await_args.kwargs["_deadline"] == pytest.approx(started + 1)


@pytest.mark.asyncio
async def test_blocked_public_selection_expires_without_entering_refresh(
    monkeypatch, routed_refresh
):
    cancelled = asyncio.Event()

    async def discover(_ctx):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(routed_refresh.middleware, "_discover_instances", discover)
    async with Client(routed_refresh.server) as client:
        result = await asyncio.wait_for(
            client.call_tool(
                "refresh_unity",
                {"unity_instance": "abc", "timeout_seconds": 1.0},
                raise_on_error=False,
            ),
            timeout=3,
        )
    assert result.structured_content["data"]["dispatch"] == "not_dispatched"
    assert result.structured_content["data"]["phase"] == "selection"
    assert cancelled.is_set()
    routed_refresh.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_refresh_requests_keep_separate_deadlines(monkeypatch, routed_refresh):
    both_sending = asyncio.Event()
    deadlines = []
    instances = [SimpleNamespace(id="Owned@abc123", hash="abc123", port=6400)]
    monkeypatch.setattr(
        routed_refresh.middleware, "_discover_instances", AsyncMock(return_value=instances)
    )

    async def send(_sender, _instance, _command, params, **options):
        deadlines.append((options["_deadline"], params["timeout_seconds"]))
        if len(deadlines) == 2:
            both_sending.set()
        await asyncio.wait_for(both_sending.wait(), timeout=2)
        return {"success": True}

    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    async with Client(routed_refresh.server) as client:
        await asyncio.gather(
            *(
                client.call_tool(
                    "refresh_unity",
                    {"unity_instance": "abc", "timeout_seconds": budget, "wait_for_ready": False},
                )
                for budget in (3.0, 10.0)
            )
        )
    deadlines.sort()
    assert deadlines[1][0] - deadlines[0][0] == pytest.approx(7, abs=0.5)
    assert deadlines[0][1] == pytest.approx(3, abs=0.5)
    assert deadlines[1][1] == pytest.approx(10, abs=0.5)
    assert refresh.refresh_request_deadline.get() is None


@pytest.mark.asyncio
async def test_direct_refresh_bounds_implicit_instance_discovery(monkeypatch, routed_refresh):
    cancelled = asyncio.Event()
    monkeypatch.setattr(refresh, "get_unity_instance_from_context", AsyncMock(return_value=None))

    async def infer(_ctx):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(refresh.editor_state, "infer_single_instance_id", infer)
    result = await asyncio.wait_for(refresh.refresh_unity(None, timeout_seconds=1), timeout=3)
    assert result.data["dispatch"] == "not_dispatched"
    assert cancelled.is_set()
    routed_refresh.send.assert_not_awaited()
