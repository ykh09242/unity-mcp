"""Real legacy framing/admission with a controlled authenticated peer socket."""

import asyncio  # noqa: ANYIO_OK -- production transport admission uses asyncio.
import json
import struct
from types import SimpleNamespace
from dataclasses import replace

import pytest
import pytest_asyncio

from core.config import config
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationAdmission, OperationScope
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
            self.applied_count = 0

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
            if not refused and command["type"] == "manage_script":
                value = {
                    "status": "success",
                    "result": {
                        "success": True,
                        "data": {
                            "contents": "class Owned {}",
                            "sha256": "a" * 64,
                            "lengthBytes": 14,
                        },
                    },
                }
            if self.mode == "started_reload_hint":
                refused = False
                self.applied_count += 1
                value = {
                    "status": "success",
                    "result": {"success": False, "hint": "retry", "data": {"reason": "reloading"}},
                }
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
@pytest.mark.parametrize(
    "mode", ["recovery", "unknown", "spoof", "wrong_nonce", "started_reload_hint"]
)
async def test_only_exact_transport_owned_transient_refusal_can_replay(peer, mode):
    peer.socket.mode = mode
    result = await write()
    assert len(peer.socket.commands) == 1
    if mode == "spoof":
        assert result == {"native_arbitration": {"disposition": "not_started", "retryable": True}}
    elif mode == "wrong_nonce":
        assert "invalid_native_arbitration_response" in result.error
    elif mode == "started_reload_hint":
        assert result == {"success": False, "hint": "retry", "data": {"reason": "reloading"}}
        assert peer.socket.applied_count == 1
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
    backoff = asyncio.Event()
    sleep = asyncio.sleep

    async def observed_sleep(delay, *args, **kwargs):
        if delay == 0.1:
            backoff.set()
        return await sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", observed_sleep)
    peer.rejected.clear()
    pending = asyncio.create_task(write())
    await await_signal(backoff, "completed native pre-handler rejection waiting off-wire")
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert scheduler().snapshot() == {
        "instances": 0,
        "active": 0,
        "waiting": 0,
        "retained_bytes": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["transport", "native"])
async def test_generation_change_while_waiting_wire_gate_is_not_dispatched(
    peer, monkeypatch, change
):
    gate = peer.connection._async_admission(asyncio.get_running_loop())
    await gate.acquire()
    waiting = asyncio.Event()
    acquire = gate.acquire

    async def observe():
        waiting.set()
        return await acquire()

    monkeypatch.setattr(gate, "acquire", observe)
    pending = asyncio.create_task(write())
    await await_signal(waiting, "selected request waiting for its exact wire")
    if change == "transport":
        peer.connection.session_generation = "replacement-session"
    else:
        peer.connection.authenticated_session = replace(
            peer.connection.authenticated_session, arbitration_generation="c" * 32
        )
    gate.release()
    result = await pending
    assert result["data"] == {"reason": "operation_target_changed", "dispatch": "not_dispatched"}
    assert peer.socket.commands == []
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_direct_native_queue_charges_payload_before_retention(peer, monkeypatch):
    owner = scheduler()
    monkeypatch.setattr(owner, "_max_retained_bytes", 64)
    scope = OperationScope("local", "deadbeef", "holder")
    async with owner.session(OperationAdmission(scope, "exclusive", lambda: True)):
        result = await uc.async_send_command_with_retry(
            "custom_unknown", {"text": "x" * 10000}, instance_id="Owned@deadbeef"
        )
        assert result.error == "operation_capacity"
        assert owner.snapshot()["waiting"] == owner.snapshot()["retained_bytes"] == 0
        assert peer.socket.commands == []


@pytest.mark.asyncio
async def test_connection_handshake_cannot_replace_captured_generation_before_payload(
    peer, monkeypatch
):
    peer.connection.sock = None

    def connect(*args, **kwargs):
        peer.connection.sock = peer.socket
        peer.connection.session_generation = "replacement-handshake"
        return True

    monkeypatch.setattr(peer.connection, "connect", connect)
    result = await write()
    assert result["data"] == {"reason": "operation_target_changed", "dispatch": "not_dispatched"}
    assert not peer.socket.commands


@pytest.mark.asyncio
async def test_old_reload_wait_cannot_drop_whole_operation_generation_guard(peer, monkeypatch):
    observations = iter([{"reloading": True}, None])
    monkeypatch.setattr(uc, "read_status_file", lambda *_: next(observations, None))
    result = await write()
    assert result["data"] == {"reason": "operation_target_changed", "dispatch": "not_dispatched"}
    assert not peer.socket.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("negotiated", [True, False])
async def test_mutation_wrapper_never_replays_handler_reload_hint(peer, monkeypatch, negotiated):
    from services.tools import refresh_unity

    monkeypatch.setattr(config, "transport_mode", "stdio")
    if not negotiated:
        peer.connection.authenticated_session = None
    peer.socket.mode = "started_reload_hint"

    async def ready(*args, **kwargs):
        return True, 0

    monkeypatch.setattr(refresh_unity, "wait_for_editor_ready", ready)
    result = await refresh_unity._send_mutation(
        None,
        "Owned@deadbeef",
        "manage_script",
        {"action": "create", "name": "Owned", "path": "Assets"},
    )
    assert result == {"success": False, "hint": "retry", "data": {"reason": "reloading"}}
    assert len(peer.socket.commands) == peer.socket.applied_count == 1
    assert ("arbitration" in peer.socket.commands[0]) is negotiated


@pytest.mark.asyncio
async def test_mutation_wrapper_retries_only_typed_local_zero_dispatch_preflight(peer, monkeypatch):
    from services.tools import refresh_unity

    monkeypatch.setattr(config, "transport_mode", "stdio")
    original = peer.connection.authenticated_session
    replacement = type(peer.socket)()
    replacement.allow_write = True
    states = iter([{"reloading": True}, None])
    monkeypatch.setattr(uc, "read_status_file", lambda *_: next(states, None))
    ready_calls = []

    async def ready(*args, **kwargs):
        if not ready_calls:
            peer.connection.sock = replacement
            peer.connection.authenticated_session = replace(
                original, session_id="replacement", arbitration_generation="c" * 32
            )
            peer.connection.session_generation = (
                peer.connection.authenticated_session.session_generation
            )
        ready_calls.append(True)
        return True, 0

    monkeypatch.setattr(refresh_unity, "wait_for_editor_ready", ready)
    result = await refresh_unity._send_mutation(
        None,
        "Owned@deadbeef",
        "manage_script",
        {"action": "create", "name": "Owned", "path": "Assets"},
    )
    assert result["success"] is True
    assert peer.socket.commands == []
    assert len(replacement.commands) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["batch_execute", "batch_apply_preview", "batch_validate"])
async def test_exact_not_started_batch_envelope_is_never_reexecuted(peer, command):
    result = await uc.async_send_command_with_retry(
        command, {"commands": [], "action": []}, instance_id="Owned@deadbeef"
    )
    assert result == {"status": "error", "error": "operation_busy"}
    assert len(peer.socket.commands) == 1


@pytest.mark.asyncio
async def test_queued_legacy_payload_above_http_limit_reaches_real_framing(peer, monkeypatch):
    size = 33 * 1024 * 1024
    peer.socket.allow_write = True
    owner = scheduler()
    queued = asyncio.Event()
    wake = owner._wake

    def observe(key, state):
        wake(key, state)
        if state.waiting:
            queued.set()

    monkeypatch.setattr(owner, "_wake", observe)
    async with owner.session(
        OperationAdmission(OperationScope("local", "deadbeef", "holder"), "exclusive", lambda: True)
    ):
        pending = asyncio.create_task(
            uc.async_send_command_with_retry(
                "custom_large", {"text": "x" * size}, instance_id="Owned@deadbeef"
            )
        )
        await await_signal(queued, "large legacy request retained behind its instance predecessor")
        assert size < owner.snapshot()["retained_bytes"] < 1024 * 1024 * 1024
        assert peer.socket.commands == []
    assert await pending == {"success": True}
    assert len(peer.socket.commands[0]["params"]["text"]) == size
    assert owner.snapshot()["retained_bytes"] == 0
    peer.socket.commands.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,action",
    [("get_sha", "get_sha"), ("validate_script", "validate"), ("find_in_file", "read")],
)
async def test_actual_public_script_read_reaches_canonical_native_admission(
    peer, monkeypatch, name, action
):
    from services.registry.operation_invocation import wrap_tool
    from services.tools.manage_script import get_sha, validate_script
    from services.tools.find_in_file import find_in_file

    class Context:
        async def get_state(self, key, default=None):
            return "Owned@deadbeef" if key == "unity_instance" else default

    monkeypatch.setattr(config, "transport_mode", "stdio")
    peer.socket.allow_write = True
    settled = asyncio.Event()
    release = scheduler()._release

    def observed_release(owner):
        release(owner)
        settled.set()

    monkeypatch.setattr(scheduler(), "_release", observed_release)
    function = {
        "get_sha": get_sha,
        "validate_script": validate_script,
        "find_in_file": find_in_file,
    }[name]
    arguments = {
        "uri": "Assets/Owned.cs",
        **({"pattern": "Owned"} if name == "find_in_file" else {}),
    }
    result = await wrap_tool(function, name, "manage_script")(Context(), **arguments)
    assert result["success"] is True, result
    assert len(peer.socket.commands) == 1
    assert peer.socket.commands[0]["type"] == "manage_script"
    assert peer.socket.commands[0]["params"]["action"] == action
    await await_signal(settled, "whole invocation and actual delegated workers settled")
    assert scheduler().snapshot()["instances"] == 0
