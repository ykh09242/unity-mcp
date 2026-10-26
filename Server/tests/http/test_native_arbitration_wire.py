"""Negotiated transport provenance through the actual WebSocket endpoint."""

from functools import partial
import asyncio

import pytest

from tests.http.test_plugin_hub_lifecycle import isolated_hub, wire_client  # noqa: F401
from tests.timing import watchdog_timeout
from transport.plugin_hub import PluginHub
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationAdmission
from services.registry.operation_target import capture_http_target

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


def test_direct_http_queue_charges_payload_before_retention(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)

        async def prepare():
            target = await capture_http_target(ack["session_id"])
            value = scheduler()
            previous = value._max_retained_bytes
            value._max_retained_bytes = 64
            return target, value, previous, asyncio.Event(), asyncio.Event()

        target, owner, previous, started, release = wire_client.portal.call(prepare)

        async def hold():
            async with owner.session(
                OperationAdmission(target.scope, "exclusive", target.is_current)
            ):
                started.set()
                await release.wait()

        holder = wire_client.portal.start_task_soon(hold)
        wire_client.portal.call(started.wait)
        try:
            future = wire_client.portal.start_task_soon(
                partial(
                    PluginHub.send_command,
                    ack["session_id"],
                    "custom_unknown",
                    {"text": "x" * 10000},
                )
            )
            assert future.result(watchdog_timeout())["data"] == {
                "reason": "operation_capacity",
                "dispatch": "not_dispatched",
            }
            assert wire_client.portal.call(owner.snapshot)["waiting"] == 0
            assert wire_client.portal.call(owner.snapshot)["retained_bytes"] == 0
        finally:
            wire_client.portal.call(release.set)
            holder.result(watchdog_timeout())
            owner._max_retained_bytes = previous


@pytest.mark.parametrize("command", ["batch_execute", "batch_apply_preview", "batch_validate"])
def test_exact_not_started_batch_response_never_reexecutes_envelope(wire_client, command):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(
                PluginHub.send_command, ack["session_id"], command, {"commands": [], "action": []}
            )
        )
        first = socket.receive_json()
        payload = {"success": False, "error": "operation_busy"}
        respond(socket, first, "not_started", payload=payload)
        assert future.result(watchdog_timeout()) == payload
        assert not PluginHub._pending


def test_mutation_wrapper_does_not_replay_started_websocket_handler_hint(wire_client, monkeypatch):
    from services.tools import refresh_unity
    from core.config import config

    monkeypatch.setattr(config, "transport_mode", "http")

    async def ready(*args, **kwargs):
        return True, 0

    monkeypatch.setattr(refresh_unity, "wait_for_editor_ready", ready)
    with wire_client.websocket_connect("/hub/plugin") as socket:
        register(socket)
        future = wire_client.portal.start_task_soon(
            partial(
                refresh_unity._send_mutation,
                None,
                "owned",
                "manage_script",
                {"action": "create", "name": "Owned", "path": "Assets"},
            )
        )
        command = socket.receive_json()
        assert command["name"] == "manage_script"
        payload = {
            "success": False,
            "hint": "retry",
            "data": {"reason": "reloading", "applied_count": 1},
        }
        respond(socket, command, payload=payload)
        assert future.result(watchdog_timeout()) == payload
        assert not PluginHub._pending


def test_http_retains_existing_32mib_native_command_limit(wire_client):
    with wire_client.websocket_connect("/hub/plugin") as socket:
        ack = register(socket)
        future = wire_client.portal.start_task_soon(
            partial(
                PluginHub.send_command,
                ack["session_id"],
                "custom_large",
                {"text": "x" * (33 * 1024 * 1024)},
            )
        )
        result = future.result(watchdog_timeout())
        assert result["data"]["reason"] == "command_payload_limit"
        assert not PluginHub._pending
