"""Owned authenticated TCP peers exercise continuation and separate cancellation sockets."""

import asyncio
from contextlib import contextmanager
import json
import secrets
import socket
import struct
import threading
import time

import pytest

from core.config import config
from models.models import UnityInstanceInfo
from services import command_progress as progress
from tests.test_stdio_command_progress import RecordingContext, _control, _write
from tests.timing import watchdog_timeout
from transport.legacy import unity_connection as uc
from transport.legacy.stdio_auth import authentication_proof
from tests.receipt_resume_support import prepared_receipt

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
    waiting = state in {"queued", "executing"}
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
                "disposition": "not_started" if waiting else "started",
                "retryable": waiting,
                **({"reason": "operation_busy"} if waiting else {}),
            },
            "payload": value
            if value.get("status") == "error"
            else {"status": "success", "result": value},
        },
    }


@contextmanager
def resume_peer(
    mode="completed",
    reloads=1,
    *,
    origin=False,
    business_result=None,
    clock_sample=None,
    progress_callback=None,
):
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
                    f"{1 if mode in ('cancel', 'cancel_after_completion') else attempt + 1:032x}",
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
                        "capabilities": [
                            "native_arbitration_v1",
                            "command_resume_v1",
                            *(["command_progress_v1"] if progress_callback is not None else []),
                            *(["command_resume_origin_v1"] if origin else []),
                            *(["command_resume_clock_v1"] if clock_sample is not None else []),
                        ],
                        **(
                            {"resume_clock_unix_ms": clock_sample + attempt * 5000}
                            if clock_sample is not None
                            else {}
                        ),
                        "arbitration_generation": generation,
                        "resume_generation": "f" * 32 if mode == "cold" and attempt else EDITOR,
                    },
                )
                try:
                    command = read_frame(peer)
                    prelude = None
                    if command.get("type") == "command_progress_begin":
                        prelude, command = command, read_frame(peer)
                except EOFError:
                    assert (mode == "cold" and attempt == 1) or (
                        mode == "preflight" and not attempt
                    )
                    return
                commands.append(command)
                if progress_callback is not None:
                    progress_callback(peer, command, attempt, prelude)
                if mode in {"cancel", "cancel_after_completion"}:
                    control = command["resume"]["action"] == "cancel"
                    if control:
                        canceled.set()
                    else:
                        started.set()
                        assert canceled.wait(watchdog_timeout(5))
                    state = "completed" if mode == "cancel_after_completion" else "canceled"
                    value = (
                        {"status": "error", "error": "Original handler failed"}
                        if state == "completed"
                        else {
                            "status": "error",
                            "code": "command_resume_canceled",
                            "error": "Command continuation state: canceled",
                        }
                    )
                    send_frame(peer, result(command, state, value))
                    if control:
                        assert peer.recv(1) == b""  # The control socket is owned by cleanup.
                elif mode in {"queued_then_offline", "applied_then_offline"}:
                    send_frame(
                        peer,
                        result(
                            command, "queued", {"status": "error", "code": "command_resume_queued"}
                        ),
                    )
                    if mode == "applied_then_offline":
                        commands.append(read_frame(peer))
                        started.set()  # The mutation is applied but its reply is lost.
                elif mode == "executing" and attempt < reloads:
                    send_frame(peer, result(command, "executing", {"status": "error"}))
                elif attempt >= reloads:
                    value = (
                        business_result
                        if business_result is not None
                        else {"success": True, "data": {"applied": 1, "receipt": "original"}}
                    )
                    if mode == "unknown":
                        value = {
                            "status": "error",
                            "code": "command_outcome_unknown",
                            "error": "Command continuation state: unknown",
                        }
                    reply = result(command, "unknown" if mode == "unknown" else "completed", value)
                    if mode == "malformed":
                        reply["command_resume"]["invocation_id"] = "0" * 32
                    send_frame(peer, reply)
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
            connection.selected_target = UnityInstanceInfo(
                id=connection.instance_id,
                name="Owned",
                path="/Owned/Assets",
                hash="deadbeef",
                port=port,
                status="running",
                listener_generation=f"{selections:032x}",
            )
            assert connection.connect()
        return connection

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    pool = uc.UnityConnectionPool()
    pool._connections[connection.instance_id] = connection
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)
    monkeypatch.setattr(uc, "get_unity_connection", select)
    monkeypatch.setattr(uc, "read_status_file", lambda *_: None)
    return connection


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
async def test_stdio_reload_keeps_progress_bound_to_original_foreground(monkeypatch, asynchronous):
    # Given: an executing batch loses its authenticated wire after a visible settled item.
    ctx = RecordingContext()
    preludes = []

    def observe(peer, command, attempt, prelude):
        if prelude is None:
            return
        preludes.append(prelude)
        if attempt:
            assert command["resume"]["action"] == "resume"
            assert preludes[0]["id"] != prelude["id"]
            _write(peer, _control(preludes[0]["id"], completed=2))
        _write(peer, _control(prelude["id"], completed=attempt + 1))
        assert ctx.acknowledged.wait(watchdog_timeout())
        ctx.acknowledged.clear()

    with resume_peer(progress_callback=observe) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        monkeypatch.setattr(config, "command_total_timeout", 10.0)
        params = {"commands": [{"tool": "manage_scene", "params": {"action": "save"}}] * 2}
        try:
            async with progress.foreground_progress(ctx):
                sink = progress.claim_command_progress(total=2)
                # When: the original sender reconnects and continues under the original identity.
                function = (
                    uc.async_send_command_with_retry if asynchronous else uc.send_command_with_retry
                )
                options = {"instance_id": connection.instance_id, "_progress": sink}
                invocation = (
                    function("batch_execute", params, **options)
                    if asynchronous
                    else asyncio.to_thread(function, "batch_execute", params, **options)
                )
                ctx.command = asyncio.create_task(invocation)
                value = await asyncio.wait_for(ctx.command, watchdog_timeout())
            # Then: both settled counts reach the same foreground, without mutation replay.
            assert len(preludes) == 2, "Reload cleanup permanently closed the logical reporter"
            assert [call[0] for call in ctx.calls] == [1, 2]
            assert value["success"]
            assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
            assert commands[1]["params"] == commands[0]["params"]
            assert not sink.offer(progress.CommandProgressUpdate(2, 2, "batch_items"))
        finally:
            connection.disconnect()


@pytest.mark.asyncio
async def test_native_cancel_wait_revokes_business_progress_before_control_reply(monkeypatch):
    # Given: negotiated progress/resume uses distinct held business and cancellation sockets.
    ctx = RecordingContext()
    control_received, send_late = threading.Event(), threading.Event()
    release_control, release_business = threading.Event(), threading.Event()
    processed = asyncio.Event()
    loop = asyncio.get_running_loop()
    claimed = []
    monkeypatch.setattr(progress, "_SEND_INTERVAL", 0)

    def observe(peer, command, _attempt, prelude):
        if command["resume"]["action"] == "cancel":
            assert prelude is None
            control_received.set()
            assert release_control.wait(watchdog_timeout())
        else:
            assert prelude is not None
            _write(peer, _control(prelude["id"]))
            assert ctx.acknowledged.wait(watchdog_timeout())
            assert send_late.wait(watchdog_timeout())
            _write(peer, _control(prelude["id"], completed=2))
            assert release_business.wait(watchdog_timeout())

    with resume_peer("cancel", progress_callback=observe) as (port, _, _, _):
        connection = configure(monkeypatch, port)
        original_offer = connection._offer_stdio_progress

        def observed_offer(sink, command_id, raw):
            original_offer(sink, command_id, raw)
            if json.loads(raw)["completed"] == 2:
                loop.call_soon_threadsafe(processed.set)

        monkeypatch.setattr(connection, "_offer_stdio_progress", observed_offer)

        async def invocation():
            async with progress.foreground_progress(ctx):
                sink = progress.claim_command_progress(total=2)
                claimed.append(sink)
                return await uc.async_send_command_with_retry(
                    "batch_execute",
                    {"commands": [{"tool": "manage_scene", "params": {"action": "save"}}] * 2},
                    instance_id=connection.instance_id,
                    _progress=sink,
                )

        ctx.command = asyncio.create_task(invocation())
        try:
            assert await asyncio.wait_for(ctx.received.get(), watchdog_timeout()) == 1
            # When: cancellation delivery is pending and the original business wire reports again.
            ctx.command.cancel()
            assert await asyncio.to_thread(control_received.wait, watchdog_timeout())
            assert not ctx.command.done()
            assert connection._async_admission(loop).in_use()
            send_late.set()
            await asyncio.wait_for(processed.wait(), watchdog_timeout())
            for _ in range(6):
                await asyncio.sleep(0)
            # Then: reporting authority is revoked before the control reply or foreground exit.
            assert [call[0] for call in ctx.calls] == [1]
            assert not ctx.command.done()
            assert connection._async_admission(loop).in_use()
        finally:
            send_late.set()
            release_business.set()
            release_control.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(ctx.command, watchdog_timeout())
            async with asyncio.timeout(watchdog_timeout()):
                while connection._async_admission(loop).in_use():
                    await asyncio.sleep(0.001)
            connection.disconnect()
        assert not claimed[0].offer(progress.CommandProgressUpdate(2, 2, "batch_items"))


@pytest.mark.parametrize("skew_ms", [-60000, 60000])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_negotiated_editor_clock_survives_tcp_reload(monkeypatch, skew_ms, asynchronous):
    sample = int(time.time() * 1000) + skew_ms - 2000
    with resume_peer(clock_sample=sample) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        monkeypatch.setattr(config, "command_total_timeout", 10.0)
        try:
            if asynchronous:
                value = await uc.async_send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            else:
                value = uc.send_command_with_retry(
                    "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                )
            value = value.model_dump() if hasattr(value, "model_dump") else value
            assert value["success"]
            assert len(commands) == 2
            assert sample + 9000 <= commands[0]["resume"]["deadline_unix_ms"] <= sample + 11000
            assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
        finally:
            connection.disconnect()


@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("applied", [True, False])
@pytest.mark.parametrize("skew_ms", [None, -60000, 60000])
@pytest.mark.asyncio
async def test_deadline_after_queued_proof_uses_latest_wire_attempt(
    monkeypatch, asynchronous, applied, skew_ms
):
    mode = "applied_then_offline" if applied else "queued_then_offline"
    sample = int(time.time() * 1000) + skew_ms if skew_ms is not None else None
    with resume_peer(mode, reloads=0, clock_sample=sample) as (port, commands, mutation, _):
        connection = configure(monkeypatch, port, reconnect_failures=1000)
        monkeypatch.setattr(config, "command_total_timeout", 1.0)
        try:
            args = ("manage_scene", {"action": "save"})
            if asynchronous:
                response = await uc.async_send_command_with_retry(
                    *args, instance_id=connection.instance_id
                )
            else:
                response = uc.send_command_with_retry(*args, instance_id=connection.instance_id)
            assert mutation.is_set() is applied
            if applied:
                assert len(commands) == 2
                assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
                assert commands[1]["params"] == commands[0]["params"]
                assert response.data["reason"] == "outcome_unknown"
                assert response.hint == "inspect_state_before_retry"
            else:
                assert len(commands) == 1
                assert response.data == {
                    "reason": "command_deadline_exceeded",
                    "dispatch": "not_dispatched",
                }
        finally:
            connection.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "business",
    [
        {"message": {"id": 1}},
        {"error": ["detail"]},
        {"data": "raw", "count": 2},
        {"success": True, "message": {"id": 1}},
    ],
)
async def test_completed_custom_result_survives_stdio_delivery(monkeypatch, business):
    from fastmcp import FastMCP
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService

    with resume_peer(reloads=0, business_result=business) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        monkeypatch.setattr(CustomToolService, "_instance", None)
        service = CustomToolService(FastMCP("custom-stdio-result"), project_scoped_tools=True)
        name = "fixture_custom_result"
        service._register_project_tools("deadbeef", [ToolDefinitionModel(name=name)])
        try:
            response = await service.execute_tool("deadbeef", name, connection.instance_id)
            assert response.success
            assert response.data == business
            assert [command["type"] for command in commands] == [name]
        finally:
            connection.disconnect()


@pytest.mark.asyncio
async def test_malformed_tcp_resume_proof_is_unknown_and_retires_connection(monkeypatch):
    with resume_peer("malformed", reloads=0) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        try:
            value = await uc.async_send_command_with_retry(
                "manage_scene", {"action": "save"}, instance_id=connection.instance_id
            )
            value = value.model_dump() if hasattr(value, "model_dump") else value
            assert value["data"]["reason"] == "outcome_unknown"
            assert value["hint"] == "inspect_state_before_retry"
            assert connection.sock is None
            assert len(commands) == 1
        finally:
            connection.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completed", "cold"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_receipt_enabled_tcp_reconnect_keeps_original_invocation(
    monkeypatch, mode, asynchronous
):
    with resume_peer(mode) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        try:
            with prepared_receipt("deadbeef"):
                if asynchronous:
                    value = await uc.async_send_command_with_retry(
                        "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                    )
                else:
                    value = uc.send_command_with_retry(
                        "manage_scene", {"action": "save"}, instance_id=connection.instance_id
                    )
            value = value.model_dump() if hasattr(value, "model_dump") else value
            if mode == "cold":
                assert value["data"]["reason"] == "outcome_unknown"
                assert len(commands) == 1
            else:
                assert value["data"]["applied"] == 1
                assert len(commands) == 2
                assert commands[1]["resume"] == {**commands[0]["resume"], "action": "resume"}
        finally:
            connection.disconnect()


@pytest.mark.asyncio
async def test_origin_extension_reaches_authenticated_tcp_for_read_preflight(monkeypatch):
    from transport.command_resume import queued_origin

    with resume_peer(reloads=0, origin=True) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        token = queued_origin.set("a" * 32)
        try:
            value = await uc.async_send_command_with_retry(
                "get_editor_state", {}, instance_id=connection.instance_id
            )
            assert value["success"]
            assert commands[0]["resume"]["origin_generation"] == "a" * 32
        finally:
            queued_origin.reset(token)
            connection.disconnect()


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
@pytest.mark.parametrize("mode", ["preflight", "completed", "executing"])
async def test_mutation_reload_preflight_preserves_dispatched_invocation(monkeypatch, mode):
    from services.tools import refresh_unity

    with resume_peer(mode) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        reload_hint = {"reason": "reloading"}
        observations = iter([reload_hint, None] if mode == "preflight" else [None, reload_hint])
        monkeypatch.setattr(uc, "read_status_file", lambda *_: next(observations, None))
        ready_at = []

        async def ready(_ctx):
            ready_at.append(len(commands))
            assert len(commands) != 1 or mode == "preflight"
            return True, 0

        monkeypatch.setattr(refresh_unity, "wait_for_editor_ready", ready)
        try:
            response = await refresh_unity._send_mutation(
                None, connection.instance_id, "manage_scene", {"action": "save"}
            )
            assert response["data"] == {"applied": 1, "receipt": "original"}
            assert commands[0]["resume"]["action"] == "new"
            if mode == "preflight":
                assert ready_at == [0, 1]
            else:
                assert ready_at == [2]
                assert commands[1]["params"] == commands[0]["params"]
                assert commands[1]["resume"] == {
                    **commands[0]["resume"],
                    "action": "status" if mode == "executing" else "resume",
                }
        finally:
            connection.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "cancel_after_completion"])
async def test_cancel_uses_separate_authenticated_socket_and_stops_queued_worker(monkeypatch, mode):
    # Given a native queue holding the original authenticated connection's response.
    with resume_peer(mode) as (port, commands, started, canceled):
        connection = configure(monkeypatch, port)
        gate = connection._async_admission(asyncio.get_running_loop())
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
            await asyncio.wait_for(gate.acquire(), watchdog_timeout(5))
            gate.release()
            assert not gate.in_use()
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
