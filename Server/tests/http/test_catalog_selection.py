"""Availability is a final cap, never a change to a client's selection."""

import asyncio
import socket
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from models.models import ToolDefinitionModel, UnityInstanceInfo
from services.custom_tool_service import CustomToolService
from services.tools import sync_tool_visibility_from_unity
from services.tools.manage_tools import manage_tools, search_unity_tools
from tests.timing import await_signal, watchdog_timeout
import transport.legacy.unity_connection as connections
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def catalog_server(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    metadata = [
        {"name": "scene_probe", "group": "core", "unity_target": "scene_probe"},
        {"name": "effect_probe", "group": "vfx", "unity_target": "effect_probe"},
        {"name": "texture_probe", "group": "vfx", "unity_target": "texture_probe"},
        {"name": "manage_tools", "group": None, "unity_target": None},
    ]
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", metadata)
    server = FastMCP("independent-selection")
    for item in metadata[:-1]:
        server.tool(name=item["name"], tags={f"group:{item['group']}"})(lambda: "ok")
    server.tool(name="manage_tools")(manage_tools)
    server.disable(tags={"group:vfx"}, components={"tool"})
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_mcp", server)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    middleware = UnityInstanceMiddleware()
    selected = {"project": "First@first"}

    async def inject(context, **_kwargs):
        await context.fastmcp_context.set_state("unity_instance", selected["project"])

    # The connection boundary is controlled; registry, publication, SDK transforms,
    # final middleware and actual client requests remain real.
    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)
    server.add_middleware(middleware)
    return server, registry, service, selected


async def publish(registry, session_id, names):
    return await registry.register_tools_for_session(
        session_id,
        [ToolDefinitionModel(name=name) for name in names],
        _publish=lambda session, tools: PluginHub._publish_session_catalog(
            session, tools, registry._sessions, generation=session_id
        ),
    )


async def names(client):
    return {tool.name for tool in await client.list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog,first_request",
    [
        ("disabled", "list"),
        ("unavailable", "list"),
        ("offline", "list"),
        ("other_project", "list"),
        ("legacy", "list"),
        ("enabled", "call"),
        ("disabled", "call"),
        ("unavailable", "call"),
        ("enabled", "list_groups"),
        ("disabled", "inspect_inventory"),
        ("enabled", "skipped_startup"),
    ],
)
async def test_first_request_after_empty_stdio_startup(
    catalog_server, monkeypatch, catalog, first_request
):
    import main

    server, _, service, selected = catalog_server
    instance = selected["project"]
    pool = connections.UnityConnectionPool()
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setenv(
        "UNITY_MCP_SKIP_STARTUP_CONNECT", "1" if first_request == "skipped_startup" else "0"
    )
    monkeypatch.setattr(main, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(main, "_unity_connection_pool", None)
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    monkeypatch.setattr(pool, "discover_all_instances", lambda: [])
    monkeypatch.setattr(
        connections, "get_authenticated_stdio_generation", AsyncMock(return_value="socket-one")
    )
    monkeypatch.setattr(
        connections, "get_stdio_catalog_generation", AsyncMock(return_value="socket-one")
    )
    monkeypatch.setattr(
        pool, "catalog_session_for", lambda _: SimpleNamespace(session_generation="socket-one")
    )
    reads = []

    async def read_states(_send, target, command, _params, **kwargs):
        reads.append((target, command))
        if catalog == "unavailable":
            return {"error": "Editor catalog temporarily unavailable"}
        assert catalog in {"disabled", "enabled"}
        kwargs["_catalog_capture"].append((SimpleNamespace(instance_id=target), "socket-one"))
        return {
            "data": {
                "tools": [
                    {"name": "scene_probe", "is_built_in": True, "enabled": catalog == "enabled"}
                ]
            }
        }

    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", read_states)
    monkeypatch.setattr(connections, "_commit_stdio_catalog", lambda _conn, _gen, commit: commit())
    async with main.server_lifespan(server):
        if catalog != "offline":
            observed = "Other@other" if catalog == "other_project" else instance
            pool._known_instances[observed] = SimpleNamespace(
                id=observed,
                hash=observed.rpartition("@")[2],
                status="running",
                listener_generation=None if catalog == "legacy" else "a" * 32,
            )
        async with Client(server, mode="legacy") as client:
            if first_request == "call":
                if catalog == "enabled":
                    assert not (await client.call_tool("scene_probe", {})).is_error
                else:
                    with pytest.raises(MCPError, match="disabled or unavailable"):
                        await client.call_tool("scene_probe", {}, raise_on_error=False)
            if first_request in {"list_groups", "inspect_inventory"}:
                arguments = {"action": first_request}
                if first_request == "inspect_inventory":
                    arguments.update(include_schema=True, tool="scene_probe")
                response = await client.call_tool("manage_tools", arguments)
                assert not response.is_error and reads == [(instance, "get_tool_states")]
            first = await client.list_tools_mcp()
            assert first.next_cursor is None
            expected = (
                {"manage_tools", "scene_probe"}
                if catalog in {"enabled", "offline", "other_project", "legacy"}
                else {"manage_tools"}
            )
            assert {tool.name for tool in first.tools} == expected
            assert await names(client) == expected
    assert reads == (
        [(instance, "get_tool_states")] if catalog in {"enabled", "disabled", "unavailable"} else []
    )
    if catalog == "disabled":
        assert service.get_stdio_availability("first", "socket-one") == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement", [{"port": 101}, {"path": "/neighbor"}, {"listener_generation": "b" * 32}]
)
async def test_previous_open_socket_cannot_supply_replacement_catalog(monkeypatch, replacement):
    target = UnityInstanceInfo(
        id="First@deadbeef",
        name="First",
        hash="deadbeef",
        path="/first",
        port=100,
        status="running",
        listener_generation="a" * 32,
    )
    pool = connections.UnityConnectionPool()
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    pool._known_instances[target.id] = target
    left, right = socket.socketpair()
    try:
        connection = connections.UnityConnection(
            port=target.port, instance_id=target.id, selected_target=target
        )
        connection.sock = left
        connection.session_generation = "retained-socket"
        connection.authenticated_session = SimpleNamespace(
            session_generation="retained-socket", server_generation=target.listener_generation
        )
        pool._connections[target.id] = connection
        assert await connections.get_stdio_catalog_generation(target.id) == "retained-socket"
        pool._known_instances[target.id] = target.model_copy(update=replacement)
        assert await connections.get_authenticated_stdio_generation(target.id) == "retained-socket"
        assert await connections.get_stdio_catalog_generation(target.id) is None
        attempts = []

        def handshake(**_kwargs):
            attempts.append(connection.selected_target)
            assert connection.sock is None and connection.authenticated_session is None
            return False

        replaced = pool._known_instances[target.id]
        monkeypatch.setattr(pool, "resolve_instance", lambda _: replaced)
        monkeypatch.setattr(connection, "connect", handshake)
        with pytest.raises(ConnectionError, match="Failed to connect"):
            await asyncio.to_thread(pool.get_connection, target.id)
        assert attempts == [replaced]
        assert await connections.get_stdio_catalog_generation(target.id) is None
    finally:
        left.close()
        right.close()


@pytest.mark.asyncio
async def test_same_launch_joins_read_while_other_editors_recover_independently(
    catalog_server, monkeypatch
):
    server, _, _, _ = catalog_server
    middleware = next(
        item for item in server.middleware if isinstance(item, UnityInstanceMiddleware)
    )
    pool = connections.UnityConnectionPool()
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    monkeypatch.setattr(connections, "get_stdio_catalog_generation", AsyncMock(return_value=None))
    reads = []
    first_entered, release = asyncio.Event(), asyncio.Event()

    async def read(target, **_kwargs):
        reads.append(target)
        if target == "First@first":
            first_entered.set()
            await release.wait()

    monkeypatch.setattr("services.tools.sync_tool_visibility_from_unity", read)

    def context(name):
        instance = name + "@" + name.lower()
        pool._known_instances[instance] = SimpleNamespace(
            hash=name.lower(), status="running", listener_generation="a" * 32
        )
        return SimpleNamespace(fastmcp=server, get_state=AsyncMock(return_value=instance))

    first, second = context("First"), context("Second")
    async with asyncio.TaskGroup() as workers:
        workers.create_task(middleware._prepare_stdio_catalog(first))
        await await_signal(first_entered, "first metadata read")
        workers.create_task(middleware._prepare_stdio_catalog(first))
        try:
            await middleware._prepare_stdio_catalog(second)
            assert reads == ["First@first", "Second@second"]
        finally:
            release.set()
    assert reads == ["First@first", "Second@second"]


@pytest.mark.asyncio
async def test_catalog_sync_preserves_default_and_individual_client_selection(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
        assert await names(first) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe"])
        assert await names(first) == {"scene_probe", "manage_tools"}
        await first.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        assert await names(first) == {"scene_probe", "effect_probe", "manage_tools"}
        assert await names(second) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe", "texture_probe"])
        assert await names(first) == {"scene_probe", "effect_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_reset_reports_actual_default_visibility_after_catalog_changes(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    await publish(registry, "one", ["effect_probe"])
    async with Client(server, mode="legacy") as client:
        group = (
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        ).structured_content
        assert group["server_advertised_tools"] == ["effect_probe"]
        unavailable = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "texture_probe"})
        ).structured_content
        assert unavailable["server_advertised"] is False
        assert unavailable["next_action"] == {
            "resource": "mcpforunity://tool-diagnostics/texture_probe"
        }
        assert await names(client) == {"effect_probe", "manage_tools"}
        reset = (await client.call_tool("manage_tools", {"action": "reset"})).structured_content
        assert reset["visible_tools"] == ["manage_tools"]
        assert await names(client) == {"manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe"])
        assert await names(client) == {"scene_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_known_empty_catalog_hides_native_tools_but_unknown_preserves_discovery(
    catalog_server,
):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    async with Client(server, mode="legacy") as client:
        assert await names(client) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", [])
        assert await names(client) == {"manage_tools"}
        await registry.unregister("one")
        assert await names(client) == {"scene_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_project_switch_and_reconnect_keep_selection_with_current_cap(catalog_server):
    server, registry, _, selected = catalog_server
    await registry.register("one", "First", "first", "6000")
    await registry.register("two", "Second", "second", "6000")
    await publish(registry, "one", ["effect_probe"])
    await publish(registry, "two", ["texture_probe"])
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}
        selected["project"] = "Second@second"
        assert await names(client) == {"texture_probe", "manage_tools"}
        await registry.register("replacement", "Second", "second", "6000")
        await publish(registry, "replacement", ["effect_probe"])
        assert await names(client) == {"effect_probe", "manage_tools"}
        await client.call_tool("manage_tools", {"action": "reset"})
        assert await names(client) == {"manage_tools"}


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("kind", ["family", "evidence"])
async def test_search_retries_preserve_only_an_explicit_project_binding(
    catalog_server, monkeypatch, protocol, kind
):
    server, registry, _, _ = catalog_server
    server.tool(name="search_unity_tools")(search_unity_tools)
    middleware = next(
        item for item in server.middleware if isinstance(item, UnityInstanceMiddleware)
    )
    monkeypatch.setattr(
        middleware,
        "_inject_unity_instance",
        UnityInstanceMiddleware._inject_unity_instance.__get__(middleware),
    )
    monkeypatch.setattr(
        middleware, "_maybe_autoselect_instance", AsyncMock(return_value="First@first")
    )
    await registry.register("one", "First", "first", "6000")
    await registry.register("two", "Second", "second", "6001")
    await publish(registry, "one", [])
    await publish(registry, "two", ["scene_probe"])
    form = {"query": "scene_probe", "route_surfaces": "tools", "host_tool_names": ["scene_probe"]}
    if kind == "family":
        tool_name = "manage_tools"
        form.update(action="search", selection="agent", family="scene_probe", intent="observe")
    else:
        tool_name = "search_unity_tools"
        form.update(
            intent="edit", observation_source="editor_live", required_values=["render_size.width"]
        )

    async with Client(server, mode=protocol, cache=False) as client:
        explicit = (
            await client.call_tool(tool_name, {**form, "unity_instance": "seco"})
        ).structured_content
        implicit = (await client.call_tool(tool_name, form)).structured_content
        if kind == "family":
            retry = next(
                option["arguments"]
                for option in explicit["search_retries"]
                if option["arguments"]["selection"] == "agent"
            )
            assert all(
                "unity_instance" not in option["arguments"] for option in implicit["search_retries"]
            )
        else:
            retry = explicit["retry_search"]["arguments"]
            assert "unity_instance" not in implicit["retry_search"]["arguments"]
        assert retry["unity_instance"] == "Second@second"
        accepted = (await client.call_tool(tool_name, retry)).structured_content

    if kind == "family":
        assert [row["tool"] for row in accepted["operations"]] == ["scene_probe"]
    else:
        assert [row["name"] for row in accepted["results"]] == ["scene_probe"]


@pytest.mark.asyncio
async def test_stdio_filters_after_session_transforms_and_discards_stale_catalog(
    catalog_server, monkeypatch
):
    server, _, service, selected = catalog_server
    monkeypatch.setattr(config, "transport_mode", "stdio")
    current = {"generation": "socket-one"}

    async def generation(instance):
        assert instance == selected["project"]
        return current["generation"]

    monkeypatch.setattr(
        "transport.legacy.unity_connection.get_authenticated_stdio_generation", generation
    )
    service.replace_project_catalog(
        "first",
        [ToolDefinitionModel(name="effect_probe")],
        kind="stdio",
        generation="socket-one",
        publish_global=False,
    )
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}
        service.replace_project_catalog(
            "first",
            [],
            kind="stdio",
            generation="socket-one",
            publish_global=False,
        )
        assert await names(client) == {"manage_tools"}
        current["generation"] = "socket-two"
        # Unknown current generation falls back to the client's discovery profile.
        assert await names(client) == {
            "scene_probe",
            "effect_probe",
            "texture_probe",
            "manage_tools",
        }


@pytest.mark.asyncio
async def test_stdio_sync_retains_native_availability_and_never_promotes_groups(
    catalog_server, monkeypatch
):
    server, _, service, _ = catalog_server
    monkeypatch.setattr(config, "transport_mode", "stdio")
    connection = SimpleNamespace(instance_id="First@first")

    async def send(_command, instance, command, _params, **kwargs):
        assert (instance, command) == ("First@first", "get_tool_states")
        kwargs["_catalog_capture"].append((connection, "socket-one"))
        return {
            "data": {
                "tools": [
                    {"name": "effect_probe", "enabled": True, "is_built_in": True},
                    {"name": "texture_probe", "enabled": False, "is_built_in": True},
                ]
            }
        }

    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    monkeypatch.setattr(
        "transport.legacy.unity_connection.get_authenticated_stdio_generation",
        AsyncMock(return_value="socket-one"),
    )
    monkeypatch.setattr(
        "transport.legacy.unity_connection._commit_stdio_catalog",
        lambda _connection, _generation, publish: publish(),
    )
    assert (await sync_tool_visibility_from_unity("First@first", notify=False))["synced"]
    assert set(service._sources[("stdio", None, "first")].tools) == {"effect_probe"}
    async with Client(server, mode="legacy") as client:
        assert await names(client) == {"manage_tools"}
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_local_call_admission_checks_availability_after_client_selection(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    await publish(registry, "one", ["effect_probe"])
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        # A stale client list must not dispatch a now-disabled native operation.
        assert not (await client.call_tool("effect_probe", {})).is_error
        await publish(registry, "one", ["texture_probe"])
        with pytest.raises(MCPError, match="disabled or unavailable"):
            await client.call_tool("effect_probe", {}, raise_on_error=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [["effect_probe"], []])
async def test_busy_stdio_keeps_catalog_cap_without_reusing_cached_reads(
    catalog_server, monkeypatch, available
):
    server, _, service, selected = catalog_server
    monkeypatch.setattr(config, "transport_mode", "stdio")
    left, right = socket.socketpair()
    connection = connections.UnityConnection(port=1111, auth_token_provider=lambda _: "x" * 64)
    connection.sock = left
    connection.session_generation = "socket-one"
    connection.authenticated_session = SimpleNamespace(session_generation="socket-one")
    pool = connections.UnityConnectionPool()
    instance = selected["project"]
    pool._known_instances = {instance: SimpleNamespace(id=instance)}
    pool._connections = {instance: connection}
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    service.replace_project_catalog(
        "first",
        [ToolDefinitionModel(name=name) for name in available],
        kind="stdio",
        generation="socket-one",
        publish_global=False,
    )
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def command_owns_socket():
        with connection._io_lock:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(watchdog_timeout()), "test did not release the owned socket"

    worker = threading.Thread(target=command_owns_socket)
    try:
        async with Client(server, mode="legacy") as client:
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
            worker.start()
            await await_signal(entered, "command acquiring the socket")
            assert await connections.get_authenticated_stdio_generation(instance) is None
            assert await names(client) == {*available, "manage_tools"}
            with pytest.raises(MCPError, match="disabled or unavailable"):
                await client.call_tool("texture_probe", {}, raise_on_error=False)
            if available:
                assert not (await client.call_tool("effect_probe", {})).is_error
            assert worker.is_alive(), "discovery must not interrupt the running command"
    finally:
        release.set()
        if worker.ident is not None:
            await asyncio.to_thread(worker.join, watchdog_timeout())
        left.close()
        right.close()
    assert not worker.is_alive()
