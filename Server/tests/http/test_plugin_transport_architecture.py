"""Real ASGI plugin transport negotiation, ownership and retention regressions."""
import asyncio
from concurrent.futures import CancelledError
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from models.response_limits import ResponseOwner, response_owner
from services.api_key_service import ApiKeyService, ValidationResult
from transport.editor_state_store import EditorStateStore
from transport.large_result_assembler import CHUNK_PAYLOAD_BYTES, MAGIC, THRESHOLD_BYTES
from transport.plugin_hub import PluginHub
from transport.charge_ledger import ChargeLedger
from transport.plugin_registry import PluginRegistry

FEATURES = ["large_result_v1", "editor_state_v1"]


@pytest.fixture
def client(monkeypatch):
    # Every socket/principal below is synthetic and stays inside this ASGI app.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted",
                 "_retained_results", "_raw_results"):
        monkeypatch.setattr(PluginHub, name, ChargeLedger() if name in ('_retained_results', '_raw_results') else {}, raising=False)
    for name in ("_registry", "_lock", "_loop", "_mcp", "_large_results"):
        monkeypatch.setattr(PluginHub, name, None, raising=False)
    monkeypatch.setattr(PluginHub, "_editor_states", EditorStateStore(), raising=False)
    monkeypatch.setattr(PluginHub, "KEEP_ALIVE_INTERVAL", 3600)

    async def validate(key, **kwargs):
        return ValidationResult(valid=True, user_id=key)

    monkeypatch.setattr(ApiKeyService, "_instance", SimpleNamespace(
        validate=validate, aclose=AsyncMock()))
    registry = PluginRegistry()
    clock = {"wall": 100.0, "mono": 10.0}

    @asynccontextmanager
    async def lifespan(app):
        PluginHub.configure(registry)
        PluginHub._editor_states = EditorStateStore(
            wall_time=lambda: clock["wall"], monotonic=lambda: clock["mono"])
        try:
            yield
        finally:
            await PluginHub.shutdown()

    app = Starlette(routes=[WebSocketRoute("/plugin", PluginHub)], lifespan=lifespan)
    app.state.clock = clock
    with TestClient(app) as wire:
        yield wire


def register(wire, project="owner", capabilities=None):
    welcome = wire.receive_json()
    payload = {"type": "register", "project_name": "Fixture", "project_hash": project}
    if capabilities is not None:
        payload["capabilities"] = capabilities
    wire.send_json(payload)
    registered = wire.receive_json()
    assert registered["type"] == "registered"
    return registered["session_id"], welcome, registered


def start_command(client, wire, session_id, *, owner=None, done=None):
    async def execute():
        token = response_owner.set(owner)
        try:
            return await PluginHub.send_command(session_id, "run_tests", {})
        finally:
            response_owner.reset(token)
            if done is not None:
                done.set()

    task = client.portal.start_task_soon(execute)
    message = wire.receive_json()
    assert message["type"] == "execute"
    return task, message["id"]


def barrier(client, wire, session_id):
    task, command_id = start_command(client, wire, session_id)
    wire.send_json({"type": "command_result", "id": command_id, "result": {"success": True}})
    assert task.result(timeout=2) == {"success": True}


def payload_and_frames(command_id, *, envelope_id=None):
    result = {"success": True, "data": {"synthetic": "성공" * 50000}}
    payload = json.dumps({"type": "command_result", "id": envelope_id or command_id,
                          "result": result}, ensure_ascii=False).encode()
    assert len(payload) >= THRESHOLD_BYTES
    chunks = [MAGIC + command_id.encode("ascii") + offset.to_bytes(4, "big")
              + payload[offset:offset + CHUNK_PAYLOAD_BYTES]
              for offset in range(0, len(payload), CHUNK_PAYLOAD_BYTES)]
    return result, payload, chunks


def begin(wire, command_id, payload, chunks):
    wire.send_json({"type": "result_start", "id": command_id,
                    "total_bytes": len(payload), "chunk_count": len(chunks)})


def state_message(sequence=7, observed=100000, *, reload_pending=False):
    return {"type": "editor_state", "epoch": "owned-domain", "sequence": sequence,
            "observed_at_unix_ms": observed,
            "state": {"schema_version": "unity-mcp/editor_state@2", "sequence": sequence,
                      "observed_at_unix_ms": observed,
                      "compilation": {"is_compiling": False, "is_domain_reload_pending": reload_pending}}}


def cached(client, project="owner", user="alice"):
    return client.portal.call(PluginHub.get_cached_editor_state, project, user)


def test_old_peer_gets_empty_features_and_unchanged_text_result(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, registered = register(wire)
        assert registered["capabilities"] == []
        task, cid = start_command(client, wire, sid)
        result = {"success": True, "data": "ordinary"}
        wire.send_json({"type": "command_result", "id": cid, "result": result})
        assert task.result(timeout=2) == result
    assert PluginHub._raw_results == {}


def test_capabilities_are_negotiated_as_supported_intersection(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, welcome, registered = register(wire, capabilities=[*FEATURES, "unsupported"])
        assert set(welcome["capabilities"]) == {*FEATURES, "command_cancel_v1", "large_result_gzip_v1"}
        assert set(registered["capabilities"]) == set(FEATURES)
        state = PluginHub._connections[sid].state
        assert state.plugin_session_id == sid
        assert state.plugin_features == frozenset(FEATURES)
        assert state.plugin_generation


def test_negotiated_large_result_completes_after_interleaved_control(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        task, cid = start_command(client, wire, sid)
        expected, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        wire.send_json({"type": "pong", "session_id": sid})
        barrier(client, wire, sid)
        assert len(PluginHub._raw_results) == 1
        assert not task.done()
        for chunk in chunks[1:]:
            wire.send_bytes(chunk)
        assert task.result(timeout=2) == expected
        assert PluginHub._raw_results == {}
        assert PluginHub._large_results.retained_bytes == 0


def test_foreign_socket_cannot_allocate_or_finish_owner_result(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as owner:
        owner_sid, _, _ = register(owner, capabilities=FEATURES)
        task, cid = start_command(client, owner, owner_sid)
        _, payload, chunks = payload_and_frames(cid)
        with client.websocket_connect("/plugin", headers={"x-api-key": "bob"}) as foreign:
            foreign_sid, _, _ = register(foreign, "foreign", FEATURES)
            begin(foreign, cid, payload, chunks)
            foreign.send_bytes(chunks[0])
            barrier(client, foreign, foreign_sid)
            assert PluginHub._raw_results == {}
            assert not task.done()
        owner.send_json({"type": "command_result", "id": cid, "result": {"success": True}})
        assert task.result(timeout=2) == {"success": True}


def test_cancellation_releases_partial_raw_bytes_and_ignores_late_chunks(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        done = Event()
        task, cid = start_command(client, wire, sid, done=done)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        barrier(client, wire, sid)
        assert PluginHub._raw_results
        task.cancel()
        assert done.wait(2)
        with pytest.raises(CancelledError):
            task.result(timeout=2)
        for chunk in chunks[1:]:
            wire.send_bytes(chunk)
        barrier(client, wire, sid)
        assert PluginHub._raw_results == {}
        assert PluginHub._large_results.retained_bytes == 0


@pytest.mark.parametrize("kind", ["offset", "short", "envelope_id"])
def test_malformed_active_large_transfer_closes_only_its_socket(client, kind):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        with client.websocket_connect("/plugin", headers={"x-api-key": "bob"}) as healthy:
            healthy_sid, _, _ = register(healthy, "healthy", FEATURES)
            unrelated, unrelated_id = start_command(client, healthy, healthy_sid)
            task, cid = start_command(client, wire, sid)
            _, payload, chunks = payload_and_frames(cid, envelope_id="another-command" if kind == "envelope_id" else None)
            begin(wire, cid, payload, chunks)
            if kind == "offset":
                wire.send_bytes(chunks[1])
            elif kind == "short":
                wire.send_bytes(chunks[0][:-1])
            else:
                for chunk in chunks:
                    wire.send_bytes(chunk)
            with pytest.raises(WebSocketDisconnect):
                wire.receive_json()
            assert task.result(timeout=2)["success"] is False
            assert not unrelated.done()
            healthy.send_json({"type": "command_result", "id": unrelated_id, "result": {"success": True}})
            assert unrelated.result(timeout=2) == {"success": True}
    assert PluginHub._raw_results == {}
    assert PluginHub._large_results.retained_bytes == 0


def test_original_command_deadline_releases_partial_bytes_and_late_data_is_ignored(client, monkeypatch):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.2)
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        task, cid = start_command(client, wire, sid)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        barrier(client, wire, sid)
        assert PluginHub._raw_results
        # Await the actual command deadline, without delaying or polling manually.
        with pytest.raises(TimeoutError):
            task.result(timeout=2)
        assert PluginHub._raw_results == {}
        assert PluginHub._large_results.retained_bytes == 0
        for chunk in chunks[1:]:
            wire.send_bytes(chunk)
        barrier(client, wire, sid)


@pytest.mark.parametrize("scope", ["session", "user", "global"])
def test_raw_allocation_counts_existing_retained_responses(client, monkeypatch, scope):
    ledger_owner = ResponseOwner()
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        first, cid = start_command(client, wire, sid, owner=ledger_owner)
        retained_result = {"success": True, "data": "retained" * 100}
        wire.send_json({"type": "command_result", "id": cid, "result": retained_result})
        assert first.result(timeout=2) == retained_result
        retained_charge = PluginHub._retained_results[cid]["bytes"]
        ceiling = {"session": "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
                   "user": "MAX_RETAINED_RESULT_BYTES_PER_USER", "global": "MAX_RETAINED_RESULT_BYTES"}[scope]
        second_user = "bob" if scope == "global" else "alice"
        second_context = (nullcontext(wire) if scope == "session" else
                          client.websocket_connect("/plugin", headers={"x-api-key": second_user}))
        with second_context as second_wire:
            second_sid = sid
            if scope != "session":
                second_sid, _, _ = register(second_wire, "second", FEATURES)
            second, second_id = start_command(client, second_wire, second_sid)
            _, payload, chunks = payload_and_frames(second_id)
            # Raw reservation includes owned bytes and conservative decoded Unicode.
            monkeypatch.setattr(PluginHub, ceiling, retained_charge + 5 * len(payload) + 4096 - 1)
            begin(second_wire, second_id, payload, chunks)
            reply = second.result(timeout=2)
            assert reply["success"] is False
            assert reply["data"]["reason"] == "result_capacity"
            assert PluginHub._raw_results == {}
            assert cid in PluginHub._retained_results
            # Rejected/late data must leave this multiplexed connection usable.
            second_wire.send_bytes(chunks[0])
            barrier(client, second_wire, second_sid)
        client.portal.call(ledger_owner.release)
    assert PluginHub._retained_results == {}


@pytest.mark.parametrize("cleanup", ["disconnect", "eviction", "shutdown"])
def test_connection_cleanup_releases_partial_raw_and_cached_state(client, cleanup):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        task, cid = start_command(client, wire, sid)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        wire.send_json(state_message())
        barrier(client, wire, sid)
        assert cached(client)["sequence"] == 7
        assert PluginHub._raw_results
        if cleanup == "eviction":
            client.portal.call(PluginHub._evict_connection, sid, "owned regression")
        elif cleanup == "shutdown":
            client.portal.call(PluginHub.shutdown)
        else:
            wire.close()
    assert task.result(timeout=2)["success"] is False
    assert PluginHub._raw_results == {}
    assert PluginHub._large_results is None or PluginHub._large_results.retained_bytes == 0
    if PluginHub.is_configured():
        assert cached(client) is None


def test_state_push_is_negotiated_and_owned_by_registered_socket(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as alice:
        alice_sid, _, _ = register(alice, capabilities=FEATURES)
        alice.send_json(state_message())
        barrier(client, alice, alice_sid)
        assert cached(client)["sequence"] == 7
        assert cached(client, user="bob") is None
        with client.websocket_connect("/plugin", headers={"x-api-key": "bob"}) as bob:
            bob_sid, _, _ = register(bob, "foreign", FEATURES)
            spoof = state_message(sequence=99)
            spoof["session_id"] = alice_sid
            bob.send_json(spoof)
            barrier(client, bob, bob_sid)
            assert cached(client)["sequence"] == 7
            assert cached(client, "foreign", "bob") is None
    assert cached(client) is None


def test_old_peer_cannot_populate_state_cache_without_negotiation(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire)
        wire.send_json(state_message())
        barrier(client, wire, sid)
        assert cached(client) is None


def test_replacement_registration_invalidates_old_generation_raw_and_state(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as old:
        old_sid, _, _ = register(old, capabilities=FEATURES)
        old_generation = PluginHub._connections[old_sid].state.plugin_generation
        task, cid = start_command(client, old, old_sid)
        _, payload, chunks = payload_and_frames(cid)
        begin(old, cid, payload, chunks)
        old.send_bytes(chunks[0])
        old.send_json(state_message())
        barrier(client, old, old_sid)
        assert cached(client)["sequence"] == 7
        with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as replacement:
            new_sid, _, _ = register(replacement, capabilities=FEATURES)
            assert new_sid != old_sid
            assert PluginHub._connections[new_sid].state.plugin_generation != old_generation
            assert task.result(timeout=2)["success"] is False
            assert PluginHub._raw_results == {}
            assert cached(client) is None
            reset_state = state_message(sequence=1)
            reset_state["epoch"] = "new-domain"
            replacement.send_json(reset_state)
            barrier(client, replacement, new_sid)
            assert cached(client)["sequence"] == 1


def test_cached_resource_reads_reuse_push_but_strict_and_stale_reads_use_rpc(client, monkeypatch):
    import services.resources.editor_state as resource

    monkeypatch.setattr(resource, "get_unity_instance_from_context", AsyncMock(return_value="owner"))
    authoritative = state_message(sequence=99)["state"]
    rpc = AsyncMock(side_effect=lambda *args, **kwargs: {"success": True, "data": deepcopy(authoritative)})
    monkeypatch.setattr(resource.unity_transport, "send_with_unity_instance", rpc)
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="alice"))
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        wire.send_json(state_message())
        barrier(client, wire, sid)
        pushed = client.portal.call(resource.get_editor_state, ctx)
        assert pushed.data["sequence"] == 7
        rpc.assert_not_awaited()
        strict = client.portal.call(resource.get_editor_state_authoritative, ctx)
        assert strict.data["sequence"] == 99
        assert rpc.await_count == 1
        assert rpc.call_args.kwargs["editor_state_read_mode"] == "authoritative"
        client.app.state.clock.update(wall=103, mono=13)
        expired = client.portal.call(resource.get_editor_state, ctx)
        assert expired.data["sequence"] == 99
        assert rpc.await_count == 2
        assert rpc.call_args.kwargs["editor_state_read_mode"] == "ordinary"
