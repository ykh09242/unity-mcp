"""Desired revision receipts must describe committed catalogs, not frame sends."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def run_sdk(source, tmp_path, mode):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_catalog_byte_boundary_does_not_count_absent_receipt_fields(tmp_path):
    run_sdk(
        r"""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP
        from starlette.websockets import WebSocketState
        from core.config import config
        import services.custom_tool_service as custom
        from transport.models import RegisterMessage, RegisterToolsMessage
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def main():
            config.transport_mode = "http"
            config.http_remote_hosted = False
            app = FastMCP("legacy-settings-size")
            service = custom.CustomToolService(app)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
            ws = AsyncMock()
            ws.state = SimpleNamespace(user_id=None)
            ws.client_state = ws.application_state = WebSocketState.CONNECTED
            await hub._handle_register(ws, RegisterMessage(project_hash="legacy-size-owned"))
            PluginHub._notify_committed_catalog = AsyncMock()
            payload = RegisterToolsMessage(tools=[{"name": "legacy_size_owned", "description": ""}])
            exclude = {"settings_epoch", "desired_revision"}
            base = len(payload.model_dump_json(exclude=exclude).encode("utf-8"))
            payload.tools[0].description = "x" * (512 * 1024 - base)
            assert len(payload.model_dump_json(exclude=exclude).encode("utf-8")) == 512 * 1024
            try:
                await hub.on_receive(ws, payload.model_dump(exclude=exclude))
                assert ws.close.await_count == 0, "Absent receipt fields reduced the legacy byte budget"
                snapshot = await registry.get_catalog_snapshot("legacy-size-owned")
                assert snapshot[2] is True and set(snapshot[3]) == {"legacy_size_owned"}
                assert not any(call.args[0].get("type") == "tool_settings_result" for call in ws.send_json.await_args_list)
                payload.tools[0].description += "x"
                await hub.on_receive(ws, payload.model_dump(exclude=exclude))
                assert ws.close.await_count == 1
                assert ws.close.await_args.kwargs["code"] == 4400
                assert (await registry.get_catalog_snapshot("legacy-size-owned"))[1:] == snapshot[1:]
            finally:
                for task in list(PluginHub._ping_tasks.values()):
                    task.cancel()
                await asyncio.gather(*PluginHub._ping_tasks.values(), return_exceptions=True)
        asyncio.run(main())
        """,
        tmp_path,
        "legacy",
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_sdk_receipts_match_catalog_commit_rejection_retry_and_reconnect(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from starlette.websockets import WebSocketState
        from core.config import config
        import services.custom_tool_service as custom
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.models import RegisterMessage

        async def main():
            config.transport_mode = "http"
            config.http_remote_hosted = False
            app = FastMCP("settings-receipt")
            service = custom.CustomToolService(app)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
            notifications = []
            original_notify = PluginHub._notify_committed_catalog
            async def notify(outcome, scope):
                notifications.append((outcome, scope))
            PluginHub._notify_committed_catalog = notify
            frames = []
            def socket():
                ws = AsyncMock()
                ws.state = SimpleNamespace(user_id=None)
                ws.client_state = ws.application_state = WebSocketState.CONNECTED
                async def send(frame):
                    frames.append((ws, frame))
                ws.send_json = send
                return ws
            async def register(ws):
                await hub._handle_register(ws, RegisterMessage(project_name="Receipt", project_hash="settings-owned", capabilities=["tool_settings_ack_v1"]))
            epoch = "1" * 32
            def request(revision, parameter="first"):
                return {"type": "register_tools", "settings_epoch": epoch, "desired_revision": revision,
                    "tools": [{"name": "settings_owned_tool", "parameters": [{"name": parameter, "type": "string", "required": True}]}]}
            async def publish(ws, body):
                before = len(frames)
                notifications_before = len(notifications)
                await hub.on_receive(ws, body)
                receipts = [row for owner, row in frames[before:] if owner is ws and row.get("type") == "tool_settings_result"]
                assert len(receipts) == 1, ("A completed frame/catalog path needs exactly one correlated result", frames[before:])
                assert len(notifications) - notifications_before == int(receipts[0]["status"] == "applied")
                return receipts[0]
            first = socket()
            await register(first)
            custom.get_unity_instance_from_context = AsyncMock(return_value="Receipt@settings-owned")
            custom.resolve_project_id_for_unity_instance = lambda _value: "settings-owned"
            try:
                async with Client(app, mode=MODE) as client:
                    applied = await publish(first, request(1))
                    assert applied["status"] == "applied" and applied["desired_revision"] == 1
                    assert applied["settings_epoch"] == epoch and applied["session_id"] == first.state.plugin_session_id
                    assert applied["enabled_tool_count"] == 1
                    revision = applied["catalog_revision"]
                    snapshot = await registry.get_catalog_snapshot("settings-owned")
                    assert snapshot[1] == revision and snapshot[2] is True and set(snapshot[3]) == {"settings_owned_tool"}
                    listed = next(tool for tool in await client.list_tools() if tool.name == "settings_owned_tool")
                    assert listed.input_schema["required"] == ["first"]
                    duplicate = await publish(first, request(1))
                    assert duplicate["status"] == "applied" and duplicate["catalog_revision"] == revision
                    conflict = await publish(first, request(1, "conflicting"))
                    assert conflict["status"] == "rejected" and conflict["reason"] == "revision_conflict"
                    malformed = request(2)
                    malformed["tools"].append({"name": "bad-last", "parameters": [{"name": "x", "required": "not-a-boolean"}]})
                    rejected = await publish(first, malformed)
                    assert rejected["status"] == "rejected" and rejected["desired_revision"] == 2
                    assert (await registry.get_catalog_snapshot("settings-owned"))[1:] == snapshot[1:]
                    assert (next(tool for tool in await client.list_tools() if tool.name == "settings_owned_tool")).input_schema == listed.input_schema
                    replacement = await publish(first, request(2, "second"))
                    assert replacement["status"] == "applied" and replacement["catalog_revision"] == revision + 1
                    stale = await publish(first, request(1))
                    assert stale["status"] == "rejected" and stale["reason"] == "stale_revision"
                    assert (next(tool for tool in await client.list_tools() if tool.name == "settings_owned_tool")).input_schema["required"] == ["second"]
                    second = socket()
                    await register(second)
                    fresh = await publish(second, request(2, "second"))
                    assert fresh["status"] == "applied" and fresh["session_id"] != applied["session_id"]
                    assert (await registry.get_catalog_snapshot("settings-owned"))[0] == fresh["session_id"]
            finally:
                PluginHub._notify_committed_catalog = original_notify
                for task in list(PluginHub._ping_tasks.values()):
                    task.cancel()
                await asyncio.gather(*PluginHub._ping_tasks.values(), return_exceptions=True)
        asyncio.run(main())
        """,
        tmp_path,
        mode,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_stdio_provenance_is_captured_at_own_commit_before_notification(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        from transport.legacy import unity_connection as legacy
        from transport.plugin_hub import PluginHub
        from services.custom_tool_service import CustomToolService
        from services.tools import sync_tool_visibility_from_unity

        async def main():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            app = FastMCP("settings-stdio-provenance")
            service = CustomToolService(app)
            PluginHub._mcp = app
            PluginHub._unity_visibility_signature = None
            conn = legacy.UnityConnection(port=1, instance_id="Owned@settings-stdio")
            conn.sock = object()
            conn.session_generation = "owned-generation"
            legacy._unity_connection_pool = legacy.UnityConnectionPool()
            legacy._unity_connection_pool._connections[conn.instance_id] = conn
            legacy.get_unity_connection = lambda *_args: conn
            legacy.get_authenticated_stdio_generation = AsyncMock(return_value=conn.session_generation)
            revision = 1
            def response(*_args, **_kwargs):
                return {"success": True, "data": {"settings_epoch": "2" * 32, "desired_revision": revision,
                    "tools": [{"name": "stdio_settings_owned", "is_built_in": False,
                        "parameters": [{"name": "value_" + str(revision), "type": "string"}]}]}}
            conn.send_command = response
            entered, release = asyncio.Event(), asyncio.Event()
            notifications = 0
            async def notify(*_args, **_kwargs):
                nonlocal notifications
                notifications += 1
                if notifications == 1:
                    entered.set()
                    await release.wait()
            PluginHub._notify_mcp_tool_list_changed = notify
            first = asyncio.create_task(sync_tool_visibility_from_unity(conn.instance_id, notify=True))
            await asyncio.wait_for(entered.wait(), 3)
            revision = 2
            second = await sync_tool_visibility_from_unity(conn.instance_id, notify=True)
            release.set()
            first_result = await first
            assert first_result["settings_sync"]["desired_revision"] == 1
            assert second["settings_sync"]["desired_revision"] == 2
            assert first_result["settings_sync"]["catalog_revision"] + 1 == second["settings_sync"]["catalog_revision"]
            assert first_result["settings_sync"]["settings_epoch"] == "2" * 32
            assert first_result["settings_sync"]["source_generation"] == "owned-generation"
            assert first_result["settings_sync"]["status"] == "applied"
            duplicate = await sync_tool_visibility_from_unity(conn.instance_id, notify=False)
            assert duplicate["settings_sync"] == second["settings_sync"]
            async with Client(app, mode=MODE) as client:
                listed = next(tool for tool in await client.list_tools() if tool.name == "stdio_settings_owned")
                assert "value_2" in listed.input_schema["properties"]
            conn.send_command = lambda *_a, **_k: {"success": True, "data": {"tools": [{"name": "unknown_old_format", "enabled": True}]}}
            unknown = await sync_tool_visibility_from_unity(conn.instance_id, notify=False)
            assert unknown["settings_sync"]["status"] == "unconfirmed"
            assert "settings_epoch" not in unknown["settings_sync"]
        asyncio.run(main())
        """,
        tmp_path,
        mode,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("fault", ["publish", "send", "notify"])
def test_sdk_commit_receipt_and_notification_failures_are_separate(tmp_path, mode, fault):
    run_sdk(
        "FAULT = "
        + repr(fault)
        + "\n"
        + textwrap.dedent(
            r"""
            import asyncio, sys
            from types import SimpleNamespace
            from unittest.mock import AsyncMock
            sys.path.insert(0, "src")
            from fastmcp import FastMCP, Client
            from starlette.websockets import WebSocketState
            from core.config import config
            from transport.plugin_hub import PluginHub
            from transport.plugin_registry import PluginRegistry
            from transport.models import RegisterMessage
            from services.custom_tool_service import CustomToolService

            async def main():
                config.http_remote_hosted = False
                app = FastMCP("settings-fault-boundaries")
                service = CustomToolService(app)
                registry = PluginRegistry()
                PluginHub.configure(registry, mcp=app)
                hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
                ws = AsyncMock()
                ws.state = SimpleNamespace(user_id=None)
                ws.client_state = ws.application_state = WebSocketState.CONNECTED
                frames, notices = [], []
                fail_send = False
                async def send(frame):
                    frames.append(frame)
                    if fail_send and frame.get("type") == "tool_settings_result":
                        raise RuntimeError("controlled lost receipt")
                ws.send_json = send
                PluginHub._notify_committed_catalog = AsyncMock()
                await hub._handle_register(ws, RegisterMessage(project_name="Owned", project_hash="fault-owned", capabilities=["tool_settings_ack_v1"]))
                def body(revision):
                    return {"type": "register_tools", "settings_epoch": "3" * 32, "desired_revision": revision,
                        "tools": [{"name": "fault_owned", "parameters": [{"name": "value_" + str(revision), "type": "string"}]}]}
                await hub.on_receive(ws, body(1))
                old = await registry.get_catalog_snapshot("fault-owned")
                old_components = dict(app._local_provider._components)
                frames.clear()
                async def notify(outcome, scope):
                    notices.append((outcome, scope))
                    if FAULT == "notify":
                        raise RuntimeError("controlled notification failure")
                PluginHub._notify_committed_catalog = notify
                original_add = app.add_tool
                if FAULT == "publish":
                    def insert_then_fail(tool, *args, **kwargs):
                        original_add(tool, *args, **kwargs)
                        raise RuntimeError("controlled late SDK insert failure")
                    app.add_tool = insert_then_fail
                fail_send = FAULT == "send"
                try:
                    await hub.on_receive(ws, body(2))
                    result = [frame for frame in frames if frame.get("type") == "tool_settings_result"]
                    assert len(result) == 1
                    current = await registry.get_catalog_snapshot("fault-owned")
                    if FAULT == "publish":
                        assert result[0]["status"] == "rejected" and result[0]["reason"] == "publication_failed"
                        assert current == old and notices == []
                        assert app._local_provider._components == old_components
                        assert all(app._local_provider._components[key] is value for key, value in old_components.items())
                    else:
                        assert result[0]["status"] == "applied" and len(notices) == 1
                        assert current[1] == old[1] + 1
                        assert result[0]["catalog_revision"] == current[1]
                    async with Client(app, mode=MODE) as client:
                        tool = next(tool for tool in await client.list_tools() if tool.name == "fault_owned")
                        assert "value_" + ("1" if FAULT == "publish" else "2") in tool.input_schema["properties"]
                finally:
                    app.add_tool = original_add
                    for task in list(PluginHub._ping_tasks.values()):
                        task.cancel()
                    await asyncio.gather(*PluginHub._ping_tasks.values(), return_exceptions=True)
            asyncio.run(main())
            """
        ),
        tmp_path,
        mode,
    )


@pytest.mark.parametrize(
    "epoch,revision",
    [
        ("1" * 32, True),
        ("1" * 32, "1"),
        ("1" * 32, 0),
        ("1" * 32, 9007199254740992),
        ("g" * 32, 1),
        ("1" * 33, 1),
        (None, 1),
    ],
)
def test_settings_correlation_is_strict_and_bounded(epoch, revision):
    from pydantic import ValidationError
    from transport.models import ToolSettingsCorrelation

    with pytest.raises(ValidationError):
        ToolSettingsCorrelation(settings_epoch=epoch, desired_revision=revision)
