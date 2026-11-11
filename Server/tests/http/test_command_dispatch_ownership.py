"""Concurrent writers retain distinct cancellation and result ownership."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.timing import await_signal, watchdog_timeout
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from tests.transport_socket import JsonSocket


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["caller", "writer"])
async def test_cancelled_dispatch_cannot_release_or_relabel_another_writer(
    command_hub, cancellation
):
    entered = {name: asyncio.Event() for name in ("session", "second")}
    release = {name: asyncio.Event() for name in entered}
    messages = {name: [] for name in entered}
    await command_hub.registry.register("second", "Other", "other-hash", "6000.3")
    sockets = {"session": command_hub.websocket}
    sockets["second"] = JsonSocket(
        **{
            **vars(command_hub.websocket),
            "state": SimpleNamespace(
                **{
                    **vars(command_hub.websocket.state),
                    "plugin_session_id": "second",
                    "plugin_generation": "second-generation",
                }
            ),
        }
    )
    PluginHub._connections["second"] = sockets["second"]

    def sender(session_id):
        async def send(message):
            messages[session_id].append(message)
            if message["type"] != "execute":
                return
            entered[session_id].set()
            await release[session_id].wait()
            if session_id == "session":
                raise asyncio.CancelledError
            await command_hub.endpoint._handle_command_result(
                sockets[session_id],
                CommandResultMessage(id=message["id"], result={"success": True, "data": "second"}),
            )

        return send

    for session_id, socket in sockets.items():
        socket.send_json = sender(session_id)
    tasks = {
        session_id: asyncio.create_task(PluginHub.send_command(session_id, "manage_scene", {}))
        for session_id in sockets
    }
    try:
        for session_id, event in entered.items():
            await await_signal(event, session_id, producers=(tasks[session_id],))
        second_id = messages["second"][0]["id"]
        second_owner = PluginHub._pending[second_id]
        if cancellation == "caller":
            tasks["session"].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks["session"]
        else:
            release["session"].set()
            result = await asyncio.wait_for(tasks["session"], watchdog_timeout())
            assert result["data"]["reason"] == "outcome_unknown"
            assert result["data"]["cause"] == "send_cancelled"
            assert result["data"]["session_id"] == "session"
            assert result["hint"] == "inspect_state_before_retry"
        assert PluginHub._pending == {second_id: second_owner}
        assert not second_owner["future"].done()
        assert not tasks["second"].done()
        assert [frame["type"] for frame in messages["session"]] == ["execute", "cancel"]
        assert [frame["type"] for frame in messages["second"]] == ["execute"]
        release["second"].set()
        assert await asyncio.wait_for(tasks["second"], watchdog_timeout()) == {
            "success": True,
            "data": "second",
        }
        assert PluginHub._pending == {}
    finally:
        for event in release.values():
            event.set()
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
