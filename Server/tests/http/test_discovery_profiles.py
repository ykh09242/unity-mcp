"""Profiles and trusted selection scopes preserve complete tool contracts."""

import asyncio
from contextlib import asynccontextmanager

from fastmcp import Client, Context, FastMCP
from fastmcp.server.middleware import Middleware
from mcp.types import ToolListChangedNotification
import pytest

from core.config import config
from services.tools import register_all_tools
from services.tools.manage_tools import manage_tools
from transport.session_state_store import SessionStateStore
from tests.timing import await_signal


@pytest.fixture
def discovery_server(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr("services.tools.discover_modules", lambda *_: [])
    monkeypatch.delenv("UNITY_MCP_TOOL_PROFILE", raising=False)
    state = {"project": "First@first", "owner": None}

    async def inspect_probe(count: int = 1):
        """Return the requested number of inspected objects without changing them."""
        return {"count": count}

    async def effect_probe(strength: float, enabled: bool = True):
        """Apply the supplied strength and explicit activation flag."""
        return {"strength": strength, "enabled": enabled}

    entries = []
    for func, group in [(inspect_probe, "core"), (effect_probe, "vfx"), (manage_tools, None)]:
        entries.append(
            {
                "name": func.__name__,
                "func": func,
                "description": func.__doc__,
                "group": group,
                "unity_target": None,
                "kwargs": {"tags": {f"group:{group}"} if group else set()},
            }
        )
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", entries)

    class Routing(Middleware):
        async def on_request(self, context, call_next):
            await context.fastmcp_context.set_state(
                "unity_instance", state["project"], serializable=False
            )
            await context.fastmcp_context.set_state("user_id", state["owner"], serializable=False)
            return await call_next(context)

    def build(profile=None, *, auth=None):
        if profile is not None:
            monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
        store = SessionStateStore()

        @asynccontextmanager
        async def lifespan(_server):
            async with store.lifespan():
                yield

        server = FastMCP("profiles", session_state_store=store, lifespan=lifespan, auth=auth)
        server._unity_session_state_store = store
        server.add_middleware(Routing())
        register_all_tools(server)
        return server, store

    return build, state


async def names(client):
    return {tool.name for tool in await client.list_tools()}


@pytest.mark.asyncio
async def test_minimal_describe_activate_reset_preserves_complete_schema_and_calls(
    discovery_server,
):
    build, _ = discovery_server
    full, _ = build("full")
    async with Client(full, mode="legacy") as client:
        expected = {tool.name: tool for tool in await client.list_tools()}
        result = await client.call_tool("effect_probe", {"strength": 2.5, "enabled": False})
        full_result = result.structured_content
    server, _ = build("minimal")
    async with Client(server, mode="legacy") as client:
        assert await names(client) == {"manage_tools"}
        description = (
            await client.call_tool("manage_tools", {"action": "describe", "tool": "effect_probe"})
        ).structured_content
        assert description["tool"] == expected["effect_probe"].model_dump(
            mode="json", exclude_none=True
        )
        assert await names(client) == {"manage_tools"}
        await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        selected = {tool.name: tool for tool in await client.list_tools()}
        assert selected["effect_probe"] == expected["effect_probe"]
        result = await client.call_tool("effect_probe", {"strength": 2.5, "enabled": False})
        assert result.structured_content == full_result
        bad = await client.call_tool("effect_probe", {"strength": []}, raise_on_error=False)
        assert bad.is_error
        reset = (await client.call_tool("manage_tools", {"action": "reset"})).structured_content
        assert reset["default_groups"] == []
        assert reset["visible_tools"] == ["manage_tools"]


@pytest.mark.asyncio
async def test_modern_selection_is_bound_to_owner_project_and_server(discovery_server):
    build, state = discovery_server
    server, store = build("core")
    async with Client(server) as client:
        first = (
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        ).structured_content
        assert first.get("activated") == "vfx", first
        assert await names(client) == {"manage_tools", "inspect_probe", "effect_probe"}
        state["project"] = "Second@second"
        assert "effect_probe" not in await names(client)
        state["project"] = "First@first"
        state["owner"] = "another-user"
        assert "effect_probe" not in await names(client)
        state["owner"] = None
        assert "effect_probe" in await names(client)
        other, _ = build("core")
        async with Client(other) as independent:
            assert "effect_probe" not in await names(independent)
        for _ in range(12):
            duplicate = (
                await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
            ).structured_content
            assert duplicate["changed"] is False
        assert store.retained_entries == 1
        reset = (await client.call_tool("manage_tools", {"action": "reset"})).structured_content
        assert reset["visible_tools"] == ["inspect_probe", "manage_tools"]
        assert store.retained_entries == 0
    assert store.retained_entries == 0


@pytest.mark.asyncio
async def test_modern_noop_writes_only_at_hourly_renewal_and_preserves_expiry(
    discovery_server, monkeypatch
):
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    build, _ = discovery_server
    server, store = build("core")
    origin = time.time()
    clock = [origin]
    monkeypatch.setattr("services.tool_discovery.time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(store, "_clock", lambda: clock[0])
    put = AsyncMock(wraps=store.put)
    monkeypatch.setattr(store, "put", put)
    async with Client(server, cache=False) as client:
        first = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        ).structured_content
        assert first["changed"] is True
        assert put.await_count == 1
        put.reset_mock()
        for elapsed in (0, 100, 3599, 3600):
            clock[0] = origin + elapsed
            duplicate = (
                await client.call_tool(
                    "manage_tools", {"action": "activate", "tool": "effect_probe"}
                )
            ).structured_content
            assert duplicate["changed"] is False
            if elapsed == 3600:
                assert put.await_count == 1
            else:
                assert put.await_count == 0
        put.reset_mock()
        assert "effect_probe" in await names(client)
        assert put.await_count == 0
        clock[0] += 86401
        assert "effect_probe" not in await names(client)
        assert store.retained_entries == 0
        default = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "inspect_probe"})
        ).structured_content
        assert default["changed"] is False
        assert put.await_count == 0


@pytest.mark.asyncio
async def test_unidentified_modern_scope_refuses_and_explains_static_fallback(
    discovery_server, monkeypatch
):
    build, _ = discovery_server
    server, store = build()
    monkeypatch.setattr(Context, "_session_scope_key", lambda _: None)
    async with Client(server) as client:
        result = (
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        ).structured_content
        assert "UNITY_MCP_TOOL_PROFILE=full" in result["error"]
        assert "effect_probe" not in await names(client)
        assert store.retained_entries == 0


@pytest.mark.asyncio
async def test_no_project_does_not_create_global_modern_selection(discovery_server):
    build, state = discovery_server
    server, store = build()
    state["project"] = None
    async with Client(server) as client:
        result = (
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        ).structured_content
        assert "project" in result["error"].lower()
        assert store.retained_entries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "legacy"])
async def test_refresh_notifications_are_idempotent_and_fixed_inventory_stays_explicit(
    discovery_server,
    mode,
):
    build, _ = discovery_server
    server, _ = build()
    changed = asyncio.Event()
    notifications = []

    async def handle(message):
        if isinstance(message, ToolListChangedNotification):
            notifications.append(message)
            changed.set()

    async with Client(server, message_handler=handle, **({"mode": mode} if mode else {})) as client:
        fixed_inventory = await names(client)
        result = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        ).structured_content
        assert "fixed" in result["message"].lower()
        assert result["client_inventory_status"] == "unconfirmed"
        await await_signal(changed, "selection notification")
        refreshed = await names(client)
        assert "effect_probe" in refreshed and "effect_probe" not in fixed_inventory
        count = len(notifications)
        await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        await client.list_tools()
        assert len(notifications) == count


@pytest.mark.asyncio
async def test_full_startup_serves_optional_schema_to_a_fixed_inventory_client(discovery_server):
    build, _ = discovery_server
    server, _ = build("full")
    async with Client(server, mode="legacy", cache=False) as client:
        initial_inventory = {tool.name: tool for tool in await client.list_tools()}
        assert "effect_probe" in initial_inventory
        assert initial_inventory["effect_probe"].input_schema["required"] == ["strength"]
        # No activation or second tools/list is needed by this connection.
        result = await client.call_tool("effect_probe", {"strength": 2.5, "enabled": False})
        assert result.structured_content == {"strength": 2.5, "enabled": False}
        capability = (
            await client.call_tool("manage_tools", {"action": "list_groups"})
        ).structured_content["discovery"]
        assert capability["client_inventory_status"] == "unconfirmed"


def test_unknown_profile_fails_at_registration(discovery_server):
    build, _ = discovery_server
    with pytest.raises(ValueError, match="UNITY_MCP_TOOL_PROFILE"):
        build("typo")


@pytest.mark.parametrize("profile", ["core", "minimal", "full"])
def test_real_http_without_sdk_identity_uses_static_profile(discovery_server, profile):
    from starlette.testclient import TestClient

    build, _ = discovery_server
    server, store = build(profile)
    with TestClient(server.http_app(json_response=True)) as client:
        from tests.http.test_mcp_protocol_wire import _body, _headers

        def request(method, params=None):
            params = params or {}
            headers = dict(_headers(method, name=params.get("name")))
            headers["accept"] = "application/json, text/event-stream"
            response = client.post("/mcp", headers=headers, json=_body(method, params))
            assert response.status_code == 200, response.text
            return response.json()["result"]

        before = request("tools/list")["tools"]
        groups = request(
            "tools/call", {"name": "manage_tools", "arguments": {"action": "list_groups"}}
        )["structuredContent"]
        capability = groups["discovery"]
        assert capability["profile"] == profile
        assert capability["mode"] == "static_profile"
        assert capability["selection_supported"] is False
        assert capability["reason"] == "verified_sdk_principal_required"
        assert "UNITY_MCP_TOOL_PROFILE=full" in capability["guidance"]
        refused = request(
            "tools/call",
            {"name": "manage_tools", "arguments": {"action": "activate", "group": "vfx"}},
        )
        assert "UNITY_MCP_TOOL_PROFILE=full" in refused["structuredContent"]["error"]
        assert request("tools/list")["tools"] == before
        assert ("effect_probe" in {tool["name"] for tool in before}) == (profile == "full")
        assert store.retained_entries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "legacy"])
async def test_connection_capability_reports_supported_selection(discovery_server, mode):
    build, _ = discovery_server
    server, _ = build()
    async with Client(server, **({"mode": mode} if mode else {})) as client:
        groups = (
            await client.call_tool("manage_tools", {"action": "list_groups"})
        ).structured_content
        capability = groups["discovery"]
        assert capability["selection_supported"] is True
        assert capability["mode"] == ("legacy_connection" if mode else "scoped_project")
        assert capability["reason"] == "supported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        ("project", "resolved_project_required"),
        ("sdk", "reviewed_sdk_required"),
        ("store", "selection_store_unavailable"),
    ],
)
async def test_connection_capability_explains_each_unavailable_scope(
    discovery_server, missing, reason
):
    build, state = discovery_server
    server, _ = build()
    if missing == "project":
        state["project"] = None
    elif missing == "sdk":
        server._tool_discovery._supported = False
    else:
        server._tool_discovery.store = None
    async with Client(server) as client:
        groups = (
            await client.call_tool("manage_tools", {"action": "list_groups"})
        ).structured_content
        assert groups["discovery"]["selection_supported"] is False
        assert groups["discovery"]["reason"] == reason


def test_verified_http_principal_selection_and_capability_are_isolated(discovery_server):
    from fastmcp.server.auth import StaticTokenVerifier
    from starlette.testclient import TestClient
    from tests.http.test_mcp_protocol_wire import _body, _headers

    build, _ = discovery_server
    auth = StaticTokenVerifier(
        tokens={
            "test-owner-one": {"client_id": "same-client", "sub": "owner-one"},
            "test-owner-two": {"client_id": "same-client", "sub": "owner-two"},
        }
    )
    server, store = build(auth=auth)
    with TestClient(server.http_app(json_response=True)) as client:

        def request(method, params=None, *, token=None):
            params = params or {}
            headers = dict(_headers(method, name=params.get("name")))
            headers["accept"] = "application/json, text/event-stream"
            headers["authorization"] = "Bearer " + (token or "test-owner-one")
            response = client.post("/mcp", headers=headers, json=_body(method, params))
            assert response.status_code == 200, response.text
            return response.json()["result"]

        capability = request(
            "tools/call", {"name": "manage_tools", "arguments": {"action": "list_groups"}}
        )["structuredContent"]["discovery"]
        assert capability["mode"] == "scoped_project"
        assert capability["selection_supported"] is True
        request(
            "tools/call",
            {"name": "manage_tools", "arguments": {"action": "activate", "group": "vfx"}},
        )
        assert "effect_probe" in {tool["name"] for tool in request("tools/list")["tools"]}
        assert "effect_probe" not in {
            tool["name"] for tool in request("tools/list", token="test-owner-two")["tools"]
        }
        assert store.retained_entries == 1


@pytest.mark.asyncio
async def test_idle_expiration_and_store_capacity_preserve_other_selections(
    discovery_server, monkeypatch
):
    build, state = discovery_server
    server, store = build()
    async with Client(server) as client:
        await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        state["project"] = "Second@second"
        store._max_entries = 1
        refused = await client.call_tool(
            "manage_tools", {"action": "activate", "tool": "effect_probe"}, raise_on_error=False
        )
        assert refused.is_error
        state["project"] = "First@first"
        assert "effect_probe" in await names(client)
        import time

        monkeypatch.setattr(store, "_clock", lambda: time.time() + 86401)
        store.expire()
        assert "effect_probe" not in await names(client)
        assert store.retained_entries == 0


@pytest.mark.asyncio
async def test_sdk_principals_never_share_project_selection(discovery_server, monkeypatch):
    build, _ = discovery_server
    principal = {"value": "verified-principal-one"}
    monkeypatch.setattr("fastmcp.server.sessions.current_principal", lambda: principal["value"])
    server, _ = build()
    async with Client(server) as client:
        result = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        ).structured_content
        assert result["changed"] is True
        principal["value"] = "verified-principal-two"
        assert "effect_probe" not in await names(client)
        principal["value"] = "verified-principal-one"
        assert "effect_probe" in await names(client)


@pytest.mark.asyncio
async def test_project_scoped_call_keeps_validation_before_discovery(discovery_server, monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    build, _ = discovery_server
    server, _ = build("minimal")
    server.middleware.clear()
    middleware = UnityInstanceMiddleware()
    injections = []

    async def inject(context, **_kwargs):
        injections.append(context)
        await context.fastmcp_context.set_state("unity_instance", "First@first", serializable=False)

    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)
    server.add_middleware(middleware)
    async with Client(server) as client:
        malformed = await client.call_tool("effect_probe", {"strength": []}, raise_on_error=False)
        assert malformed.is_error
        assert injections == []
        result = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        ).structured_content
        assert result["changed"] is True
        valid = await client.call_tool("effect_probe", {"strength": 3.5, "enabled": False})
        assert valid.structured_content == {"strength": 3.5, "enabled": False}
