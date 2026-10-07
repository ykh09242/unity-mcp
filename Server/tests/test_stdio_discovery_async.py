"""Local discovery must not stall unrelated MCP requests or persist cancelled choices."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from core.config import config
from transport.legacy.port_discovery import PortDiscovery
import transport.legacy.unity_connection as uc
from transport.unity_instance_middleware import PluginHub, UnityInstanceMiddleware


class SessionContext:
    def __init__(self):
        self.state = {}
        self.thread_ids = []

    async def set_state(self, key, value):
        self.thread_ids.append(threading.get_ident())
        self.state[key] = value

    async def get_state(self, key):
        return self.state.get(key)


@pytest.fixture
def local_discovery(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    monkeypatch.delenv("UNITY_MCP_DEFAULT_INSTANCE", raising=False)
    pool = uc.UnityConnectionPool()
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    target = SimpleNamespace(
        id="Main@deadbeef", hash="deadbeef", name="Main", port=6400, path="/Owned/Main/Assets"
    )
    return pool, target, UnityInstanceMiddleware(), SessionContext()


def gated_discovery(monkeypatch, target):
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    started = asyncio.Event()
    completed = asyncio.Event()
    release = threading.Event()
    threads = []

    def scan(*_args):
        thread = threading.get_ident()
        threads.append(thread)
        loop.call_soon_threadsafe(started.set)
        try:
            # A baseline main-thread call returns immediately, so a failing test
            # never deadlocks its own event loop waiting for the release task.
            if thread != loop_thread:
                assert release.wait(2), "Discovery worker was not released"
            return target
        finally:
            loop.call_soon_threadsafe(completed.set)

    monkeypatch.setattr(PortDiscovery, "discover_unity_instance", scan)
    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", lambda: [scan()])
    return started, completed, release, threads


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["Main@deadbeef", "dead", "6400", None])
async def test_local_selection_allows_other_requests_while_discovery_waits(
    local_discovery,
    monkeypatch,
    selector,
):
    pool, target, middleware, ctx = local_discovery
    started, _, release, threads = gated_discovery(monkeypatch, target)
    operation = (
        middleware._maybe_autoselect_instance(ctx)
        if selector is None
        else middleware._resolve_instance_value(selector, ctx)
    )
    request = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(threads) == 1 and threads[0] != threading.get_ident(), (
            "Local discovery ran on the MCP event loop"
        )
        assert not request.done(), "Other requests cannot run until discovery completes"
        assert not ctx.state, "Selection was persisted before discovery completed"
    finally:
        release.set()
        result = await request
    assert result == target.id
    assert pool._connections == {}, "Metadata lookup opened a command connection"
    if selector is None:
        assert ctx.state[middleware._ACTIVE_INSTANCE_STATE_KEY] == target.id
        assert ctx.thread_ids == [threading.get_ident()]
    else:
        assert not ctx.state, "Per-call selection changed session state"


@pytest.mark.asyncio
async def test_cancelled_autoselect_does_not_persist_late_discovery(
    local_discovery,
    monkeypatch,
):
    _, target, middleware, ctx = local_discovery
    started, completed, release, _ = gated_discovery(monkeypatch, target)
    request = asyncio.create_task(middleware._maybe_autoselect_instance(ctx))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not request.done(), "Discovery blocked the caller until after selection"
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        if not request.done():
            await request
    assert ctx.state == {}, "Cancelled discovery persisted a session selection"


@pytest.mark.asyncio
async def test_missing_exact_selection_fallback_stays_off_event_loop(
    local_discovery,
    monkeypatch,
):
    _, target, middleware, ctx = local_discovery
    threads = []

    def absent(_identifier):
        threads.append(threading.get_ident())
        return None

    def full_scan():
        threads.append(threading.get_ident())
        return [target]

    monkeypatch.setattr(PortDiscovery, "discover_unity_instance", absent)
    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", full_scan)
    with pytest.raises(ValueError, match="Instance 'Missing@cafebabe' not found"):
        await middleware._resolve_instance_value("Missing@cafebabe", ctx)
    assert len(threads) == 2
    assert all(thread != threading.get_ident() for thread in threads)
    assert ctx.state == {}


@pytest.mark.asyncio
async def test_late_autoselect_preserves_explicit_public_tool_selection(
    local_discovery, monkeypatch
):
    import services.tools.set_active_instance as selection_tool

    pool, target, middleware, ctx = local_discovery
    other = SimpleNamespace(id="Other@cafebabe", hash="cafebabe", name="Other", port=6401)
    started, _, release, _ = gated_discovery(monkeypatch, target)
    original_discovery = PortDiscovery.discover_all_unity_instances
    scans = 0

    def changing_inventory():
        nonlocal scans
        scans += 1
        return original_discovery() if scans == 1 else [target, other]

    def explicit_discovery(force_refresh=False):
        # Finish the already-running scan while the public selection tool waits
        # on the real pool lock. Its forced scan observes the second Editor.
        release.set()
        return pool.discover_all_instances(force_refresh=force_refresh)

    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", changing_inventory)
    monkeypatch.setattr(
        selection_tool,
        "get_unity_connection_pool",
        lambda: SimpleNamespace(discover_all_instances=explicit_discovery),
    )
    monkeypatch.setattr(selection_tool, "get_unity_instance_middleware", lambda: middleware)
    monkeypatch.setattr(selection_tool, "is_sessionless", lambda _: False)
    automatic = asyncio.create_task(middleware._maybe_autoselect_instance(ctx))
    try:
        await asyncio.wait_for(started.wait(), 2)
        result = await selection_tool.set_active_instance(ctx, other.id)
        assert result["success"] and result["data"]["instance"] == other.id
        assert await middleware.get_active_instance(ctx) == other.id
    finally:
        release.set()
        automatic_result = await automatic
    assert automatic_result == other.id
    assert await middleware.get_active_instance(ctx) == other.id


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_action", ["set", "clear"])
async def test_auto_selection_state_read_and_write_cannot_overwrite_explicit_writer(
    local_discovery,
    monkeypatch,
    explicit_action,
):
    _, target, middleware, _ = local_discovery
    reading = asyncio.Event()
    release_read = asyncio.Event()

    class DelayedStateContext(SessionContext):
        async def get_state(self, key):
            value = await super().get_state(key)
            reading.set()
            await release_read.wait()
            return value

    ctx = DelayedStateContext()
    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", lambda: [target])
    automatic = asyncio.create_task(middleware._maybe_autoselect_instance(ctx))
    explicit = None
    try:
        await asyncio.wait_for(reading.wait(), 2)
        operation = (
            middleware.set_active_instance(ctx, "Other@cafebabe")
            if explicit_action == "set"
            else middleware.clear_active_instance(ctx)
        )
        explicit = asyncio.create_task(operation)
        await asyncio.sleep(0)
        assert not explicit.done(), "An explicit writer interleaved automatic state read/write"
    finally:
        release_read.set()
        await automatic
        if explicit is not None:
            await explicit
    expected = "Other@cafebabe" if explicit_action == "set" else None
    assert ctx.state[middleware._ACTIVE_INSTANCE_STATE_KEY] == expected


@pytest.mark.asyncio
async def test_cancelled_state_read_releases_selection_lock(local_discovery, monkeypatch):
    _, target, middleware, _ = local_discovery
    reading = asyncio.Event()

    class CancelledReadContext(SessionContext):
        async def get_state(self, key):
            reading.set()
            await asyncio.Event().wait()

    ctx = CancelledReadContext()
    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", lambda: [target])
    automatic = asyncio.create_task(middleware._maybe_autoselect_instance(ctx))
    try:
        await asyncio.wait_for(reading.wait(), 2)
    finally:
        automatic.cancel()
        with pytest.raises(asyncio.CancelledError):
            await automatic
    await asyncio.wait_for(middleware.set_active_instance(ctx, "Other@cafebabe"), 2)
    assert ctx.state[middleware._ACTIVE_INSTANCE_STATE_KEY] == "Other@cafebabe"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["instances", "editor_state", "custom_tools"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_resource_reads_allow_other_requests_while_discovery_waits(
    local_discovery, monkeypatch, resource, cancelled
):
    from services.resources import custom_tools, editor_state, unity_instances
    import services.custom_tool_service as custom_tool_service

    pool, target, _, ctx = local_discovery
    target.to_dict = lambda: {"id": target.id}
    monkeypatch.setattr(unity_instances, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(custom_tool_service, "get_unity_connection_pool", lambda: pool)
    started, completed, release, threads = gated_discovery(monkeypatch, target)
    listed = []

    async def selected(_ctx):
        return target.id

    async def principal(_ctx):
        return "owned-user"

    async def list_tools(project_id, *, user_id):
        listed.append((project_id, user_id, threading.get_ident()))
        return []

    monkeypatch.setattr(custom_tools, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(custom_tools, "get_user_id_from_context", principal)
    monkeypatch.setattr(
        custom_tools.CustomToolService,
        "get_instance",
        lambda: SimpleNamespace(list_registered_tools=list_tools),
    )
    operations = {
        "instances": unity_instances.unity_instances,
        "editor_state": editor_state.infer_single_instance_id,
        "custom_tools": custom_tools.get_custom_tools,
    }
    request = asyncio.create_task(operations[resource](ctx))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(threads) == 1 and threads[0] != threading.get_ident(), (
            "Resource discovery ran on the MCP event loop"
        )
        assert not request.done(), "Other requests cannot run until resource discovery completes"
        assert listed == [], "Custom tool listing ran before project resolution completed"
        if cancelled:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    finally:
        release.set()
        result = (await asyncio.gather(request, return_exceptions=True))[0]
        await asyncio.wait_for(completed.wait(), 2)
    if cancelled:
        assert isinstance(result, asyncio.CancelledError)
        assert listed == [], "Cancelled resource read listed tools after project resolution"
    elif resource == "instances":
        assert result["success"] and result["instances"] == [{"id": target.id}]
    elif resource == "editor_state":
        assert result == target.id
    else:
        assert result.success and result.data.project_id == target.hash
        assert result.data.tool_count == 0
        assert listed == [(target.hash, "owned-user", threading.get_ident())]
    assert ctx.state == {}, "Resource metadata lookup changed session selection"
    assert pool._connections == {}, "Resource metadata lookup opened a command connection"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["instances", "editor_state"])
async def test_resource_discovery_errors_keep_existing_response_contract(
    local_discovery, monkeypatch, resource
):
    from services.resources import editor_state, unity_instances

    pool, _, _, ctx = local_discovery
    threads = []

    def unavailable():
        threads.append(threading.get_ident())
        raise OSError("fixture discovery unavailable")

    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", unavailable)
    monkeypatch.setattr(unity_instances, "get_unity_connection_pool", lambda: pool)
    if resource == "instances":
        result = await unity_instances.unity_instances(ctx)
        assert result == {
            "success": False,
            "error": "Failed to list Unity instances: fixture discovery unavailable",
            "instance_count": 0,
            "instances": [],
        }
    else:
        assert await editor_state.infer_single_instance_id(ctx) is None
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    assert ctx.state == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["generic", "global", "rest_execute", "rest_list"])
async def test_custom_tool_entrypoints_allow_other_requests_while_discovery_waits(
    local_discovery, monkeypatch, entrypoint
):
    import httpx
    from fastmcp import FastMCP
    import main
    from models.models import MCPResponse, ToolDefinitionModel
    import services.custom_tool_service as custom_tool_service
    import services.tools.execute_custom_tool as execution_tool
    from transport.models import SessionDetails, SessionList

    pool, target, _, ctx = local_discovery
    monkeypatch.setattr(custom_tool_service, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(custom_tool_service.CustomToolService, "_instance", None)
    monkeypatch.setattr(main, "custom_tool_service", None)
    monkeypatch.setattr(config, "local_auth_token", "discovery-fixture-token")
    started, completed, release, threads = gated_discovery(monkeypatch, target)
    dispatched = []

    async def selected(_ctx):
        return target.id

    async def principal(_ctx):
        return "owned-user"

    async def execute(project_id, tool_name, instance, params, **options):
        dispatched.append((project_id, tool_name, instance, params, options, threading.get_ident()))
        return MCPResponse(success=True, data={"resolved": project_id})

    async def listing(project_id):
        dispatched.append((project_id, threading.get_ident()))
        return []

    async def sessions():
        return SessionList(
            sessions={
                "fixture-session": SessionDetails(
                    project=target.name, hash=target.hash, unity_version="6000", connected_at="now"
                )
            }
        )

    for module in (custom_tool_service, execution_tool):
        monkeypatch.setattr(module, "get_unity_instance_from_context", selected)
        monkeypatch.setattr(module, "get_user_id_from_context", principal)
    monkeypatch.setattr(PluginHub, "get_sessions", sessions)
    server = main.create_mcp_server(False) if entrypoint.startswith("rest_") else FastMCP("fixture")
    service = (
        custom_tool_service.CustomToolService.get_instance()
        if entrypoint.startswith("rest_")
        else custom_tool_service.CustomToolService(server)
    )
    monkeypatch.setattr(service, "execute_tool", execute)
    monkeypatch.setattr(service, "list_registered_tools", listing)

    async def invoke():
        if entrypoint == "generic":
            return await execution_tool.execute_custom_tool(ctx, "fixture_tool", {"value": 3})
        if entrypoint == "global":
            handler = service._build_global_tool_handler(ToolDefinitionModel(name="fixture_tool"))
            return await handler(ctx, value=3)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.http_app()),
            base_url="http://fixture",
            headers={"X-Unity-MCP-Token": "discovery-fixture-token"},
        ) as client:
            if entrypoint == "rest_execute":
                response = await client.post(
                    "/api/command",
                    json={
                        "type": "execute_custom_tool",
                        "unity_instance": target.id,
                        "params": {"tool_name": "fixture_tool", "parameters": {"value": 3}},
                    },
                )
            else:
                response = await client.get("/api/custom-tools", params={"instance": target.id})
            assert response.status_code == 200, response.text
            return MCPResponse(**response.json())

    request = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(threads) == 1 and threads[0] != threading.get_ident(), (
            "Custom tool entrypoint discovery ran on the MCP event loop"
        )
        assert not request.done(), "Other requests cannot run until project discovery completes"
        assert dispatched == [], "Custom tool was dispatched before project discovery completed"
    finally:
        release.set()
        result = await request
        await asyncio.wait_for(completed.wait(), 2)
    assert result.success
    if entrypoint == "rest_list":
        assert dispatched == [(target.hash, threading.get_ident())]
    else:
        options = {"user_id": "owned-user"} if entrypoint in ("generic", "global") else {}
        if entrypoint == "global":
            options["omit_nulls"] = True
        instance = target.hash if entrypoint == "rest_execute" else target.id
        assert dispatched == [
            (target.hash, "fixture_tool", instance, {"value": 3}, options, threading.get_ident())
        ]
    assert ctx.state == {}, "Custom tool project resolution changed session selection"
    assert pool._connections == {}, "Project resolution opened a legacy command connection"
