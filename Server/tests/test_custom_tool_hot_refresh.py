"""Actual SDK discovery/invocation across atomic owned catalog generations."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_stdio_authoritative_snapshots_and_generation_evidence(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        from transport.legacy import unity_connection as legacy
        from transport.plugin_hub import PluginHub
        import services.custom_tool_service as module
        from services.tools import sync_tool_visibility_from_unity

        async def main():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            app = FastMCP("stdio-revision-snapshots")
            service = module.CustomToolService(app)
            PluginHub._mcp = app
            PluginHub._unity_visibility_signature = None
            conn = legacy.UnityConnection(port=1, instance_id="A@a")
            conn.sock = object()
            conn.session_generation = "authenticated-one"
            legacy._unity_connection_pool = legacy.UnityConnectionPool()
            legacy._unity_connection_pool._connections[conn.instance_id] = conn
            legacy.get_unity_connection = lambda *a: conn
            legacy.get_authenticated_stdio_generation = AsyncMock(side_effect=lambda *a: conn.session_generation)
            rows = [{"name": "stdio_hot", "is_built_in": False, "parameters": [{"name": "old", "type": "integer", "required": True}]}]
            conn.send_command = lambda *a, **kw: {"success": True, "data": {"tools": rows}}
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda value: "a"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True})
            async def sync():
                return await sync_tool_visibility_from_unity("A@a", notify=False)
            assert (await sync())["custom_tool_count"] == 1
            async with Client(app, mode=MODE) as client:
                schema = (await client.list_tools())[0].input_schema
                assert "old" in schema["required"]
                rows = [{"name": "stdio_hot", "is_built_in": False, "parameters": [{"name": "new", "type": "object", "required": True}]}]
                assert (await sync())["synced"]
                assert "new" in (await client.list_tools())[0].input_schema["required"]
                previous = service._sources[("stdio", None, "a")]
                # A valid member cannot conceal a malformed disabled member.
                rows = [{"name": "different", "is_built_in": False}, {"name": "broken", "is_built_in": False, "enabled": False, "parameters": [1]}]
                assert "error" in await sync()
                assert service._sources[("stdio", None, "a")] is previous
                assert [tool.name for tool in await client.list_tools()] == ["stdio_hot"]
                rows = [{"name": "old_peer", "enabled": True}]
                assert (await sync())["synced"]
                assert service._sources[("stdio", None, "a")] is previous
                rows = []
                conn.session_generation = "authenticated-two"
                assert "error" in await sync()  # Old generation evidence cannot authorize [] deletion.
                assert service._sources[("stdio", None, "a")] is previous
                rows = [{"name": "stdio_hot", "is_built_in": False, "enabled": False}]
                assert (await sync())["synced"]  # Raw extended evidence survives disabled filtering.
                assert await client.list_tools() == []
                rows = []
                assert (await sync())["synced"]  # Same authenticated generation now proves empty.
        asyncio.run(main())
    """,
        tmp_path,
        mode,
    )


def test_final_merged_catalog_and_logical_source_bounds(monkeypatch):
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(FastMCP("bounded-source-state"))
    tools = [ToolDefinitionModel(name=f"tool_{i}") for i in range(256)]
    service._register_project_tools("manual", tools)
    old = service._sources[("manual", None, "manual")]
    with pytest.raises(ValueError, match="256 tools"):
        service._register_project_tools("manual", [ToolDefinitionModel(name="overflow")])
    assert service._sources[old.key] is old
    service._register_project_tools(
        "bytes", [ToolDefinitionModel(name="a", description="a" * 270000)]
    )
    with pytest.raises(ValueError, match="512 KiB"):
        service._register_project_tools(
            "bytes", [ToolDefinitionModel(name="b", description="b" * 270000)]
        )
    assert list(service._project_tools["bytes"]) == ["a"]
    for index in range(254):
        service.replace_project_catalog(str(index), [], generation="live", publish_global=False)
    with pytest.raises(ValueError, match="source limit"):
        service.replace_project_catalog("overflow", [], publish_global=False)
    assert len(service._sources) == 256
    for index in range(254):
        service.replace_project_catalog(str(index), [], withdraw=True, publish_global=False)
    assert len(service._sources) == 2
    service.replace_project_catalog(
        "new_participation", [], generation="live", publish_global=False
    )


@pytest.mark.asyncio
async def test_noop_hash_refresh_and_foreign_component_ownership(monkeypatch):
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    app = FastMCP("identity-aware-publication")
    service = CustomToolService(app, project_scoped_tools=False)
    definition = ToolDefinitionModel(name="owned")
    first = service.replace_project_catalog("a", [definition], kind="manual", project_hash="old")
    component = service._owned_tools["owned"]
    second = service.replace_project_catalog("a", [definition], kind="manual", project_hash="old")
    assert not second["changed"] and first["revision"] == second["revision"]
    service.replace_project_catalog("a", [definition], kind="manual", project_hash="new")
    assert service.get_project_id_for_hash("old") is None
    assert service.get_project_id_for_hash("new") == "a"
    assert service._owned_tools["owned"] is component

    @app.tool(name="owned")
    async def foreign(value: int) -> dict:
        return {"foreign": value}

    foreign_component = app._local_provider._components[component.key]
    before = service._sources
    with pytest.raises(ValueError, match="ownership"):
        service.replace_project_catalog(
            "a", [ToolDefinitionModel(name="owned", description="replace foreign")], kind="manual"
        )
    assert service._sources is before
    service.replace_project_catalog("a", [], kind="manual")
    assert ("manual", None, "a") not in service._sources
    assert "a" not in service._project_tools
    assert service.get_project_id_for_hash("new") is None
    assert app._local_provider._components[component.key] is foreign_component


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_delayed_stdio_refresh_cannot_overwrite_newer_same_generation(tmp_path, mode):
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
            app = FastMCP("delayed-source-cas")
            service = CustomToolService(app)
            PluginHub._mcp = app
            PluginHub._unity_visibility_signature = None
            conn = legacy.UnityConnection(port=1, instance_id="A@a")
            conn.sock = object()
            conn.session_generation = "authenticated-generation"
            legacy._unity_connection_pool = legacy.UnityConnectionPool()
            legacy._unity_connection_pool._connections[conn.instance_id] = conn
            legacy.get_unity_connection = lambda *a: conn
            snapshots = [[{"name": "old", "is_built_in": False}], [{"name": "new", "is_built_in": False}]]
            conn.send_command = lambda *a, **kw: {"success": True, "data": {"tools": snapshots.pop(0)}}
            entered, release = asyncio.Event(), asyncio.Event()
            checks = 0
            async def generation(*args):
                nonlocal checks
                checks += 1
                if checks == 1:
                    entered.set()
                    await release.wait()
                return conn.session_generation
            legacy.get_authenticated_stdio_generation = generation
            stale = asyncio.create_task(sync_tool_visibility_from_unity("A@a", notify=False))
            await asyncio.wait_for(entered.wait(), 2)
            assert (await sync_tool_visibility_from_unity("A@a", notify=False))["synced"]
            release.set()
            assert "error" in await stale
            async with Client(app, mode=MODE) as client:
                assert [tool.name for tool in await client.list_tools()] == ["new"]
            assert list(service._sources[("stdio", None, "a")].tools) == ["new"]
        asyncio.run(main())
    """,
        tmp_path,
        mode,
    )


@pytest.mark.asyncio
async def test_polling_revision_change_preserves_job_id_without_replay(monkeypatch):
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    import services.custom_tool_service as module

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(module.CustomToolService, "_instance", None)
    service = module.CustomToolService(FastMCP("captured-job-policy"))
    old = ToolDefinitionModel(
        name="job", requires_polling=True, max_poll_seconds=1, poll_action="old_status"
    )
    service._register_project_tools("a", [old])
    sent = []

    async def send(_fn, _instance, command, params, **_kw):
        sent.append((command, params))
        service._register_project_tools(
            "a", [ToolDefinitionModel(name="job", poll_action="new_status")]
        )
        return {"success": True, "data": {"job_id": "old-job"}, "_mcp_status": "pending"}

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    response = await service.execute_tool("a", "job", "A@a", {})
    assert response.data == {
        "reason": "custom_tool_catalog_changed",
        "dispatched": False,
        "job_id": "old-job",
    }
    assert response.hint == "inspect_state_before_retry"
    assert sent == [("job", {})] and service._active_polls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["io_lock", "admission", "reconnect"])
async def test_stdio_actual_worker_rechecks_catalog_and_connection_before_wire(
    monkeypatch, barrier
):
    import asyncio
    import threading
    from fastmcp import FastMCP
    from core.config import config
    from models.models import ToolDefinitionModel
    from transport.legacy import unity_connection as legacy
    import services.custom_tool_service as module

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(module.CustomToolService, "_instance", None)
    service = module.CustomToolService(FastMCP("worker-admission-fence"))
    definition = ToolDefinitionModel(name="mutate")
    service.replace_project_catalog("a", [definition], kind="stdio", generation="old")
    writes = []

    class Socket:
        timeout = 1

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

        def sendall(self, value):
            writes.append(value)

    conn = legacy.UnityConnection(port=1, instance_id="A@a")
    conn.sock = Socket()
    conn.session_generation = "old"
    monkeypatch.setattr(legacy, "get_unity_connection", lambda *a: conn)
    monkeypatch.setattr(legacy, "read_status_file", lambda *a: None)
    monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
    monkeypatch.setattr(
        conn,
        "receive_full_response",
        lambda *a, **kw: b'{"status":"success","result":{"success":true}}',
    )
    entered = threading.Event()
    original = legacy._send_command_with_retry

    def worker(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(legacy, "_send_command_with_retry", worker)
    gate = conn._async_admission(asyncio.get_running_loop())
    if barrier == "admission":
        await gate.acquire()
        selected = asyncio.Event()
        select = module._CatalogAdmission.select_stdio

        def record_selection(guard, connection):
            select(guard, connection)
            selected.set()

        monkeypatch.setattr(module._CatalogAdmission, "select_stdio", record_selection)
    elif barrier == "io_lock":
        conn._io_lock.acquire()
    else:

        def reconnect():
            conn.sock = Socket()
            conn.session_generation = "new"

        monkeypatch.setattr(conn, "_ensure_live_connection", reconnect)

    task = asyncio.create_task(service.execute_tool("a", "mutate", "A@a", {}))
    try:
        if barrier == "admission":
            await asyncio.wait_for(selected.wait(), 2)
        else:
            assert await asyncio.to_thread(entered.wait, 2)
        if barrier != "reconnect":
            service.replace_project_catalog(
                "a",
                [ToolDefinitionModel(name="mutate", description="next")],
                kind="stdio",
                generation="old",
            )
    finally:
        if barrier == "io_lock":
            conn._io_lock.release()
        elif barrier == "admission":
            gate.release()
    result = await asyncio.wait_for(task, 3)
    assert result.data == {"reason": "custom_tool_catalog_changed", "dispatched": False}
    assert writes == []
    monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
    service.replace_project_catalog(
        "a", [definition], kind="stdio", generation=conn.session_generation
    )
    assert (await service.execute_tool("a", "mutate", "A@a", {})).success
    assert len(writes) in (1, 2)  # Existing legacy/framed wire mode; one payload admission.


@pytest.mark.parametrize("change", ["pool_owner", "generation", "busy"])
def test_stdio_publication_rechecks_exact_cached_connection(monkeypatch, change):
    import threading
    from transport.legacy import unity_connection as legacy

    pool = legacy.UnityConnectionPool()
    connection = legacy.UnityConnection(port=1, instance_id="A@a")
    connection.sock = object()
    connection.session_generation = "old"
    pool._connections[connection.instance_id] = connection
    monkeypatch.setattr(legacy, "_unity_connection_pool", pool)
    entered, release = threading.Event(), threading.Event()

    def busy():
        with connection._io_lock:
            entered.set()
            release.wait(2)

    thread = None
    if change == "pool_owner":
        pool._connections[connection.instance_id] = legacy.UnityConnection(
            port=1, instance_id="A@a"
        )
    elif change == "generation":
        connection.session_generation = "new"
    else:
        thread = threading.Thread(target=busy)
        thread.start()
        assert entered.wait(2)
    committed = []
    try:
        with pytest.raises(ValueError):
            legacy._commit_stdio_catalog(connection, "old", lambda: committed.append(True))
        assert committed == []
    finally:
        release.set()
        if thread is not None:
            thread.join(2)
            assert not thread.is_alive()


def run_sdk(source, tmp_path, mode):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_owner_schema_refresh_removal_and_suspended_old_handler(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import FastMCP, Client
        from core.config import config
        import services.custom_tool_service as module

        async def main():
            config.http_remote_hosted = False
            app = FastMCP("owner-hot-refresh")
            service = module.CustomToolService(app, project_scoped_tools=False)
            entered, release = asyncio.Event(), asyncio.Event()
            blocking = False
            async def selected(ctx):
                if blocking:
                    entered.set()
                    await release.wait()
                return "A@a"
            module.get_unity_instance_from_context = selected
            module.resolve_project_id_for_unity_instance = lambda value: value.rsplit("@", 1)[1]
            sent = []
            async def send(fn, instance, command, params, **kwargs):
                sent.append((instance, command, params))
                return {"success": True, "data": {"received": params}}
            module.send_with_unity_instance = send
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.http_app()), base_url="http://owned") as registrations:
                async def register(tools, replace=False):
                    result = await registrations.post("/register-tools", json={"project_id": "a", "project_hash": "a", "tools": tools, "replace": replace})
                    assert result.status_code == 200, result.text
                    return result.json()
                await register([{"name": "hot_probe", "parameters": [{"name": "count", "type": "integer", "required": True}]}, {"name": "removed_probe"}])
                async with Client(app, mode=MODE) as client:
                    old = {tool.name: tool for tool in await client.list_tools()}
                    assert old["hot_probe"].input_schema["properties"]["count"]["type"] == "integer"
                    blocking = True
                    old_call = asyncio.create_task(client.call_tool("hot_probe", {"count": 1}))
                    await asyncio.wait_for(entered.wait(), 2)
                    updated = {"name": "hot_probe", "description": "new generation", "structured_output": False, "parameters": [
                        {"name": "count", "type": "object", "required": True},
                        {"name": "label", "type": "string", "required": True},
                        {"name": "options", "type": "object", "required": False, "default_value": '{"nested":{"enabled":false}}'},
                    ]}
                    await register([updated], replace=True)
                    release.set()
                    stale = json.loads((await old_call).content[0].text)
                    assert stale["success"] is False and (stale.get("data") or {}).get("reason") == "custom_tool_schema_changed", stale
                    assert sent == []
                    blocking = False
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    assert set(tools) == {"hot_probe"}
                    assert tools["hot_probe"].input_schema["properties"]["count"]["type"] == "object"
                    assert set(tools["hot_probe"].input_schema["required"]) == {"count", "label"}
                    assert tools["hot_probe"].output_schema is None
                    invalid = await client.call_tool("hot_probe", {"count": 1}, raise_on_error=False)
                    assert invalid.is_error and sent == []
                    result = await client.call_tool("hot_probe", {"count": {"nested": [1, {"value": True}]}, "label": "new"})
                    assert result.structured_content is None
                    assert json.loads(result.content[0].text)["success"] is True
                    assert sent[-1][2]["options"] == {"nested": {"enabled": False}}
                    before = tools["hot_probe"].input_schema
                    await register([])
                    assert {t.name: t.input_schema for t in await client.list_tools()} == {"hot_probe": before}
                    cleared = await register([], replace=True)
                    assert cleared["removed"] == ["hot_probe"]
                    assert await client.list_tools() == []
                    missing = await client.call_tool("hot_probe", {}, raise_on_error=False)
                    assert missing.is_error and len(sent) == 1
                async with Client(app, mode=MODE) as fresh:
                    assert await fresh.list_tools() == []
        asyncio.run(main())
        """,
        tmp_path,
        mode,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_same_name_owner_updates_fallback_and_selected_schema_refusal(tmp_path, mode):
    run_sdk(
        r"""
        import asyncio, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import FastMCP, Client
        from core.config import config
        from services.tools.execute_custom_tool import execute_custom_tool
        import services.custom_tool_service as module
        import importlib

        async def main():
            config.http_remote_hosted = False
            app = FastMCP("source-collision-refresh")
            app.tool(name="execute_custom_tool")(execute_custom_tool)
            service = module.CustomToolService(app, project_scoped_tools=False)
            current = ["A@a"]
            async def selected(ctx): return current[0]
            module.get_unity_instance_from_context = selected
            module.resolve_project_id_for_unity_instance = lambda value: value.rsplit("@", 1)[1]
            execute = importlib.import_module("services.tools.execute_custom_tool")
            execute.get_unity_instance_from_context = selected
            execute.resolve_project_id_for_unity_instance = module.resolve_project_id_for_unity_instance
            sent = []
            async def send(fn, instance, command, params, **kwargs):
                sent.append((instance, command, params))
                return {"success": True, "data": {"instance": instance, "params": params}}
            module.send_with_unity_instance = send
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.http_app()), base_url="http://owned") as registrations:
                async def register(project, tools, replace=True):
                    response = await registrations.post("/register-tools", json={"project_id": project, "tools": tools, "replace": replace})
                    assert response.status_code == 200, response.text
                await register("a", [{"name": "shared_probe", "parameters": [{"name": "value", "type": "integer"}]}])
                await register("b", [{"name": "shared_probe", "parameters": [{"name": "value", "type": "string"}]}])
                async with Client(app, mode=MODE) as client:
                    assert next(t for t in await client.list_tools() if t.name == "shared_probe").input_schema["properties"]["value"]["type"] == "integer"
                    current[0] = "B@b"
                    denied = json.loads((await client.call_tool("shared_probe", {"value": 7})).content[0].text)
                    assert denied["success"] is False and (denied.get("data") or {}).get("reason") == "custom_tool_schema_changed", denied
                    assert sent == []
                    allowed = await client.call_tool("execute_custom_tool", {"tool_name": "shared_probe", "parameters": {"value": "b"}})
                    assert json.loads(allowed.content[0].text)["success"] is True
                    assert sent[-1] == ("B@b", "shared_probe", {"value": "b"})
                    await register("a", [])
                    assert next(t for t in await client.list_tools() if t.name == "shared_probe").input_schema["properties"]["value"]["type"] == "string"
                    await client.call_tool("shared_probe", {"value": "new-owner"})
                    assert sent[-1] == ("B@b", "shared_probe", {"value": "new-owner"})
                    current[0] = "A@a"
                    missing = json.loads((await client.call_tool("execute_custom_tool", {"tool_name": "shared_probe", "parameters": {"value": "no"}})).content[0].text)
                    assert missing["success"] is False and len(sent) == 2
                    await register("a", [{"name": "shared_probe", "parameters": [{"name": "value", "type": "integer"}]}])
                    assert service._sources[("manual", None, "a")].ordinal > service._sources[("manual", None, "b")].ordinal
                    assert "string" in str(next(tool.input_schema for tool in await client.list_tools() if tool.name == "shared_probe"))
        asyncio.run(main())
        """,
        tmp_path,
        mode,
    )
