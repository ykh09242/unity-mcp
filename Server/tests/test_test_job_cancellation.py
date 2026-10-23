"""Actual SDK calls with only the native command boundary faked."""

from tests.timing import await_signal

import importlib
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
import pytest


@pytest.mark.asyncio
async def test_sdk_cancel_preserves_pending_native_state_without_ordinary_preflight(monkeypatch):
    module = importlib.import_module("services.tools.cancel_test_job")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    payload = {
        "success": True,
        "data": {
            "job_id": "a" * 32,
            "status": "running",
            "native_control": {"request_state": "accepted", "termination": "cleanup_pending"},
        },
    }
    send = AsyncMock(return_value=payload)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    app = FastMCP("cancel-native-job")
    app.tool(name="cancel_test_job")(module.cancel_test_job)
    async with Client(app) as client:
        result = await client.call_tool("cancel_test_job", {"job_id": "a" * 32})
    value = json.loads(result.content[0].text)
    assert value["data"] == payload["data"]
    assert send.call_args.args[1:4] == ("Game@owned", "cancel_test_job", {"job_id": "a" * 32})
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [250, 3600000, 86400000])
async def test_sdk_opt_in_deadline_forwards_once_without_changing_wait_contract(
    monkeypatch, duration
):
    module = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    send = AsyncMock(return_value={"success": True, "data": {"job_id": "job", "status": "running"}})
    monkeypatch.setattr(module.unity_transport, "send_with_unity_instance", send)
    app = FastMCP("deadline-native-job")
    app.tool(name="run_tests")(module.run_tests)
    async with Client(app) as client:
        result = await client.call_tool("run_tests", {"run_timeout_ms": duration})
    assert json.loads(result.content[0].text)["data"]["status"] == "running"
    assert send.call_args.args[3] == {"mode": "EditMode", "runTimeoutMs": duration}
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sdk_invalid_cancel_reply_does_not_echo_payload(monkeypatch):
    module = importlib.import_module("services.tools.cancel_test_job")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(
        module, "send_with_unity_instance", AsyncMock(return_value="PRIVATE_NATIVE_CONTENT")
    )
    app = FastMCP("invalid-native-cancel")
    app.tool(name="cancel_test_job")(module.cancel_test_job)
    async with Client(app) as client:
        result = await client.call_tool("cancel_test_job", {"job_id": "a" * 32})
    value = json.loads(result.content[0].text)
    assert value["success"] is False
    assert "PRIVATE_NATIVE_CONTENT" not in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 86400001, True, 1.5])
async def test_invalid_direct_deadline_has_no_preflight_or_native_side_effect(monkeypatch, timeout):
    module = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    gate = AsyncMock(side_effect=AssertionError("No preflight mutation before validation"))
    send = AsyncMock()
    monkeypatch.setattr(module, "preflight", gate)
    monkeypatch.setattr(module.unity_transport, "send_with_unity_instance", send)
    result = await module.run_tests(None, run_timeout_ms=timeout)
    assert result.success is False
    gate.assert_not_awaited()
    send.assert_not_awaited()


def test_cancel_catalog_and_selected_tenant_enforcement_in_production_middleware(tmp_path):
    program = r"""
        import anyio, json, socket
        from contextlib import AsyncExitStack
        from tests.remote_credentials import remote_credentials
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from starlette.websockets import WebSocketState
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        import services.tools.cancel_test_job
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        import transport.unity_transport as transport
        from mcp.shared.exceptions import MCPError
        async def scenario():
            def denied(*args, **kwargs): raise AssertionError("Unexpected network")
            socket.socket.connect = socket.socket.connect_ex = denied
            metadata = [t for t in get_registered_tools() if t["name"] == "cancel_test_job"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            for hosted in (False, True):
                config.transport_mode = "http"
                config.http_remote_hosted = hosted
                async with AsyncExitStack() as credentials:
                    if hosted:
                        auth = await credentials.enter_async_context(remote_credentials({"requester:alice": "alice", "plugin:owned": "alice", "plugin:foreign": "bob"}))
                    credentials.push_async_callback(PluginHub.shutdown)
                    registry = PluginRegistry()
                    PluginHub.configure(registry)
                    for sid, owner in (("owned", "alice"), ("foreign", "bob")):
                        await registry.register(sid, sid, sid + "-hash", "test", user_id=owner if hosted else None)
                        await registry.register_tools_for_session(sid, [ToolDefinitionModel(name="cancel_test_job")])
                        PluginHub._connections[sid] = SimpleNamespace(state=SimpleNamespace(user_id=owner,
                            plugin_generation=sid + "-generation", plugin_registered=True, plugin_session_id=sid),
                            client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
                            close=AsyncMock())
                        if hosted:
                            await auth.attach_socket(PluginHub._connections[sid], "plugin:" + sid)
                    middleware = UnityInstanceMiddleware()
                    if hosted:
                        await credentials.enter_async_context(auth.requester("requester:alice"))
                    app = FastMCP("test-cancel-catalog")
                    app.add_middleware(middleware)
                    tools.register_all_tools(app)
                    PluginHub._mcp = app
                    app.enable(tags={"group:testing"}, components={"tool"})
                    calls = []
                    async def dispatch(instance, command, params, *, user_id, **kwargs):
                        sid = await PluginHub._resolve_session_id(instance, user_id=user_id)
                        calls.append((sid, command, user_id))
                        return {"success": True, "data": {"job_id": "a" * 32, "status": "running", "native_control": {"request_state": "accepted", "termination": "cleanup_pending"}}}
                    PluginHub.send_command_for_instance = dispatch
                    for protocol in ("legacy", "2026-07-28"):
                        calls.clear()
                        async with Client(app, mode=protocol) as client:
                            assert "cancel_test_job" in {t.name for t in await client.list_tools()}
                            reply = await client.call_tool("cancel_test_job", {"job_id": "a" * 32, "unity_instance": "owned@owned-hash"})
                            assert json.loads(reply.content[0].text)["success"] is True
                            assert calls == [("owned", "cancel_test_job", "alice" if hosted else None)]
                            if hosted:
                                try:
                                    reply = await client.call_tool("cancel_test_job", {"job_id": "a" * 32, "unity_instance": "foreign@foreign-hash"}, raise_on_error=False)
                                except MCPError: pass
                                else: assert reply.is_error
                                assert len(calls) == 1
                    # A connected populated catalog must not grant an absent native tool.
                    await registry.register_tools_for_session("owned", [ToolDefinitionModel(name="run_tests")])
                    async with Client(app) as client:
                        try:
                            reply = await client.call_tool("cancel_test_job", {"job_id": "a" * 32, "unity_instance": "owned@owned-hash"}, raise_on_error=False)
                        except MCPError: pass
                        else: assert reply.is_error
        anyio.run(scenario)
    """
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / "src"),
            )
        ),
        HOME=str(tmp_path),
        UNITY_MCP_TELEMETRY_ENABLED="false",
    )
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        env=env,
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.asyncio
async def test_abandoned_sdk_poll_never_requests_native_cancel(monkeypatch):
    poll = importlib.import_module("services.tools.run_tests")
    cancel = importlib.import_module("services.tools.cancel_test_job")
    monkeypatch.setattr(
        poll, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(poll, "_update_job_nudge", AsyncMock())
    started = asyncio.Event()

    async def pending(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    native_cancel = AsyncMock()
    monkeypatch.setattr(poll.unity_transport, "send_with_unity_instance", pending)
    monkeypatch.setattr(cancel, "send_with_unity_instance", native_cancel)
    app = FastMCP("abandoned-poll")
    app.tool(name="get_test_job")(poll.get_test_job)
    async with Client(app) as client:
        request = asyncio.create_task(client.call_tool("get_test_job", {"job_id": "job"}))
        await await_signal(started, "started")
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    native_cancel.assert_not_awaited()
