"""Owned authenticated TCP peers exercise continuation and separate cancellation sockets."""

import asyncio
from contextlib import contextmanager
import json
import secrets
import socket
import struct
import threading

import pytest

from core.config import config
from tests.timing import watchdog_timeout
from transport.legacy import unity_connection as uc
from transport.legacy.stdio_auth import authentication_proof

TOKEN = "owned-command-resume-token-" + "x" * 32
EDITOR = "e" * 32


def read_frame(peer):
    def exact(count):
        data = bytearray()
        while len(data) < count:
            chunk = peer.recv(count - len(data))
            if not chunk:
                raise EOFError
            data.extend(chunk)
        return bytes(data)

    size = struct.unpack(">Q", exact(8))[0]
    assert 0 < size < 65536
    return json.loads(exact(size))


def send_frame(peer, value):
    payload = json.dumps(value).encode()
    peer.sendall(struct.pack(">Q", len(payload)) + payload)


def result(command, state, value):
    return {
        "command_resume": {
            "version": 1,
            "invocation_id": command["resume"]["invocation_id"],
            "editor_session_id": command["resume"]["editor_session_id"],
            "state": state,
        },
        "payload": {
            "native_arbitration": {
                **command["arbitration"],
                "disposition": "started",
                "retryable": False,
            },
            "payload": value
            if value.get("status") == "error"
            else {"status": "success", "result": value},
        },
    }


@contextmanager
def resume_peer(mode="completed", reloads=1):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(watchdog_timeout(5))
    commands = []
    failures = []
    started = threading.Event()
    canceled = threading.Event()
    workers = []

    def serve_peer(peer, attempt):
        try:
            with peer:
                peer.settimeout(watchdog_timeout(5))
                generation, challenge, session = (
                    secrets.token_hex(16),
                    secrets.token_hex(32),
                    secrets.token_hex(16),
                )
                peer.sendall(
                    f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={generation} CHALLENGE={challenge}\n".encode()
                )
                request = read_frame(peer)
                nonce = request["client_nonce"]
                assert request["proof"] == authentication_proof(
                    TOKEN, ["client", generation, challenge, nonce]
                )
                send_frame(
                    peer,
                    {
                        "type": "authenticated",
                        "version": 2,
                        "session_id": session,
                        "proof": authentication_proof(
                            TOKEN, ["server", generation, challenge, nonce, session]
                        ),
                        "capabilities": ["native_arbitration_v1", "command_resume_v1"],
                        "arbitration_generation": generation,
                        "resume_generation": "f" * 32 if mode == "cold" and attempt else EDITOR,
                    },
                )
                try:
                    command = read_frame(peer)
                except EOFError:
                    assert mode == "cold" and attempt == 1
                    return
                commands.append(command)
                if mode == "cancel":
                    if command["resume"]["action"] == "cancel":
                        canceled.set()
                    else:
                        started.set()
                        assert canceled.wait(watchdog_timeout(5))
                    send_frame(
                        peer, result(command, "canceled", {"success": False, "error": "canceled"})
                    )
                elif attempt >= reloads:
                    value = {"success": True, "data": {"applied": 1, "receipt": "original"}}
                    if mode == "unknown":
                        value = {
                            "status": "error",
                            "code": "command_outcome_unknown",
                            "error": "Command continuation state: unknown",
                        }
                    send_frame(
                        peer,
                        result(command, "unknown" if mode == "unknown" else "completed", value),
                    )
        except (OSError, AssertionError, EOFError) as error:
            failures.append(error)

    def serve():
        try:
            for attempt in range(reloads + 1):
                peer, _ = listener.accept()
                worker = threading.Thread(target=serve_peer, args=(peer, attempt))
                workers.append(worker)
                worker.start()
        except OSError as error:
            failures.append(error)

    acceptor = threading.Thread(target=serve)
    acceptor.start()
    try:
        yield listener.getsockname()[1], commands, started, canceled
    finally:
        canceled.set()
        acceptor.join(watchdog_timeout(5))
        listener.close()
        for worker in workers:
            worker.join(watchdog_timeout(5))
        assert not acceptor.is_alive()
        assert not any(worker.is_alive() for worker in workers)
        assert not failures


def configure(monkeypatch, port, reconnect_failures=0):
    connection = uc.UnityConnection(
        host="127.0.0.1",
        port=port,
        instance_id="Owned@deadbeef",
        auth_token_provider=lambda _: TOKEN,
    )

    selections = 0

    def select(instance):
        nonlocal selections, reconnect_failures
        assert instance in {None, "Owned@deadbeef"}
        if not connection.sock:
            if selections and reconnect_failures:
                reconnect_failures -= 1
                raise ConnectionError("Editor is still reloading")
            selections += 1
            assert connection.connect()
        return connection

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(uc, "_unity_connection_pool", None)
    monkeypatch.setattr(uc, "get_unity_connection", select)
    monkeypatch.setattr(uc, "read_status_file", lambda *_: None)
    return connection


@pytest.mark.parametrize("mode", ["queued", "completed", "unknown", "cold"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_tcp_reconnect_retains_invocation_and_never_creates_a_new_command(
    monkeypatch, mode, asynchronous
):
    # Given a mutually authenticated peer that loses its original command reply.
    with resume_peer(mode) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        try:
            # When the still-live sender continues through its original API.
            if asynchronous:
                value = await uc.async_send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            else:
                value = uc.send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            # Then every attempted continuation keeps the original wire identity and deadline.
            value = value.model_dump() if hasattr(value, "model_dump") else value
            if mode in {"unknown", "cold"}:
                assert value["data"]["reason"] == "outcome_unknown"
            else:
                assert value == {"success": True, "data": {"applied": 1, "receipt": "original"}}
            assert len(commands) == (1 if mode == "cold" else 2)
            if len(commands) == 2:
                assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
                assert commands[1]["params"] == commands[0]["params"]
        finally:
            connection.disconnect()


@pytest.mark.asyncio
async def test_cancel_uses_separate_authenticated_socket_and_stops_queued_worker(monkeypatch):
    # Given a native queue holding the original authenticated connection's response.
    with resume_peer("cancel") as (port, commands, started, canceled):
        connection = configure(monkeypatch, port)
        params = {"action": "save"}
        pending = asyncio.create_task(
            uc.async_send_command_with_retry(
                "manage_scene", params, instance_id=connection.instance_id
            )
        )
        assert await asyncio.to_thread(started.wait, watchdog_timeout(5))
        params["action"] = "delete"
        try:
            # When its MCP caller explicitly cancels.
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            # Then a second authenticated connection tombstones that exact invocation.
            assert canceled.is_set()
            assert len(commands) == 2
            assert commands[1]["resume"] == {**commands[0]["resume"], "action": "cancel"}
            assert commands[1]["params"] == commands[0]["params"]
        finally:
            connection.disconnect()


@pytest.mark.asyncio
async def test_repeated_tcp_reloads_keep_one_original_invocation(monkeypatch):
    # Given two consecutive reloads interrupting the same queued invocation.
    with resume_peer("queued", reloads=2) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        try:
            # When the original async sender remains alive through both reloads.
            value = await uc.async_send_command_with_retry(
                "manage_scene",
                {"action": "save"},
                instance_id=connection.instance_id,
            )
            # Then every continuation retains the one original identity and deadline.
            assert value["data"]["applied"] == 1
            assert len(commands) == 3
            assert (
                commands[1]["resume"]
                == commands[2]["resume"]
                == {**commands[0]["resume"], "action": "resume"}
            )
        finally:
            connection.disconnect()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_temporary_reconnect_failure_keeps_the_original_sender_waiting(
    monkeypatch, asynchronous
):
    # Given a queued command whose Editor is temporarily unavailable after disconnect.
    with resume_peer("queued") as (port, commands, _, _):
        connection = configure(monkeypatch, port, reconnect_failures=1)
        try:
            # When the still-live sender encounters a failed reconnect before recovery.
            if asynchronous:
                value = await uc.async_send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            else:
                value = uc.send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            # Then recovery continues under the same invocation instead of failing or creating work.
            assert value["data"]["applied"] == 1
            assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
        finally:
            connection.disconnect()
