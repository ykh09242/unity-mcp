"""Narrow readiness reads exercise the real SDK and HTTP command owner."""

from tests.timing import watchdog_timeout

import asyncio
import json
from types import SimpleNamespace

from fastmcp import Client
import pytest

from services.capabilities import query_project_capabilities
from tests.http.test_project_capabilities import (
    capability_server,
    install_native_replies,
    native_observations,
)
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub


async def state(name):
    return "Fixture@hash" if name == "unity_instance" else None


def test_capability_probe_limit_schema_keeps_default_and_exposes_bounded_retry():
    # Given/When: clients discover the public capability limit schema.
    from models.capabilities import CapabilityLimits

    schema = CapabilityLimits.model_json_schema()["properties"]["max_probes"]
    # Then: existing construction keeps five while an explicit bounded retry advertises six.
    assert schema["default"] == 5
    assert schema["enum"] == [5, 6]


@pytest.mark.asyncio
async def test_build_selection_reduces_actual_dispatched_commands(command_hub):
    await install_native_replies(command_hub, native_observations())
    ctx = SimpleNamespace(get_state=state)
    full = await query_project_capabilities(ctx)
    assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 5
    command_hub.sent.clear()
    narrow = await query_project_capabilities(ctx, features=("build",))
    commands = [m["name"] for m in command_hub.sent if m["type"] == "execute"]
    assert sorted(commands) == ["get_editor_state", "get_project_info"]
    assert narrow.features["build"] == full.features["build"]
    assert narrow.features["input"].supported is None
    assert narrow.features["input"].reason == "not_requested"
    assert narrow.evidence["manage_input"].reason == "not_requested"
    assert narrow.partial is True
    assert narrow.cache_ttl_seconds == full.cache_ttl_seconds == 0
    assert PluginHub._pending == {}


def scoped_server():
    from services.registry import get_registered_resources
    from services.resources import _serialize_pydantic
    from services.resources import capabilities as _resource  # noqa: F401

    server = capability_server()
    row = next(
        item for item in get_registered_resources() if item["name"] == "scoped_project_capabilities"
    )
    server.resource(row["uri"])(_serialize_pydantic(row["func"]))
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_scoped_queries_union_sources_keep_default_and_read_fresh(command_hub, mode):
    observations = native_observations()
    await install_native_replies(command_hub, observations)
    async with Client(scoped_server(), mode=mode, cache=False) as client:
        uri = "mcpforunity://capabilities/scoped?features=build&sources=manage_input"
        data = json.loads((await client.read_resource(uri))[0].text)["data"]
        assert {m["name"] for m in command_hub.sent if m["type"] == "execute"} == {
            "get_editor_state",
            "get_project_info",
            "manage_input",
        }
        assert data["features"]["input"]["supported"] is True
        assert data["features"]["recording"]["reason"] == "not_requested"
        assert data["features"]["render_pipeline"]["reason"] == "not_requested"
        observations["manage_input"]["input_system"] = False
        fresh = json.loads((await client.read_resource(uri))[0].text)["data"]
        assert fresh["features"]["input"]["supported"] is False
        command_hub.sent.clear()
        await client.read_resource("mcpforunity://capabilities")
        assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["features=", "features=unknown", "sources=delete_asset", "features=build,,input"]
)
async def test_invalid_selection_never_dispatches_native_work(command_hub, query):
    from fastmcp.exceptions import ResourceError
    from mcp.shared.exceptions import MCPError

    await install_native_replies(command_hub, native_observations())
    async with Client(scoped_server(), mode="legacy", cache=False) as client:
        with pytest.raises((ResourceError, MCPError)):
            await client.read_resource("mcpforunity://capabilities/scoped?" + query)
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_scoped_reconnect_discards_evidence_and_keeps_unrequested_distinct(command_hub):
    await install_native_replies(command_hub, native_observations())
    reply = command_hub.websocket.send_json

    async def reconnect(message):
        await reply(message)
        if len([m for m in command_hub.sent if m["type"] == "execute"]) == 2:
            command_hub.websocket.state.plugin_generation = "replaced"

    command_hub.websocket.send_json = reconnect
    data = await query_project_capabilities(SimpleNamespace(get_state=state), features=("build",))
    assert data.target.instance_id is None
    assert data.connection.generation is None
    assert data.evidence["get_project_info"].reason == "connection_changed_or_deadline"
    assert data.evidence["manage_input"].reason == "not_requested"
    assert data.features["build"].supported is None


@pytest.mark.asyncio
async def test_concurrent_scopes_own_their_observations(command_hub):
    await install_native_replies(command_hub, native_observations())
    first, second = await asyncio.gather(
        query_project_capabilities(SimpleNamespace(get_state=state), features=("input",)),
        query_project_capabilities(SimpleNamespace(get_state=state), features=("recording",)),
    )
    assert first.features["input"].supported is True
    assert first.features["recording"].reason == "not_requested"
    assert second.features["recording"].supported is True
    assert second.features["input"].reason == "not_requested"
    assert first.readiness.status == second.readiness.status == "ready"
    assert first.limits.max_probes == second.limits.max_probes == 6
    assert first.target.execution_environment == second.target.execution_environment == "editor"
    assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 4
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_overlapping_scope_retries_only_invalidated_state_within_original_budget(
    command_hub, monkeypatch
):
    # Given: another capability query advances the epoch after the first state identity capture.
    from tests.timing import await_signal

    await install_native_replies(command_hub, native_observations())
    captured = asyncio.Event()
    release = asyncio.Event()
    read_identity = PluginHub._read_identity
    state_attempts = 0

    async def hold_first_identity(cls, session, *, authoritative=False):
        nonlocal state_attempts
        identity = await read_identity(session, authoritative=authoritative)
        if authoritative:
            state_attempts += 1
            if state_attempts == 1:
                captured.set()
                await release.wait()
        return identity

    monkeypatch.setattr(PluginHub, "_read_identity", classmethod(hold_first_identity))
    first = asyncio.create_task(
        query_project_capabilities(SimpleNamespace(get_state=state), features=("input",))
    )
    try:
        await await_signal(captured, "first authoritative identity captured")
        # When: a second scope completes its own metadata and state before releasing the first.
        second = await query_project_capabilities(
            SimpleNamespace(get_state=state), features=("recording",)
        )
        release.set()
        result = await first
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    # Then: each scope gets its own current state; invalidation never replays metadata.
    assert result.readiness.status == second.readiness.status == "ready"
    assert result.features["recording"].reason == second.features["input"].reason == "not_requested"
    assert result.target.execution_environment == second.target.execution_environment == "editor"
    assert state_attempts == 3
    commands = [item["name"] for item in command_hub.sent if item["type"] == "execute"]
    assert sorted(commands) == [
        "get_editor_state",
        "get_editor_state",
        "manage_input",
        "manage_recording",
    ]
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("exhaust_deadline", [False, True])
async def test_real_mutations_cannot_make_invalidated_capability_state_ready(
    command_hub, monkeypatch, exhaust_deadline
):
    # Given: an actual mutation advances the epoch after every captured state identity.
    import anyio
    import services.capabilities as capabilities
    from transport.unity_transport import send_with_unity_instance

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.1)
    await install_native_replies(command_hub, native_observations())
    read_identity = PluginHub._read_identity
    attempts = 0

    async def mutate_after_identity(cls, session, *, authoritative=False):
        nonlocal attempts
        identity = await read_identity(session, authoritative=authoritative)
        if authoritative:
            attempts += 1
            await send_with_unity_instance(
                None, "Fixture@hash", "manage_scene", {"action": "create"}, retry_on_reload=False
            )
            if exhaust_deadline:
                await anyio.sleep_forever()
        return identity

    monkeypatch.setattr(PluginHub, "_read_identity", classmethod(mutate_after_identity))
    # When: the selected query spends its original finite read budget.
    started = anyio.current_time()
    result = await query_project_capabilities(SimpleNamespace(get_state=state), features=("input",))
    # Then: retries are finite, no stale state dispatches, and metadata remains scoped.
    assert anyio.current_time() - started < 0.5
    assert attempts == (1 if exhaust_deadline else 2)
    assert result.readiness.status == "unknown"
    assert result.features["input"].supported is True
    assert result.features["recording"].reason == "not_requested"
    assert result.evidence["get_editor_state"].status == "unknown"
    assert result.evidence["get_editor_state"].reason == (
        "not_observed" if exhaust_deadline else "read_invalidated"
    )
    commands = [item["name"] for item in command_hub.sent if item["type"] == "execute"]
    assert commands.count("manage_scene") == attempts
    assert commands.count("manage_input") == 1
    assert "get_editor_state" not in commands
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_capacity_keeps_unrequested_distinct(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 0)
    result = await query_project_capabilities(SimpleNamespace(get_state=state), features=("build",))
    assert command_hub.sent == []
    assert result.evidence["get_project_info"].reason == "command_capacity"
    assert result.evidence["manage_input"].reason == "not_requested"


def test_scoped_resource_requires_read_permission():
    from services.operation_permissions import (
        OperationGrants,
        Permission,
        authorize_resource,
        OperationPermissionError,
    )

    uri = "mcpforunity://capabilities/scoped?features=build"
    authorize_resource(OperationGrants(frozenset({Permission.READ}), scoped=True), uri)
    with pytest.raises(OperationPermissionError):
        authorize_resource(OperationGrants(frozenset({Permission.EDIT}), scoped=True), uri)
    with pytest.raises(OperationPermissionError):
        authorize_resource(
            OperationGrants(frozenset({Permission.READ}), scoped=True),
            uri.replace("scoped?", "scoped/other?"),
        )


@pytest.mark.asyncio
async def test_scoped_selection_spends_the_same_bounded_read_budget(command_hub, monkeypatch):
    import anyio
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.03)
    async with Client(scoped_server(), mode="legacy", cache=False) as client:
        await client.call_tool("select_fixture")
        async with command_hub.registry._lock:
            with anyio.fail_after(watchdog_timeout()):
                result = await client.read_resource(
                    "mcpforunity://capabilities/scoped?features=build"
                )
    data = json.loads(result[0].text)["data"]
    assert data["connection"]["status"] == "unknown"
    assert data["target"]["instance_id"] is None
    assert command_hub.sent == []
