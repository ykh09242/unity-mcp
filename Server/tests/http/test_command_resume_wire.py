"""Continuation across real WebSocket registration and disconnect boundaries."""

from functools import partial

import pytest

from tests.http.test_plugin_hub_lifecycle import isolated_hub, wire_client  # noqa: F401
from tests.timing import watchdog_timeout
from transport.plugin_hub import PluginHub

EDITOR = "e" * 32


def register(socket, editor=EDITOR):
    socket.receive_json()
    socket.send_json(
        {
            "type": "register",
            "project_hash": "owned",
            "capabilities": ["native_arbitration_v1", "command_resume_v1", "command_cancel_v1"],
            "arbitration_generation": "a" * 32,
            "resume_generation": editor,
        }
    )
    return socket.receive_json()


def respond(socket, command, state, payload=None):
    waiting = state in {"queued", "executing"}
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
                        "disposition": "not_started" if waiting else "started",
                        "retryable": waiting,
                        **({"reason": "operation_busy"} if waiting else {}),
                    },
                    "payload": payload or {"success": True, "data": {"applied": 1}},
                },
            },
        }
    )


@pytest.mark.parametrize("first_state", [None, "queued"])
def test_same_sender_continues_original_invocation_after_reload(wire_client, first_state):
    # Given one original command, possibly acknowledged as still queued.
    with wire_client.websocket_connect("/hub/plugin") as first_socket:
        ack = register(first_socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
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


def test_cold_editor_restart_does_not_receive_old_invocation(wire_client):
    # Given an original invocation whose reply was lost.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        socket.receive_json()
    # When a new Editor process replaces it.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket, "f" * 32)
        result = future.result(watchdog_timeout())
        # Then the original execution stays uncertain and no command is admitted.
        assert result["data"]["reason"] == "outcome_unknown"
        assert not PluginHub._pending


def test_explicit_cancellation_delivers_original_invocation_tombstone(wire_client):
    # Given a queued invocation on a reachable native transport.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
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


def test_job_polling_does_not_consume_mutation_continuation_fences(wire_client):
    # Given a negotiated Editor and more polls than the native journal's capacity.
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        for _ in range(130):
            future = wire_client.portal.start_task_soon(
                partial(
                    PluginHub.send_command, ack["session_id"], "get_test_job", {"job_id": "owned"}
                )
            )
            command = socket.receive_json()
            # When a reviewed job observation uses the native transport.
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
