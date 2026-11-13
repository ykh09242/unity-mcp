"""Continuation across real WebSocket registration and disconnect boundaries."""

import asyncio
from concurrent.futures import FIRST_COMPLETED, wait
from functools import partial
import json
import time

import pytest

from tests.http.test_plugin_hub_lifecycle import isolated_hub, wire_client  # noqa: F401
from tests.timing import watchdog_timeout
from transport.command_budget import _CommandBudget
from transport.command_dispatch import CommandNotDispatchedError
from transport.plugin_hub import PluginHub
from tests.receipt_resume_support import prepared_receipt

EDITOR = "e" * 32


async def receipt_dispatch(session_id):
    with prepared_receipt("owned"):
        return await PluginHub.send_command(session_id, "manage_scene", {"action": "save"})


def register(socket, editor=EDITOR, *, origin=False, path=None, clock_sample=None):
    socket.receive_json()
    socket.send_json(
        {
            "type": "register",
            "project_hash": "owned",
            "capabilities": [
                "native_arbitration_v1",
                "command_resume_v1",
                "command_cancel_v1",
                *(["command_resume_origin_v1"] if origin else []),
                *(["command_resume_clock_v1"] if clock_sample is not None else []),
            ],
            **({"resume_clock_unix_ms": clock_sample} if clock_sample is not None else {}),
            **({"project_path": path} if path else {}),
            "arbitration_generation": "a" * 32,
            "resume_generation": editor,
        }
    )
    return socket.receive_json()


@pytest.mark.parametrize("skew_ms", [-60000, 60000])
def test_negotiated_editor_clock_survives_reconnect_without_extending_deadline(
    wire_client, monkeypatch, skew_ms
):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 10.0)
    # A sample can spend time in transit before registration arrives.
    sample = int(time.time() * 1000) + skew_ms - 2000
    with wire_client.websocket_connect("/hub/plugin") as first_socket:
        ack = register(first_socket, clock_sample=sample)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = first_socket.receive_json()
        respond(first_socket, first, "queued")
        first = first_socket.receive_json()
    with wire_client.websocket_connect("/hub/plugin") as second_socket:
        register(second_socket, clock_sample=sample + 5000)
        resumed = second_socket.receive_json()
        respond(second_socket, resumed, "completed")
        assert future.result(watchdog_timeout())["success"]
        assert "command_resume_clock_v1" in ack["capabilities"]
        assert sample + 9000 <= first["resume"]["deadline_unix_ms"] <= sample + 11000
        assert resumed["resume"] == {**first["resume"], "action": "resume"}


@pytest.mark.parametrize("sample", [None, True, 0, -1, "1000", 1000.0, 9223372036854775808])
def test_clock_capability_requires_a_valid_sample_before_registration(wire_client, sample):
    from starlette.websockets import WebSocketDisconnect

    with wire_client.websocket_connect("/hub/plugin") as socket:
        socket.receive_json()
        socket.send_json(
            {
                "type": "register",
                "project_hash": "owned",
                "capabilities": [
                    "native_arbitration_v1",
                    "command_resume_v1",
                    "command_resume_clock_v1",
                ],
                "arbitration_generation": "a" * 32,
                "resume_generation": EDITOR,
                **({"resume_clock_unix_ms": sample} if sample is not None else {}),
            }
        )
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4400
        assert not PluginHub._connections


@pytest.mark.parametrize("skew_ms", [-60000, 60000])
def test_server_monotonic_timeout_remains_authoritative_with_clock_translation(
    wire_client, monkeypatch, skew_ms
):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 1.0)
    sample = int(time.time() * 1000) + skew_ms
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket, clock_sample=sample)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        command = socket.receive_json()
        # A missing reply cannot gain 60 seconds from the Editor clock or cause replay.
        result = future.result(watchdog_timeout(3))
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["hint"] == "inspect_state_before_retry"
        assert sample + 500 <= command["resume"]["deadline_unix_ms"] <= sample + 1500


def respond(socket, command, state, payload=None, *, refusal_reason=None):
    waiting = state in {"queued", "executing"}
    reason = "operation_busy" if waiting else refusal_reason
    socket.send_json(
        {
            "type": "command_result",
            "id": command["id"],
            "result": {
                "command_resume": {
                    "version": 1,
                    "invocation_id": command["resume"]["invocation_id"],
                    "editor_session_id": command["resume"]["editor_session_id"],
                    "state": state,
                },
                "payload": {
                    "native_arbitration": {
                        **command["arbitration"],
                        "disposition": "not_started" if reason else "started",
                        "retryable": waiting,
                        **({"reason": reason} if reason else {}),
                    },
                    "payload": payload or {"success": True, "data": {"applied": 1}},
                },
            },
        }
    )


@pytest.mark.parametrize("replacement_editor", [EDITOR, "f" * 32])
def test_failed_asgi_write_can_only_recover_the_original_invocation(
    wire_client, isolated_hub, replacement_editor, monkeypatch
):
    writes = []
    # Fail the actual ASGI write after the dispatch guard, without delivering a
    # separate disconnect to the receive loop.
    endpoint_init = PluginHub.__init__

    def install_endpoint_fault(endpoint, scope, receive, send):
        async def fail_first_execute(frame):
            if frame["type"] == "websocket.send":
                command = json.loads(frame["text"])
                if command["type"] == "execute":
                    writes.append(command)
                    if len(writes) == 1:
                        raise OSError("connection lost while sending execute")
            await send(frame)

        endpoint_init(endpoint, scope, receive, fail_first_execute)

    monkeypatch.setattr(PluginHub, "__init__", install_endpoint_fault)
    with wire_client.websocket_connect("/hub/plugin") as first_socket:
        session_id = register(first_socket)["session_id"]
        changed = isolated_hub.change_event
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, session_id, "manage_scene", {"action": "save"})
        )
        evicted = wire_client.portal.start_task_soon(changed.wait)
        try:
            done, _ = wait(
                [evicted, future], timeout=watchdog_timeout(), return_when=FIRST_COMPLETED
            )
            assert evicted in done
        finally:
            evicted.cancel()
        assert session_id not in PluginHub._connections
        assert len(writes) == 1
        original = writes[0]
        assert original["resume"]["action"] == "new"

        with wire_client.websocket_connect("/hub/plugin") as second_socket:
            replacement = register(second_socket, replacement_editor)["session_id"]
            if replacement_editor == EDITOR:
                resumed = second_socket.receive_json()
                assert writes == [original, resumed]
                assert resumed["id"] != original["id"]
                assert resumed["params"] == original["params"]
                assert resumed["resume"] == {**original["resume"], "action": "resume"}
                respond(second_socket, resumed, "completed")
                assert future.result(watchdog_timeout())["data"]["applied"] == 1
            else:
                result = future.result(watchdog_timeout())
                assert result["data"]["reason"] == "outcome_unknown"
                assert result["data"]["command_id"] == original["id"]
                assert writes == [original]
            assert replacement in PluginHub._connections
            assert not PluginHub._pending


def test_already_closed_websocket_has_no_dispatch_evidence(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        session_id = register(socket)["session_id"]
        old = PluginHub._connections[session_id]
        wire_client.portal.call(old.close)

        async def dispatch_while_close_is_pending():
            budget = _CommandBudget(asyncio.get_running_loop().time() + watchdog_timeout())
            with pytest.raises(CommandNotDispatchedError):
                await PluginHub.send_command(
                    session_id, "manage_scene", {"action": "save"}, _budget=budget
                )
            assert not budget.dispatched
            assert budget.command_id is None

        wire_client.portal.call(dispatch_while_close_is_pending)
        assert session_id not in PluginHub._connections
        assert not PluginHub._pending


@pytest.mark.parametrize("first_state", [None, "queued"])
@pytest.mark.parametrize("receipted", [False, True])
def test_same_sender_continues_original_invocation_after_reload(
    wire_client, first_state, receipted
):
    # Given one original command, possibly acknowledged as still queued.
    with wire_client.websocket_connect("/hub/plugin") as first_socket:
        ack = register(first_socket)
        future = wire_client.portal.start_task_soon(
            partial(receipt_dispatch, ack["session_id"])
            if receipted
            else partial(
                PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"}
            )
        )
        first = first_socket.receive_json()
        if first_state is not None:
            respond(first_socket, first, first_state)
            first = first_socket.receive_json()
    # When the same Editor process registers after domain reload.
    with wire_client.websocket_connect("/hub/plugin") as second_socket:
        register(second_socket)
        resumed = second_socket.receive_json()
        result = {"success": True, "data": {"applied": 1, "receipt": "original"}}
        respond(second_socket, resumed, "completed", result)
        # Then only the wire attempt changes; identity, arguments and deadline survive.
        assert resumed["params"] == first["params"]
        assert resumed["resume"] == {**first["resume"], "action": "resume"}
        assert resumed["id"] != first["id"]
        assert resumed["arbitration"]["request_nonce"] != first["arbitration"]["request_nonce"]
        assert future.result(watchdog_timeout()) == result
        assert not PluginHub._pending


def test_live_executing_invocation_is_observed_without_readmission(wire_client):
    # Given a live native invocation whose handler already owns execution.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = socket.receive_json()
        respond(socket, first, "executing")
        # When the original sender checks its completion.
        observed = socket.receive_json()
        respond(socket, observed, "completed")
        # Then the observation cannot request another execution.
        assert observed["resume"] == {**first["resume"], "action": "status"}
        assert future.result(watchdog_timeout())["data"]["applied"] == 1


@pytest.mark.parametrize("receipted", [False, True])
def test_cold_editor_restart_does_not_receive_old_invocation(wire_client, receipted):
    # Given an original invocation whose reply was lost.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(receipt_dispatch, ack["session_id"])
            if receipted
            else partial(
                PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"}
            )
        )
        socket.receive_json()
    # When a new Editor process replaces it.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket, "f" * 32)
        result = future.result(watchdog_timeout())
        # Then the original execution stays uncertain and no command is admitted.
        assert result["data"]["reason"] == "outcome_unknown"
        assert not PluginHub._pending


@pytest.mark.parametrize("skew_ms", [None, -60000, 60000])
def test_explicit_cancellation_delivers_original_invocation_tombstone(wire_client, skew_ms):
    # Given a queued invocation on a reachable native transport.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        sample = int(time.time() * 1000) + skew_ms if skew_ms is not None else None
        ack = register(socket, clock_sample=sample)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = socket.receive_json()
        # When the caller explicitly cancels.
        future.cancel()
        canceled = socket.receive_json()
        # Then cancellation addresses the stable invocation rather than reload waiting.
        assert canceled["type"] == "cancel"
        assert canceled["params"] == first["params"]
        assert canceled["resume"] == {**first["resume"], "action": "cancel"}


def test_consecutive_websocket_reloads_keep_the_original_invocation(wire_client):
    # Given an original invocation interrupted by consecutive native reloads.
    commands = []
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        commands.append(socket.receive_json())
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket)
        commands.append(socket.receive_json())
    # When the same sender finally observes completion after the second reload.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket)
        commands.append(socket.receive_json())
        respond(socket, commands[-1], "completed")
        # Then neither reload creates another logical command.
        assert future.result(watchdog_timeout())["data"]["applied"] == 1
        assert (
            commands[1]["resume"]
            == commands[2]["resume"]
            == {**commands[0]["resume"], "action": "resume"}
        )


def test_malformed_resume_envelope_finishes_without_another_attempt(wire_client):
    # Given a negotiated command with no valid continuation receipt.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = socket.receive_json()
        # When the peer replies with a handler-shaped, unbound resume proof.
        socket.send_json(
            {
                "type": "command_result",
                "id": first["id"],
                "result": {"command_resume": {"state": "queued"}, "payload": {}},
            }
        )
        result = future.result(watchdog_timeout())
        # Then malformed metadata cannot cause a dead wait or readmission.
        assert result["data"]["reason"] == "outcome_unknown"
        assert not PluginHub._pending


def test_receipted_reconnect_rejects_changed_project_root(wire_client, tmp_path):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket, path=str(tmp_path / "Original"))
        future = wire_client.portal.start_task_soon(partial(receipt_dispatch, ack["session_id"]))
        socket.receive_json()
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket, path=str(tmp_path / "Replacement"))
        assert future.result(watchdog_timeout())["data"]["reason"] == "outcome_unknown"
        assert not PluginHub._pending


@pytest.mark.parametrize("supported", [False, True])
def test_origin_extension_requires_negotiation_and_covers_preflight_reads(wire_client, supported):
    from transport.command_resume import CommandResumeProtocolError, queued_origin

    async def dispatch_origin(session_id):
        token = queued_origin.set("c" * 32)
        try:
            return await PluginHub.send_command(session_id, "get_editor_state", {})
        finally:
            queued_origin.reset(token)

    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket, origin=supported)
        assert ("command_resume_origin_v1" in ack.get("capabilities", [])) is supported
        future = wire_client.portal.start_task_soon(partial(dispatch_origin, ack["session_id"]))
        if supported:
            command = socket.receive_json()
            assert command["resume"]["origin_generation"] == "c" * 32
            respond(socket, command, "completed")
            assert future.result(watchdog_timeout())["success"]
        else:
            with pytest.raises(CommandResumeProtocolError):
                future.result(watchdog_timeout())
        assert not PluginHub._pending


def test_journal_capacity_is_returned_before_dispatch_without_replaying(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        command = socket.receive_json()
        respond(
            socket,
            command,
            "unavailable",
            {"code": "command_resume_capacity", "error": "Journal full"},
            refusal_reason="command_resume_capacity",
        )
        result = future.result(watchdog_timeout())
        assert result["success"] is False
        assert result["code"] == "command_resume_capacity"
        assert result["data"]["execution"] == "not_started"
        assert not PluginHub._pending


def test_observation_bursts_leave_mutation_continuation_fences_available(wire_client):
    scope = {"expected_instance_id": EDITOR, "expected_project_id": "owned"}
    reads = [("get_test_job", {"job_id": "owned"})] * 130
    reads.extend(
        (
            "manage_reference_graph",
            {
                "action": "inventory",
                "page_size": 256,
                "offset": page * 256,
                **scope,
                **({"snapshot": "owned-inventory"} if page else {}),
            },
        )
        for page in range(130)
    )
    reads.extend(
        [
            ("manage_reference_graph", {"action": "status"}),
            ("unity_reflect", {"action": "get_type", "class_name": "UnityEngine.Vector3"}),
        ]
    )
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        for tool, params in reads:
            future = wire_client.portal.start_task_soon(
                partial(PluginHub.send_command, ack["session_id"], tool, params)
            )
            command = socket.receive_json()
            assert "resume" not in command
            socket.send_json(
                {
                    "type": "command_result",
                    "id": command["id"],
                    "result": {
                        "native_arbitration": {
                            **command["arbitration"],
                            "disposition": "started",
                            "retryable": False,
                        },
                        "payload": {"success": True, "data": {"status": "running"}},
                    },
                }
            )
            assert future.result(watchdog_timeout())["data"]["status"] == "running"
        # Then mutations still receive their own stable continuation identity.
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        command = socket.receive_json()
        assert command["resume"]["action"] == "new"
        respond(socket, command, "completed")
        assert future.result(watchdog_timeout())["success"]
