"""Report accepted native capabilities without probing or inventing peer support."""

from dataclasses import replace
import socket
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from core.config import config
from models.models import UnityInstanceInfo
from services import capabilities
from tests.http.test_plugin_hub_lifecycle import isolated_hub, wire_client  # noqa: F401
from transport.command_resume import CommandResumeClock
from transport.legacy import unity_connection as uc
from transport.legacy.stdio_auth import StdioAuthenticatedSession
from transport.plugin_hub import PluginHub


NATIVE = [
    "command_progress_v1",
    "command_resume_clock_v1",
    "command_resume_origin_v1",
    "command_resume_v1",
    "native_arbitration_v1",
]


@pytest.mark.parametrize(
    "generation", ["valid", "bad-arbitration", "missing-arbitration", "missing-resume", "old"]
)
def test_http_diagnostics_report_accepted_negotiation_not_peer_claims(wire_client, generation):
    with wire_client.websocket_connect("/hub/plugin") as peer:
        peer.receive_json()
        message = {
            "type": "register",
            "project_hash": "owned",
            "project_name": "Owned",
            "capabilities": NATIVE + ["unknown-peer-secret"],
            "arbitration_generation": "a" * 32,
            "resume_generation": "b" * 32,
            "resume_clock_unix_ms": 1_800_000_000_000,
        }
        if generation == "bad-arbitration":
            message["arbitration_generation"] = "bad"
        elif generation == "missing-arbitration":
            message.pop("arbitration_generation")
        elif generation == "missing-resume":
            message.pop("resume_generation")
        elif generation == "old":
            message["capabilities"] = []
        peer.send_json(message)
        if generation == "bad-arbitration":
            with pytest.raises(WebSocketDisconnect):
                peer.receive_json()
            observed = wire_client.portal.call(PluginHub.get_capability_snapshot, "owned")
            assert observed.status == "unavailable"
            assert observed.negotiated is None
            return
        ack = peer.receive_json()
        observed = wire_client.portal.call(PluginHub.get_capability_snapshot, "owned")
        expected = (
            NATIVE
            if generation == "valid"
            else ["command_progress_v1", "native_arbitration_v1"]
            if generation == "missing-resume"
            else ["command_progress_v1"]
            if generation == "missing-arbitration"
            else []
        )
        assert ack["capabilities"] == expected
        assert observed.negotiated == expected
        assert set(NATIVE) <= set(observed.server_advertised)
        assert "unknown-peer-secret" not in observed.model_dump_json()


@pytest.fixture
def passive_stdio(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    target = UnityInstanceInfo(
        id="Owned@0123456789abcdef",
        name="Owned",
        hash="0123456789abcdef",
        path="/owned",
        status="running",
        port=6400,
        listener_generation="a" * 32,
    )
    connection = uc.UnityConnection(port=6400, instance_id=target.id, selected_target=target)
    peer, wire = socket.socketpair()
    session = StdioAuthenticatedSession(
        "a" * 32,
        "b" * 32,
        frozenset(NATIVE + ["unknown-peer-secret"]),
        "c" * 32,
        "d" * 32,
        CommandResumeClock.capture(1_800_000_000_000),
    )
    connection.sock = wire
    connection.authenticated_session = session
    connection.session_generation = session.session_generation
    pool = uc.UnityConnectionPool()
    pool._connections[target.id] = connection
    pool._known_instances[target.id] = target
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Capability diagnostics must not connect, probe, discover or wait for I/O")

    monkeypatch.setattr(connection, "connect", forbidden)
    monkeypatch.setattr(connection, "_ensure_live_connection", forbidden)
    monkeypatch.setattr(uc, "get_unity_connection_pool", forbidden)
    try:
        yield target, connection, pool
    finally:
        wire.close()
        peer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [NATIVE, ["command_progress_v1"], []])
async def test_stdio_diagnostics_only_publish_current_authenticated_capabilities(
    passive_stdio, accepted
):
    target, connection, _ = passive_stdio
    connection.authenticated_session = replace(
        connection.authenticated_session, capabilities=frozenset(accepted + ["unknown-peer-secret"])
    )
    result = await capabilities.get_connection_evidence(target.id)
    assert result.status == "connected"
    assert result.negotiated == accepted
    assert result.server_advertised == NATIVE
    assert result.project_hash == target.hash
    assert "unknown-peer-secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    [
        "session",
        "socket",
        "generation",
        "target_generation",
        "retired",
        "pool_retired",
        "pool_busy",
        "connection_busy",
        "active_command",
        "missing_target",
        "invalid_arbitration",
        "missing_resume",
        "missing_resume_flag",
        "missing_clock",
    ],
)
async def test_stdio_diagnostics_refuse_missing_stale_or_uninspectable_session(
    passive_stdio, missing
):
    target, connection, pool = passive_stdio
    if missing == "session":
        connection.authenticated_session = None
    elif missing == "socket":
        connection.sock.close()
    elif missing == "generation":
        connection.session_generation = "replacement"
    elif missing == "target_generation":
        pool._known_instances[target.id] = target.model_copy(
            update={"listener_generation": "f" * 32}
        )
    elif missing == "retired":
        connection._retired = True
    elif missing == "pool_retired":
        pool._retired = True
    elif missing == "pool_busy":
        pool._pool_lock.acquire()
    elif missing == "connection_busy":
        connection._io_lock = threading.Lock()
        connection._io_lock.acquire()
    elif missing == "active_command":
        connection._active_commands = 1
    elif missing == "missing_target":
        pool._known_instances.clear()
    elif missing == "invalid_arbitration":
        connection.authenticated_session = replace(
            connection.authenticated_session, arbitration_generation="bad"
        )
    elif missing == "missing_resume_flag":
        connection.authenticated_session = replace(
            connection.authenticated_session, capabilities=frozenset(NATIVE) - {"command_resume_v1"}
        )
    elif missing == "missing_clock":
        connection.authenticated_session = replace(
            connection.authenticated_session, resume_clock=None
        )
    else:
        connection.authenticated_session = replace(
            connection.authenticated_session, resume_generation=None
        )
    try:
        result = await capabilities.get_connection_evidence(target.id)
        assert result.status == "unknown"
        assert result.negotiated is None
        assert result.generation is None
    finally:
        if missing == "pool_busy":
            pool._pool_lock.release()
        elif missing == "connection_busy":
            connection._io_lock.release()
