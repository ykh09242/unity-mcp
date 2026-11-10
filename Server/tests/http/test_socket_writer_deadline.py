"""A stalled writer cannot publish commands after their admitted deadline."""

import asyncio
import json
import time

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette import websockets

from tests.http.test_remote_credential_composition import connected_plugin
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport import plugin_hub as hub_module
from transport.command_budget import _CommandBudget
from transport.command_resume import CAPABILITY as RESUME_CAPABILITY
from transport.plugin_hub import PluginHub


@pytest.mark.asyncio
@pytest.mark.parametrize("resumable", [False, True])
@pytest.mark.parametrize("delay_at", ["schedule", "serialization", "none", "write"])
async def test_actual_socket_writer_deadline(command_hub, monkeypatch, resumable, delay_at):
    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)])
    async with connected_plugin(app, "fixture", "writer-deadline") as peer:
        _, incoming, outgoing, registration = peer
        session_id = registration["session_id"]
        websocket = PluginHub._connections[session_id]
        websocket.state.plugin_features = frozenset({PluginHub.CANCELLATION_CAPABILITY})
        if resumable:
            websocket.state.plugin_features |= {RESUME_CAPABILITY}
            websocket.state.plugin_resume_generation = "e" * 32
        budget = _CommandBudget(asyncio.get_running_loop().time() + 0.5)
        sent = []
        schedule = hub_module.operation_task
        dumps = websockets.json.dumps

        def spend_budget():
            # Block the loop itself: its timeout callback cannot run until the
            # next yield, so a synchronous final fence must reject this write.
            time.sleep(max(0, budget.remaining()) + 0.02)

        def delayed_schedule(coroutine):
            async def delayed():
                spend_budget()
                return await coroutine

            return schedule(delayed())

        def delayed_serialization(value, **kwargs):
            if value.get("type") == "execute":
                spend_budget()
            return dumps(value, **kwargs)

        if delay_at == "schedule":
            monkeypatch.setattr(hub_module, "operation_task", delayed_schedule)
        elif delay_at == "serialization":
            monkeypatch.setattr(websockets.json, "dumps", delayed_serialization)

        async def respond():
            while True:
                frame = await outgoing.get()
                if frame["type"] != "websocket.send":
                    continue
                command = json.loads(frame["text"])
                sent.append(command)
                if command["type"] != "execute" or delay_at == "write":
                    continue
                result = {"success": True}
                if resumable:
                    result = {
                        "command_resume": {
                            "version": 1,
                            "invocation_id": command["resume"]["invocation_id"],
                            "editor_session_id": "e" * 32,
                            "state": "completed",
                        },
                        "payload": result,
                    }
                await incoming.put(
                    {
                        "type": "websocket.receive",
                        "text": dumps(
                            {"type": "command_result", "id": command["id"], "result": result}
                        ),
                    }
                )

        responder = asyncio.create_task(respond())
        try:
            result = await PluginHub.send_command(
                session_id, "manage_scene", {"action": "save"}, _budget=budget
            )
            while not outgoing.empty():
                frame = outgoing.get_nowait()
                if frame["type"] == "websocket.send":
                    sent.append(json.loads(frame["text"]))
            if delay_at in {"schedule", "serialization"}:
                assert sent == []
                assert result["data"]["reason"] == "command_deadline_exceeded"
                assert result["data"]["dispatch"] == "not_dispatched"
                assert not budget.dispatched
            elif delay_at == "write":
                assert result["data"]["reason"] == "outcome_unknown"
                assert result["hint"] == "inspect_state_before_retry"
                assert budget.dispatched
            else:
                assert result["success"] is True
                assert budget.dispatched
            assert [command["type"] for command in sent] == (
                ["execute", "cancel"]
                if delay_at == "write"
                else ["execute"]
                if delay_at == "none"
                else []
            )
            if sent:
                assert ("resume" in sent[0]) is resumable
            assert PluginHub._pending == {}
            assert not PluginHub._retained_results
        finally:
            responder.cancel()
            await asyncio.gather(responder, return_exceptions=True)
