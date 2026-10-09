"""Only commands that consume a connection read snapshot should capture one."""

import asyncio
from types import SimpleNamespace
from typing import ClassVar

import pytest
import pytest_asyncio
from core.config import config
from starlette.websockets import WebSocketState
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def command_hub(monkeypatch):
    class OwnedHub(PluginHub):
        _connections: ClassVar[dict] = {}
        _pending: ClassVar[dict] = {}
        _ping_tasks: ClassVar[dict] = {}
        _last_pong: ClassVar[dict] = {}
        _admitted: ClassVar[dict] = {}
        _registry = None
        _retained_results = ChargeLedger()
        _raw_results = ChargeLedger()
        identity_calls = 0

        @classmethod
        async def _read_identity(cls, session_id, *, authoritative=False):
            cls.identity_calls += 1
            return await super()._read_identity(session_id, authoritative=authoritative)

    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    OwnedHub.configure(registry)
    endpoint = OwnedHub.__new__(OwnedHub)
    counts = {"session": 0, "hash": 0, "list": 0}
    original_session = registry.get_session
    original_hash = registry.get_session_id_by_hash
    original_list = registry.list_sessions

    async def session(session_id):
        counts["session"] += 1
        return await original_session(session_id)

    async def by_hash(project_hash, user_id=None):
        counts["hash"] += 1
        return await original_hash(project_hash, user_id)

    async def listing(user_id=None):
        counts["list"] += 1
        return await original_list(user_id)

    monkeypatch.setattr(registry, "get_session", session)
    monkeypatch.setattr(registry, "get_session_id_by_hash", by_hash)
    monkeypatch.setattr(registry, "list_sessions", listing)
    commands = []

    async def send_json(message):
        commands.append(message["name"])
        result = {"success": True, "data": {"command": message["name"]}}
        await endpoint._handle_command_result(
            websocket, CommandResultMessage(id=message["id"], result=result)
        )

    websocket = SimpleNamespace(
        state=SimpleNamespace(
            plugin_generation="generation",
            plugin_registered=True,
            plugin_session_id="session",
            user_id=None,
            plugin_command_epoch=0,
            plugin_state_read_epoch=0,
        ),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
        send_json=send_json,
    )
    OwnedHub._connections["session"] = websocket
    yield SimpleNamespace(
        hub=OwnedHub, registry=registry, websocket=websocket, counts=counts, commands=commands
    )
    for entry in OwnedHub._pending.values():
        entry["future"].cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("retry_on_reload", [False, True])
async def test_non_fast_commands_keep_admission_without_unused_snapshot(
    command_hub, monkeypatch, hosted, retry_on_reload
):
    wire = command_hub
    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    user = "alice" if hosted else None
    await wire.registry.register("session", "Fixture", "owned-hash", "test", user_id=user)
    wire.websocket.state.user_id = user
    for command in (
        "manage_camera",
        "manage_graphics",
        "manage_asset",
        "manage_scene",
        "batch_execute",
        "execute_custom_tool",
    ):
        before = wire.counts.copy()
        result = await wire.hub.send_command_for_instance(
            "Fixture@owned-hash", command, {}, user_id=user, retry_on_reload=retry_on_reload
        )
        assert result == {"success": True, "data": {"command": command}}
        assert wire.counts["session"] - before["session"] == 1
        assert wire.counts["hash"] - before["hash"] == 1
        assert wire.counts["list"] == 0
        assert not wire.hub._pending and not wire.hub._retained_results
    assert wire.hub.identity_calls == 0
    assert len(wire.commands) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["ping", "read_console", "get_editor_state"])
async def test_fast_commands_still_capture_identity_with_reload_retry_disabled(
    command_hub, command
):
    wire = command_hub
    await wire.registry.register("session", "Fixture", "owned-hash", "test")
    result = await wire.hub.send_command_for_instance(
        "owned-hash", command, {}, retry_on_reload=False
    )
    assert result["success"]
    assert wire.hub.identity_calls == 1
    assert wire.counts == {"session": 2, "hash": 1, "list": 0}
    assert wire.websocket.state.plugin_state_read_epoch == (
        1 if command == "get_editor_state" else 0
    )
    assert wire.commands == [command]
