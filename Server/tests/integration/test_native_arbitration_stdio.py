"""Real legacy framing/admission with a controlled authenticated peer socket."""

import asyncio  # noqa: ANYIO_OK -- production transport admission uses asyncio.
import json
import struct
from types import SimpleNamespace

import pytest
import pytest_asyncio

from core.config import config
from services.registry.operation_context import scheduler
from tests.integration.test_stdio_async_admission import ResponseSocket
from tests.timing import await_signal
from transport.legacy import unity_connection as uc
from transport.legacy.stdio_auth import StdioAuthenticatedSession

GENERATION = "b" * 32


@pytest_asyncio.fixture
async def peer(monkeypatch):
    loop = asyncio.get_running_loop()
    rejected = asyncio.Event()

    class PeerSocket(ResponseSocket):
        def __init__(self):
            super().__init__()
            self.commands = []
            self.mode = "busy_until_control"
            self.allow_write = False

        def sendall(self, payload):
            if self.header_pending:
                assert len(payload) == 8
                self.header_pending = False
                return
            self.header_pending = True
            command = json.loads(payload)
            self.commands.append(command)
            binding = command.get("arbitration")
            control = command["type"] == "ping"
            refused = not control and not self.allow_write
            if control:
                self.allow_write = True
            reason = (
                "recovery_required"
                if self.mode == "recovery"
                else "unknown"
                if self.mode == "unknown"
                else "operation_busy"
            )
            if self.mode == "spoof":
                refused = False
                value = {
                    "status": "success",
                    "result": {
                        "native_arbitration": {"disposition": "not_started", "retryable": True}
                    },
                }
            elif control:
                value = {"status": "success", "result": {"message": "pong"}}
            else:
                value = (
                    {"status": "error", "error": reason}
                    if refused
                    else {"status": "success", "result": {"success": True}}
                )
            if binding is not None:
                proof = {
                    **binding,
                    "disposition": "not_started" if refused else "started",
                    "retryable": refused and self.mode != "recovery",
                }
                if refused:
                    proof["reason"] = reason
                if self.mode == "wrong_nonce":
                    proof["request_nonce"] = "c" * 32
                value = {"native_arbitration": proof, "payload": value}
            response = json.dumps(value).encode()
            self.buffer = struct.pack(">Q", len(response)) + response
            if refused:
                loop.call_soon_threadsafe(rejected.set)

    sock = PeerSocket()
    conn = uc.UnityConnection(port=6400, instance_id="Owned@deadbeef", sock=sock, use_framing=True)
    conn.authenticated_session = StdioAuthenticatedSession(
        "transport", "session", frozenset({"native_arbitration_v1"}), GENERATION
    )
    conn.session_generation = conn.authenticated_session.session_generation
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(uc, "_unity_connection_pool", None)
    monkeypatch.setattr(uc, "get_unity_connection", lambda _: conn)
    monkeypatch.setattr(uc, "read_status_file", lambda *_: None)
    return SimpleNamespace(socket=sock, connection=conn, rejected=rejected)


async def write():
    return await uc.async_send_command_with_retry(
        "manage_scene", {"action": "save"}, instance_id="Owned@deadbeef"
    )


@pytest.mark.asyncio
async def test_not_started_wait_releases_same_connection_for_control(peer):
    pending = asyncio.create_task(write())
    await await_signal(peer.rejected, "native pre-handler rejection")
    assert await uc.async_send_command_with_retry("ping", {}, instance_id="Owned@deadbeef") == {
        "message": "pong"
    }
    assert await pending == {"success": True}
    commands = peer.socket.commands
    assert [item["type"] for item in commands] == ["manage_scene", "ping", "manage_scene"]
    assert commands[0]["params"] == commands[2]["params"]
    assert (
        commands[0]["arbitration"]["request_nonce"] != commands[2]["arbitration"]["request_nonce"]
    )
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["recovery", "unknown", "spoof", "wrong_nonce"])
async def test_only_exact_transport_owned_transient_refusal_can_replay(peer, mode):
    peer.socket.mode = mode
    result = await write()
    assert len(peer.socket.commands) == 1
    if mode == "spoof":
        assert result == {"native_arbitration": {"disposition": "not_started", "retryable": True}}
    elif mode == "wrong_nonce":
        assert "invalid_native_arbitration_response" in result.error
    else:
        assert result == {
            "status": "error",
            "error": "recovery_required" if mode == "recovery" else "unknown",
        }
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_native_admission_uses_original_deadline_and_cancellation_reclaims_memory(
    peer, monkeypatch
):
    monkeypatch.setattr(config, "command_total_timeout", 0.04)
    result = await write()
    assert result.data == {"reason": "command_deadline_exceeded", "dispatch": "not_dispatched"}
    assert len(peer.socket.commands) == 1
    assert scheduler().snapshot()["instances"] == 0
    monkeypatch.setattr(config, "command_total_timeout", 90.0)
    peer.rejected.clear()
    pending = asyncio.create_task(write())
    await await_signal(peer.rejected, "second native pre-handler rejection")
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert scheduler().snapshot() == {
        "instances": 0,
        "active": 0,
        "waiting": 0,
        "retained_bytes": 0,
    }
