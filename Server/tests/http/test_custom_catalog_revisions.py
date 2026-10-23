"""Revision publication and transport admission use the actual installed SDK."""

import textwrap
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize(
    "outcome", [None, {"changed": False}, {"changed": True, "global_component_changed": False}]
)
async def test_legacy_invalidation_never_broadcasts_hosted_tenant_changes(
    monkeypatch, hosted, outcome
):
    from contextlib import AsyncExitStack
    from weakref import WeakSet
    import anyio
    from mcp.server.connection import Connection
    from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher
    from core.config import config
    from transport.plugin_hub import PluginHub
    from transport.tool_list_notifier import ToolListNotifier

    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    notifier = ToolListNotifier()
    monkeypatch.setattr(PluginHub, "_catalog_notifications", notifier)
    async with AsyncExitStack() as stack:
        connections = []
        outputs = []
        for _tenant in ("tenant-a", "tenant-b"):
            incoming, incoming_reader = anyio.create_memory_object_stream(1)
            outgoing, output = anyio.create_memory_object_stream(1)
            for stream in (incoming, incoming_reader, outgoing, output):
                await stack.enter_async_context(stream)
            connection = Connection(
                JSONRPCDispatcher(incoming_reader, outgoing), protocol_version="2025-11-25"
            )
            stack.push_async_callback(connection.exit_stack.aclose)
            connections.append(connection)
            outputs.append(output)
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet(connections))
        try:
            await PluginHub._notify_committed_catalog(
                outcome,
                {
                    "user_id": "tenant-a",
                    "project_hash": "project-a",
                    "session_id": "generation-a",
                    "catalog_revision": 1,
                },
            )
            await anyio.wait_all_tasks_blocked()
            expected = bool(not hosted and outcome and outcome.get("changed"))
            for output in outputs:
                if expected:
                    message = output.receive_nowait()
                    assert message.message.method == "notifications/tools/list_changed"
                with pytest.raises(anyio.WouldBlock):
                    output.receive_nowait()
            if not expected:
                assert not notifier._deliveries and not notifier._tasks
        finally:
            await notifier.close()


def run_sdk(source, tmp_path, mode):
    # Isolate real SDK types from legacy integration collection-time stubs.
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


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_sdk_http_refresh_admission_reconnect_and_withdrawal(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from starlette.websockets import WebSocketState
        from core.config import config
        import services.custom_tool_service as module
        from transport.models import RegisterToolsMessage, RegisterMessage, CommandResultMessage
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def main():
            config.transport_mode = "http"
            config.http_remote_hosted = False
            app = FastMCP("http-last-admission")
            service = module.CustomToolService(app)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
            notify = PluginHub._notify_mcp_tool_list_changed = AsyncMock()
            sent = []
            def socket():
                ws = AsyncMock()
                ws.state = SimpleNamespace(user_id=None)
                ws.client_state = ws.application_state = WebSocketState.CONNECTED
                async def send(payload):
                    if payload["type"] == "execute":
                        sent.append(payload)
                        await hub._handle_command_result(ws, CommandResultMessage(id=payload["id"], result={"success": True, "data": payload["params"]}))
                ws.send_json = send
                return ws
            first = socket()
            await hub._handle_register(first, RegisterMessage(project_name="A", project_hash="a"))
            first_id = first.state.plugin_session_id
            async def publish(ws, rows):
                await hub._handle_register_tools(ws, RegisterToolsMessage(tools=rows))
            await publish(first, [{"name": "live", "parameters": [{"name": "old", "type": "integer", "required": True}]}])
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda value: "a"
            async with Client(app, mode=MODE) as client:
                assert "old" in (await client.list_tools())[0].input_schema["required"]
                entered, release = asyncio.Event(), asyncio.Event()
                original = PluginHub._get_connection
                async def blocked(sid):
                    result = await original(sid)
                    entered.set()
                    await release.wait()
                    return result
                PluginHub._get_connection = blocked
                pending = asyncio.create_task(client.call_tool("live", {"old": 1}))
                await asyncio.wait_for(entered.wait(), 2)
                await publish(first, [{"name": "live", "parameters": [{"name": "new", "type": "string", "required": True}]}])
                release.set()
                denied = json.loads((await pending).content[0].text)
                assert denied["data"]["reason"] == "custom_tool_catalog_changed" and sent == []
                PluginHub._get_connection = original
                assert "new" in (await client.list_tools())[0].input_schema["required"]
                result = await client.call_tool("live", {"new": "now"})
                assert json.loads(result.content[0].text)["success"] is True and len(sent) == 1
                ordinal = service._sources[("http", None, "a")].ordinal
                replacement = socket()
                await hub._handle_register(replacement, RegisterMessage(project_name="A", project_hash="a"))
                assert await client.list_tools() == []
                await publish(first, [{"name": "late_old"}])
                await hub.on_disconnect(first, 1001)
                assert await client.list_tools() == []
                await publish(replacement, [{"name": "new_generation"}])
                assert service._sources[("http", None, "a")].ordinal == ordinal
                assert [tool.name for tool in await client.list_tools()] == ["new_generation"]
                await hub.on_disconnect(replacement, 1001)
                assert await client.list_tools() == []
                assert await registry.get_session(first_id) is None
            await PluginHub.shutdown()
        asyncio.run(main())
    """,
        tmp_path,
        mode,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_manual_project_alias_preserves_connected_only_tools(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys, importlib
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        from models.models import ToolDefinitionModel
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        import services.custom_tool_service as module
        execute = importlib.import_module("services.tools.execute_custom_tool")

        async def main():
            config.transport_mode = "http"
            config.http_remote_hosted = False
            app = FastMCP("manual-alias-connected-catalog")
            app.tool(name="execute_custom_tool")(execute.execute_custom_tool)
            service = module.CustomToolService(app, project_scoped_tools=False)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            session, _ = await registry.register("session", "A", "a", "fixture")
            async with PluginHub._lock:
                await registry.register_tools_for_session("session", [ToolDefinitionModel(name="connected_only")],
                    _publish=lambda current, tools: PluginHub._publish_session_catalog(current, tools, registry._sessions, generation="session"))
            service._register_project_tools("friendly", [ToolDefinitionModel(name="manual_only")], project_hash="a")
            selected = AsyncMock(return_value="A@a")
            module.get_unity_instance_from_context = selected
            execute.get_unity_instance_from_context = selected
            execute.resolve_project_id_for_unity_instance = module.resolve_project_id_for_unity_instance
            sent = []
            async def send(fn, instance, command, params, **kwargs):
                assert kwargs["_catalog_guard"].session_id == "session"
                sent.append(command)
                return {"success": True}
            module.send_with_unity_instance = send
            assert {tool.name for tool in await service.list_registered_tools("friendly")} == {"manual_only", "connected_only"}
            async with Client(app, mode=MODE) as client:
                assert json.loads((await client.call_tool("connected_only", {})).content[0].text)["success"]
                assert json.loads((await client.call_tool("execute_custom_tool", {"tool_name": "connected_only"})).content[0].text)["success"]
                assert json.loads((await client.call_tool("manual_only", {})).content[0].text)["success"]
            assert sent == ["connected_only", "connected_only", "manual_only"]
            await PluginHub.shutdown()
        asyncio.run(main())
    """,
        tmp_path,
        mode,
    )


@pytest.mark.asyncio
async def test_hosted_same_hash_catalogs_are_tenant_owned(monkeypatch):
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    app = FastMCP("tenant-revision-isolation")
    service = CustomToolService(app)
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=app)
    lock = PluginHub._lock
    assert lock is not None
    try:
        for user in ("a", "b"):
            await registry.register(user, "Same", "same", "fixture", user_id=user)
            async with lock:  # pylint: disable=not-async-context-manager
                await registry.register_tools_for_session(
                    user,
                    [ToolDefinitionModel(name=f"only_{user}")],
                    _publish=lambda session, tools: PluginHub._publish_session_catalog(
                        session, tools, registry._sessions, generation=session.session_id
                    ),
                )
        assert [tool.name for tool in await service.list_registered_tools("same", user_id="a")] == [
            "only_a"
        ]
        assert await service.get_tool_definition("same", "only_b", user_id="a") is None
        assert await service.get_tool_definition("same", "only_a", user_id=None) is None
        assert await app.list_tools() == []
        async with lock:  # pylint: disable=not-async-context-manager
            await PluginHub._unregister_catalog(registry, "a")
        assert [tool.name for tool in await service.list_registered_tools("same", user_id="b")] == [
            "only_b"
        ]
    finally:
        await PluginHub.shutdown()


@pytest.mark.asyncio
async def test_withdrawal_releases_source_capacity_and_foreign_sdk_ownership(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService
    from transport.models import RegisterMessage, RegisterToolsMessage
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    app = FastMCP("source-lifetime")
    service = CustomToolService(app)
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=app)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    monkeypatch.setattr(PluginHub, "_notify_mcp_tool_list_changed", AsyncMock())
    try:
        for index in range(257):
            ws = AsyncMock()
            ws.state = SimpleNamespace(user_id=None)
            await hub._handle_register(ws, RegisterMessage(project_hash=str(index)))
            assert len(service._sources) == 1
            await hub.on_disconnect(ws, 1001)
            assert service._sources == {} and await registry.list_sessions() == {}
        ws = AsyncMock()
        ws.state = SimpleNamespace(user_id=None)
        await hub._handle_register(ws, RegisterMessage(project_hash="same"))
        await hub._handle_register_tools(
            ws, RegisterToolsMessage(tools=[ToolDefinitionModel(name="shared")])
        )
        old_ordinal = service._sources[("http", None, "same")].ordinal
        owned = service._owned_tools["shared"]

        @app.tool(name="shared")
        async def foreign() -> dict:
            return {"foreign": True}

        component = app._local_provider._components[owned.key]
        await hub.on_disconnect(ws, 1001)
        assert await registry.list_sessions() == {} and not PluginHub._connections
        assert service._sources == {} and app._local_provider._components[owned.key] is component
        new = AsyncMock()
        new.state = SimpleNamespace(user_id=None)
        await hub._handle_register(new, RegisterMessage(project_hash="same"))
        assert service._sources[("http", None, "same")].ordinal > old_ordinal
        assert app._local_provider._components[owned.key] is component
    finally:
        await PluginHub.shutdown()


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("failure", ["after_insert", "after_delete", "reentrant"])
def test_hub_catalog_and_visibility_roll_back_together(tmp_path, mode, failure):
    run_sdk(
        "FAILURE = "
        + repr(failure)
        + "\n"
        + textwrap.dedent(r"""
        import asyncio, json, sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from starlette.websockets import WebSocketState
        from core.config import config
        import services.custom_tool_service as module
        import services.registry as inventory
        from transport.models import RegisterToolsMessage
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def main():
            config.transport_mode = "http"
            config.http_remote_hosted = False
            app = FastMCP("atomic-hub-catalog")
            inventory.get_group_tool_names = lambda: {"core": ["gone"]}
            service = module.CustomToolService(app)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            await registry.register("session", "A", "hash", "fixture")
            socket = AsyncMock()
            socket.state = SimpleNamespace(plugin_registered=True, plugin_session_id="session", plugin_generation="socket-generation", user_id=None)
            socket.client_state = socket.application_state = WebSocketState.CONNECTED
            PluginHub._connections["session"] = socket
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
            notify = PluginHub._notify_mcp_tool_list_changed = AsyncMock()
            module.get_unity_instance_from_context = AsyncMock(return_value="A@hash")
            module.resolve_project_id_for_unity_instance = lambda instance: "hash"
            sent = []
            async def send(fn, instance, command, params, **kwargs):
                sent.append((command, params))
                return {"success": True, "data": params}
            module.send_with_unity_instance = send
            async def publish(rows):
                await hub._handle_register_tools(socket, RegisterToolsMessage(tools=rows))
            await publish([{"name": "stable", "parameters": [{"name": "old", "type": "integer"}]}, {"name": "gone"}])
            await publish([{"name": "stable", "parameters": [{"name": "old", "type": "integer"}]}, {"name": "gone"}])
            assert notify.await_count == 1
            async with Client(app, mode=MODE) as client:
                before = {tool.name: tool.input_schema for tool in await client.list_tools()}
                original_tools = (await registry.get_session("session")).tools
                original_revision = getattr(await registry.get_session("session"), "tools_revision", 0)
                original_transforms = app._transforms
                original_component = next(value for value in app._local_provider._components.values() if value.name == "stable")
                stable_key = original_component.key
                gone_key = next(value.key for value in app._local_provider._components.values() if value.name == "gone")
                add = app._local_provider.add_tool
                remove = app._local_provider._remove_component
                if FAILURE == "after_insert":
                    def fail(tool):
                        value = add(tool)
                        if tool.name == "late": raise RuntimeError("owned add failed after mutation")
                        return value
                    app._local_provider.add_tool = fail
                elif FAILURE == "after_delete":
                    def fail(key):
                        remove(key)
                        if key == gone_key: raise RuntimeError("owned remove failed after mutation")
                    app._local_provider._remove_component = fail
                elif FAILURE == "reentrant":
                    def fail(tool):
                        value = add(tool)
                        service.register_global_tools([module.ToolDefinitionModel(name="nested")])
                        return value
                    app._local_provider.add_tool = fail
                failed = False
                try:
                    await publish([{"name": "stable", "parameters": [{"name": "new", "type": "string"}]}, {"name": "late"}])
                except (ValueError, RuntimeError):
                    failed = True
                assert failed
                session = await registry.get_session("session")
                assert session.tools is original_tools and session.tools_revision == original_revision
                assert app._transforms is original_transforms
                assert app._local_provider._components[stable_key] is original_component
                assert {tool.name: tool.input_schema for tool in await client.list_tools()} == before
                assert notify.await_count == 1
                result = await client.call_tool("stable", {"old": 3})
                assert json.loads(result.content[0].text)["success"] is True
                assert sent == [("stable", {"old": 3})]
            await PluginHub.shutdown()
        asyncio.run(main())
        """),
        tmp_path,
        mode,
    )
