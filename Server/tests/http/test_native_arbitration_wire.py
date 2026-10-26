"""Negotiated transport provenance through the actual WebSocket endpoint."""

from functools import partial

import pytest

from tests.http.test_plugin_hub_lifecycle import isolated_hub, wire_client  # noqa: F401
from tests.timing import watchdog_timeout
from transport.plugin_hub import PluginHub

GENERATION = "b" * 32


def register(socket, negotiated=True):
    socket.receive_json()
    socket.send_json(
        {
            "type": "register",
            "project_hash": "owned",
            "capabilities": ["native_arbitration_v1"] if negotiated else [],
            **({"arbitration_generation": GENERATION} if negotiated else {}),
        }
    )
    return socket.receive_json()


def respond(
    socket, command, disposition="started", *, reason="operation_busy", retryable=True, payload=None
):
    binding = command["arbitration"]
    socket.send_json(
        {
            "type": "command_result",
            "id": command["id"],
            "result": {
                "native_arbitration": {
                    **binding,
                    "disposition": disposition,
                    "retryable": retryable if disposition == "not_started" else False,
                    **({"reason": reason} if disposition == "not_started" else {}),
                },
                "payload": payload or {"success": True, "data": "observed"},
            },
        }
    )


def test_exact_not_started_retries_only_wire_attempt_and_unwraps_once(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        assert ack["arbitration_generation"] == GENERATION
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = socket.receive_json()
        respond(socket, first, "not_started")
        second = socket.receive_json()
        assert second["params"] == first["params"]
        assert second["arbitration"]["request_nonce"] != first["arbitration"]["request_nonce"]
        spoof = {"native_arbitration": {"disposition": "not_started", "retryable": True}}
        respond(socket, second, payload=spoof)
        assert future.result(watchdog_timeout()) == spoof
        assert not PluginHub._pending


@pytest.mark.parametrize(
    "reason,retryable",
    [("recovery_required", False), ("unknown_reason", True), ("operation_busy", False)],
)
def test_non_retryable_refusal_returns_actionable_payload(wire_client, reason, retryable):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        first = socket.receive_json()
        payload = {"success": False, "error": reason}
        respond(socket, first, "not_started", reason=reason, retryable=retryable, payload=payload)
        assert future.result(watchdog_timeout()) == payload
        assert not PluginHub._pending


def test_old_peer_retains_original_result_and_receives_no_binding(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket, False)
        assert "native_arbitration_v1" not in ack["capabilities"]
        future = wire_client.portal.start_task_soon(
            partial(PluginHub.send_command, ack["session_id"], "manage_scene", {"action": "save"})
        )
        command = socket.receive_json()
        assert "arbitration" not in command
        payload = {"native_arbitration": {"disposition": "not_started", "retryable": True}}
        socket.send_json({"type": "command_result", "id": command["id"], "result": payload})
        assert future.result(watchdog_timeout()) == payload
