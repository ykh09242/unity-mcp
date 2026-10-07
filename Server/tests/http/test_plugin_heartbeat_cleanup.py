"""Heartbeat failure owns cleanup before asynchronous ASGI close completes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r"""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
Path.home = classmethod(lambda cls: Path(os.environ["HOME"]))
def denied(*args, **kwargs):
    raise AssertionError("Application network prohibited")
original_connect, original_pair = socket.socket.connect, socket.socketpair
def internal_pair(*args, **kwargs):
    socket.socket.connect = original_connect
    try:
        return original_pair(*args, **kwargs)
    finally:
        socket.socket.connect = denied
socket.socketpair = internal_pair
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
import transport.plugin_hub as module
from transport.plugin_registry import PluginRegistry
Hub = module.PluginHub

class Channel:
    def __init__(self, behavior):
        self.behavior = behavior
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.incoming.put_nowait({"type": "websocket.connect"})
        self.command_started = asyncio.Event()
        self.ping_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()
        self.ping_stopped = asyncio.Event()
        self.close_codes = []
        self.command_id = None
        scope = {"type": "websocket", "path": "/hub/plugin", "headers": []}
        self.endpoint = Hub(scope, self.next_message, self.send)
        self.task = asyncio.create_task(self.endpoint.dispatch())

    def receive(self, payload):
        self.incoming.put_nowait({"type": "websocket.receive", "text": json.dumps(payload)})

    async def next_message(self):
        message = await self.incoming.get()
        return message

    async def send(self, message):
        if message["type"] == "websocket.send":
            payload = json.loads(message["text"])
            if payload["type"] == "ping":
                await self.command_started.wait()
                self.ping_started.set()
                if self.behavior == "blocked_ping":
                    try:
                        await self.release.wait()
                    finally:
                        self.ping_stopped.set()
                elif self.behavior == "failed_ping":
                    raise OSError("Owned ASGI write failure")
                elif self.behavior != "holding_reply":
                    self.receive({"type": "pong", "session_id": self.session_id})
            elif payload["type"] == "execute":
                self.command_id = payload["id"]
                self.command_started.set()
                if self.behavior == "healthy":
                    self.receive({"type": "command_result", "id": payload["id"],
                        "result": {"success": True, "data": {"owned_reply": True}}})
            await self.outgoing.put(payload)
        elif message["type"] == "websocket.close":
            self.close_codes.append(message["code"])
            self.close_started.set()
            await self.release.wait()

    async def register(self):
        assert (await asyncio.wait_for(self.outgoing.get(), 1))["type"] == "welcome"
        self.receive({"type": "register", "project_name": "Owned", "project_hash": "ownedhash", "unity_version": "fixture"})
        ack = await asyncio.wait_for(self.outgoing.get(), 1)
        assert ack["type"] == "registered"
        self.session_id = ack["session_id"]
        self.websocket = Hub._connections[self.session_id]
        self.ping_task = Hub._ping_tasks[self.session_id]

    async def stop(self):
        self.release.set()
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(self.task, 1)

async def main():
    case = sys.argv[1]
    registry = PluginRegistry()
    module.config.http_remote_hosted = False
    Hub.configure(registry)
    Hub.PING_INTERVAL, Hub.PING_TIMEOUT, Hub.CLOSE_TIMEOUT, Hub.COMMAND_TIMEOUT = .05, .1, .03, 2
    if case == "replacement":
        Hub.CLOSE_TIMEOUT = 2
    now = [100.0]
    module.time = SimpleNamespace(monotonic=lambda: now[0])
    behavior = "blocked_ping" if case == "disconnect_during_ping" else case if case in ("blocked_ping", "failed_ping", "healthy") else "stale_close"
    owner = Channel(behavior)
    channels, commands = [owner], []
    try:
        # Given a registered real endpoint and a public command awaiting its reply.
        await owner.register()
        command = asyncio.create_task(Hub.send_command_for_instance("Owned@ownedhash", "owned_query", {}, retry_on_reload=False))
        commands.append(command)
        await asyncio.wait_for(owner.command_started.wait(), 1)
        if case == "healthy":
            # When heartbeats and command results arrive on their owner socket.
            assert await asyncio.wait_for(command, 1) == {"success": True, "data": {"owned_reply": True}}
            await asyncio.wait_for(owner.ping_started.wait(), 1)
            owner.receive({"type": "pong", "session_id": "foreign-session"})
            await asyncio.sleep(.05)
            # Then the live session persists and foreign pong IDs allocate no state.
            assert set(Hub._connections) == set(Hub._last_pong) == {owner.session_id}
            assert (await Hub.get_sessions()).sessions.keys() == {owner.session_id}
            assert not Hub._pending and not owner.ping_task.done()
        else:
            # When its heartbeat goes stale, fails, or remains backpressured.
            if behavior == "stale_close":
                now[0] += .2
                await asyncio.wait_for(owner.close_started.wait(), 1)
            else:
                await asyncio.wait_for(owner.ping_started.wait(), 1)
            if case == "cancelled_close":
                owner.ping_task.cancel()
            elif case == "disconnect_during_ping":
                owner.incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})
                await asyncio.wait_for(owner.task, 1)
            if case == "replacement":
                await asyncio.wait({command}, timeout=.2)
            else:
                await asyncio.sleep(.3)
            # Then cleanup precedes blocked close and releases the existing command.
            assert command.done(), "Heartbeat failure must release pending public commands"
            assert command.result()["success"] is False and command.result()["hint"] == "retry"
            assert not (await Hub.get_sessions()).sessions
            assert Hub._connections == Hub._pending == Hub._ping_tasks == Hub._last_pong == {}
            if case != "replacement":
                assert owner.ping_task.done(), "Self-owned eviction must finish its bounded close"
            if behavior == "blocked_ping":
                assert owner.ping_stopped.is_set()
            if case == "replacement":
                # Given a same-project replacement while old bounded close is blocked.
                assert not owner.ping_task.done()
                replacement = Channel("holding_reply")
                channels.append(replacement)
                await replacement.register()
                now[0] = 100.2
                new_command = asyncio.create_task(Hub.send_command_for_instance("ownedhash", "owned_query", {}, retry_on_reload=False))
                commands.append(new_command)
                await asyncio.wait_for(replacement.command_started.wait(), 1)
                # When already-buffered old-socket messages reach the handler.
                # Server close has changed the socket application state, so the
                # dispatcher need not drain a queued test barrier after close.
                # Await the actual handlers to prove stale sender identity checks
                # still protect the replacement even if a callback was buffered.
                before = Hub._last_pong[replacement.session_id]
                now[0] += .01
                await owner.endpoint.on_receive(owner.websocket, {"type": "pong", "session_id": replacement.session_id})
                await owner.endpoint.on_receive(owner.websocket, {"type": "command_result", "id": replacement.command_id,
                                                                 "result": {"wrong_owner": True}})
                assert not new_command.done() and Hub._last_pong[replacement.session_id] == before
                replacement.receive({"type": "pong", "session_id": replacement.session_id})
                replacement.receive({"type": "command_result", "id": replacement.command_id, "result": {"success": True}})
                # Then only the actual replacement completes the reply, and it survives.
                assert await asyncio.wait_for(new_command, 1) == {"success": True}
                assert Hub._last_pong[replacement.session_id] == now[0]
                owner.release.set()
                await asyncio.wait_for(owner.ping_task, 1)
                assert owner.ping_task.done()
                assert set(Hub._connections) == {replacement.session_id}
                assert (await Hub.get_sessions()).sessions.keys() == {replacement.session_id}
        assert all(code == 1001 for channel in channels for code in channel.close_codes)
        if case in ("stale_close", "blocked_ping", "cancelled_close", "replacement"):
            assert owner.close_codes == [1001]
        print(json.dumps({"case": case, "passed": True}))
    finally:
        for command in commands:
            command.cancel()
        await asyncio.gather(*commands, return_exceptions=True)
        for channel in channels:
            await channel.stop()
        await Hub.shutdown()
asyncio.run(main())
"""


@pytest.mark.parametrize(
    "case",
    [
        "stale_close",
        "failed_ping",
        "blocked_ping",
        "cancelled_close",
        "replacement",
        "healthy",
        "disconnect_during_ping",
    ],
)
def test_heartbeat_owns_session_and_command_cleanup(tmp_path, case):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
        "UNITY_MCP_LOG_DIR",
        "UNITY_MCP_STATUS_DIR",
        "TEMP",
        "TMP",
    ):
        env[key] = str(tmp_path)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM, case],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
