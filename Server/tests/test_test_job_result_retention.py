"""Public SDK boundaries for retained native test evidence (native IO is faked)."""

import importlib
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
async def test_public_sdk_retains_page_and_availability(monkeypatch):
    jobs = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    sender = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "job_id": "job",
                "status": "failed",
                "completion_kind": "completed",
                "result_availability": "available",
                "result_page": {
                    "items": [
                        {"fullName": "Fixture.Fail", "state": "Failed", "message": "한국어<&>"}
                    ],
                    "total_count": 1000,
                    "has_more": True,
                    "next_cursor": "opaque",
                    "generation": "g",
                },
            },
        }
    )
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    app = FastMCP("test-job-pages")
    app.tool(name="get_test_job")(jobs.get_test_job)
    async with Client(app) as client:
        reply = await client.call_tool(
            "get_test_job", {"job_id": "job", "include_details": True, "page_size": 73}
        )
    data = json.loads(reply.content[0].text)["data"]
    assert data["completion_kind"] == "completed"
    assert data["result_page"]["total_count"] == 1000
    assert data["result_page"]["items"][0]["message"] == "한국어<&>"
    assert sender.call_args.args[1:3] == ("Game@owned", "get_test_job")
    assert sender.call_args.args[3]["page_size"] == 73


@pytest.mark.asyncio
async def test_public_sdk_export_preserves_native_chunk(monkeypatch):
    export = importlib.import_module("services.tools.export_test_job")
    monkeypatch.setattr(
        export, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    payload = {
        "success": True,
        "data": {
            "content": "<testsuite>한국어</testsuite>",
            "has_more": False,
            "next_cursor": None,
            "generation": "a" * 32,
            "sha256": "a" * 64,
            "total_bytes": 38,
            "mime_type": "application/xml",
        },
    }
    sender = AsyncMock(return_value=payload)
    monkeypatch.setattr(export, "send_with_unity_instance", sender)
    app = FastMCP("test-job-export")
    app.tool(name="export_test_job")(export.export_test_job)
    async with Client(app) as client:
        reply = await client.call_tool("export_test_job", {"job_id": "job", "format": "junit"})
    value = json.loads(reply.content[0].text)
    assert value["success"] is True
    assert value["data"] == payload["data"]
    assert sender.call_args.args[1:3] == ("Game@owned", "export_test_job")


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [None, "PRIVATE_NATIVE_PAYLOAD"])
async def test_public_export_rejects_invalid_envelopes_without_echo(monkeypatch, native):
    export = importlib.import_module("services.tools.export_test_job")
    monkeypatch.setattr(
        export, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(export, "send_with_unity_instance", AsyncMock(return_value=native))
    app = FastMCP("invalid-native-export")
    app.tool(name="export_test_job")(export.export_test_job)
    async with Client(app) as client:
        reply = await client.call_tool("export_test_job", {"job_id": "job"})
    value = json.loads(reply.content[0].text)
    assert value["success"] is False
    assert "PRIVATE_NATIVE_PAYLOAD" not in reply.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_mode", ["stdio", "http"])
async def test_public_sdk_reads_all_thousand_results_without_merging_pages(
    monkeypatch, transport_mode
):
    jobs = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(jobs.config, "transport_mode", transport_mode)
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    calls = []

    async def send(_sender, instance, command, params):
        assert instance == "Game@owned" and command == "get_test_job"
        calls.append(dict(params))
        start = int(params.get("cursor", "0"))
        end = min(start + params["page_size"], 1000)
        return {
            "success": True,
            "data": {
                "job_id": "job",
                "status": "failed",
                "completion_kind": "completed",
                "result_availability": "available",
                "result_page": {
                    "items": [{"fullName": f"Fixture.Test{i}"} for i in range(start, end)],
                    "total_count": 1000,
                    "has_more": end < 1000,
                    "next_cursor": str(end) if end < 1000 else None,
                    "generation": "g",
                },
            },
        }

    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", send)
    app = FastMCP("thousand-retained-results")
    app.tool(name="get_test_job")(jobs.get_test_job)
    names = []
    cursor = None
    async with Client(app) as client:
        while True:
            params = {"job_id": "job", "include_details": True, "page_size": 73, "wait_timeout": 30}
            if cursor is not None:
                params["cursor"] = cursor
            reply = await client.call_tool("get_test_job", params)
            page = json.loads(reply.content[0].text)["data"]["result_page"]
            names.extend(item["fullName"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert names == [f"Fixture.Test{i}" for i in range(1000)]
    assert len(calls) == 14


def test_export_catalog_and_selected_tenant_enforcement_in_production_middleware(tmp_path):
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
        import services.tools.export_test_job
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        import transport.unity_transport as transport
        from mcp.shared.exceptions import MCPError
        async def scenario():
            def denied(*args, **kwargs): raise AssertionError("Unexpected network")
            socket.socket.connect = socket.socket.connect_ex = denied
            metadata = [t for t in get_registered_tools() if t["name"] == "export_test_job"]
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
                        await registry.register_tools_for_session(sid, [ToolDefinitionModel(name="export_test_job")])
                        PluginHub._connections[sid] = SimpleNamespace(state=SimpleNamespace(user_id=owner,
                            plugin_generation=sid + "-generation", plugin_registered=True, plugin_session_id=sid),
                            client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
                            close=AsyncMock())
                        if hosted:
                            await auth.attach_socket(PluginHub._connections[sid], "plugin:" + sid)
                    middleware = UnityInstanceMiddleware()
                    if hosted:
                        await credentials.enter_async_context(auth.requester("requester:alice"))
                    app = FastMCP("test-export-catalog")
                    app.add_middleware(middleware)
                    tools.register_all_tools(app)
                    PluginHub._mcp = app
                    app.enable(tags={"group:testing"}, components={"tool"})
                    calls = []
                    async def dispatch(instance, command, params, *, user_id, **kwargs):
                        sid = await PluginHub._resolve_session_id(instance, user_id=user_id)
                        calls.append((sid, command, user_id))
                        return {"success": True, "data": {"content": "{}", "has_more": False, "next_cursor": None, "generation": "a" * 32, "sha256": "b" * 64, "total_bytes": 2, "mime_type": "application/json"}}
                    PluginHub.send_command_for_instance = dispatch
                    for protocol in ("legacy", "2026-07-28"):
                        calls.clear()
                        async with Client(app, mode=protocol) as client:
                            assert "export_test_job" in {t.name for t in await client.list_tools()}
                            reply = await client.call_tool("export_test_job", {"job_id": "job", "unity_instance": "owned@owned-hash"})
                            assert json.loads(reply.content[0].text)["success"] is True
                            assert calls == [("owned", "export_test_job", "alice" if hosted else None)]
                            if hosted:
                                try:
                                    reply = await client.call_tool("export_test_job", {"job_id": "job", "unity_instance": "foreign@foreign-hash"}, raise_on_error=False)
                                except MCPError: pass
                                else: assert reply.is_error
                                assert len(calls) == 1
                    # A connected populated catalog must not grant an absent native tool.
                    await registry.register_tools_for_session("owned", [ToolDefinitionModel(name="run_tests")])
                    async with Client(app) as client:
                        try:
                            reply = await client.call_tool("export_test_job", {"job_id": "job", "unity_instance": "owned@owned-hash"}, raise_on_error=False)
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
