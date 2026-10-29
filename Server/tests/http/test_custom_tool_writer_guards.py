"""Final custom-tool admission spans writer scheduling and receipt I/O."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute

from core.config import config
from models.models import ToolDefinitionModel, ToolParameterModel
from services.api_key_service import ValidationResult
from services.credential_lease import bind_credential_lease
from services.custom_tool_service import CustomToolService
from services.execution_receipt_context import execution_owner
from tests.http.test_remote_credential_composition import connected_plugin, remote_hub_credentials
from tests.http.test_remote_credential_leases import credential_service
from tests.timing import await_signal, watchdog_timeout
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport import plugin_hub as hub_module
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.models import CommandResultMessage
from transport.remote_auth_middleware import RemoteControlAuthMiddleware


class ReceiptBoundary:
    """Model only yielding receipt persistence and actual writer-pin ownership."""

    def __init__(self, entered, release, *, pause):
        self.expires_at = asyncio.get_running_loop().time() + 30
        self.entered, self.release, self.pause = entered, release, pause
        self.pins = 0
        self.dispatches = []
        self.observations = []
        self.unknown = False

    def acquire_worker(self):
        self.pins += 1

        def close():
            self.pins -= 1

        return SimpleNamespace(close=close)

    async def native_async(self, _name, _params, **_values):
        if self.pause:
            self.entered.set()
            await self.release.wait()

    def dispatch_started(self, **values):
        self.dispatches.append(values)

    async def observe_async(self, **values):
        self.observations.append(values)

    def mark_unknown(self):
        self.unknown = True


@pytest.mark.asyncio
@pytest.mark.parametrize("later_mutation", [False, True])
async def test_final_read_guard_preserves_own_mutation_and_structured_refusal(
    command_hub, monkeypatch, later_mutation
):
    identity = await PluginHub._read_identity("session")
    assert identity is not None
    entered, release = asyncio.Event(), asyncio.Event()
    schedule = hub_module.operation_task
    first = True

    def defer_first(coroutine):
        nonlocal first
        if not first:
            return schedule(coroutine)
        first = False

        async def deferred():
            try:
                entered.set()
                await release.wait()
                return await coroutine
            finally:
                coroutine.close()

        return schedule(deferred())

    monkeypatch.setattr(hub_module, "operation_task", defer_first)
    call = asyncio.create_task(
        PluginHub.send_command(
            "session", "read_console", {"action": "clear"}, _expected_read=identity
        )
    )
    try:
        await await_signal(entered, "console clear writer", producers=(call,))
        if later_mutation:
            command_hub.websocket.state.plugin_command_epoch += 1
        release.set()
        result = await asyncio.wait_for(call, watchdog_timeout())
        if later_mutation:
            assert result["data"]["reason"] == "read_invalidated"
            assert result["hint"] == "retry"
        else:
            assert result["success"] is True
        assert [message["name"] for message in command_hub.sent] == (
            [] if later_mutation else ["read_console"]
        )
        assert PluginHub._pending == {}
        assert not PluginHub._retained_results
    finally:
        release.set()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at", ["writer", "receipted_writer", "receipt_io"])
@pytest.mark.parametrize("change", ["replace", "withdraw", "unchanged", "socket", "generation"])
async def test_custom_tool_rechecks_target_and_catalog_at_socket_io(
    command_hub, monkeypatch, pause_at, change
):
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(FastMCP("writer-catalog-guard"))
    original_tool = ToolDefinitionModel(
        name="fixture_mutate",
        parameters=[ToolParameterModel(name="value", type="int", required=True)],
    )
    service.replace_project_catalog(
        "hash", [original_tool], kind="http", generation="session", publish_global=False
    )
    entered, release = asyncio.Event(), asyncio.Event()
    receipt = (
        ReceiptBoundary(entered, release, pause=pause_at == "receipt_io")
        if pause_at != "writer"
        else None
    )
    schedule = hub_module.operation_task

    def defer_writer(coroutine):
        async def deferred():
            try:
                entered.set()
                await release.wait()
                return await coroutine
            finally:
                coroutine.close()

        return schedule(deferred())

    if pause_at != "receipt_io":
        monkeypatch.setattr(hub_module, "operation_task", defer_writer)
    owner_token = execution_owner.set(receipt)
    try:
        call = asyncio.create_task(
            service.execute_tool("hash", "fixture_mutate", "Fixture@hash", {"value": 7})
        )
    finally:
        execution_owner.reset(owner_token)
    try:
        await await_signal(entered, "scheduled custom writer", producers=(call,))
        assert len(PluginHub._pending) == 1
        match change:
            case "replace":
                replacement = ToolDefinitionModel(
                    name="fixture_mutate",
                    parameters=[
                        ToolParameterModel(name="destination", type="string", required=True)
                    ],
                )
                service.replace_project_catalog(
                    "hash", [replacement], kind="http", generation="session", publish_global=False
                )
            case "withdraw":
                service.replace_project_catalog(
                    "hash",
                    [],
                    kind="http",
                    generation="session",
                    publish_global=False,
                    withdraw=True,
                )
            case "socket":
                PluginHub._connections["session"] = SimpleNamespace(
                    **{
                        **vars(command_hub.websocket),
                        "state": SimpleNamespace(**vars(command_hub.websocket.state)),
                    }
                )
            case "generation":
                command_hub.websocket.state.plugin_generation = "replacement-generation"
            case "unchanged":
                pass
        release.set()
        result = await asyncio.wait_for(call, watchdog_timeout())
        if change == "unchanged":
            assert result.success is True
            assert [(message["name"], message["params"]) for message in command_hub.sent] == [
                ("fixture_mutate", {"value": 7})
            ]
        else:
            assert result.success is False
            assert result.data["reason"] == (
                "custom_tool_catalog_changed"
                if change in {"replace", "withdraw"}
                else "command_not_dispatched"
            )
            # A refusal before the write must not send execution or cancellation.
            assert command_hub.sent == []
        assert PluginHub._pending == {}
        assert not PluginHub._retained_results
        if receipt is not None:
            assert receipt.pins == 0
            assert len(receipt.dispatches) == int(change == "unchanged")
            assert len(receipt.observations) == int(change == "unchanged")
            assert receipt.unknown is False
    finally:
        release.set()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("receipted", [False, True])
@pytest.mark.parametrize("change", ["replace", "withdraw", "unchanged"])
async def test_custom_tool_rechecks_catalog_after_actual_credential_renewal(
    command_hub, monkeypatch, receipted, change
):
    async with credential_service(monkeypatch) as fixture:
        requester, plugin = await remote_hub_credentials(command_hub, monkeypatch, fixture.service)
        monkeypatch.setattr(CustomToolService, "_instance", None)
        service = CustomToolService(FastMCP("writer-renewal-guard"))
        tool = ToolDefinitionModel(name="fixture_mutate")
        await command_hub.registry.register_tools_for_session("session", [tool])
        service.replace_project_catalog(
            "hash", [tool], generation="session", user_id="alice", publish_global=False
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def renew(_key):
            entered.set()
            await release.wait()
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", renew)
        schedule = hub_module.operation_task

        def expire_before_writer(coroutine):
            fixture.now[0] = 10
            return schedule(coroutine)

        monkeypatch.setattr(hub_module, "operation_task", expire_before_writer)
        receipt = ReceiptBoundary(entered, release, pause=False) if receipted else None
        # A connected socket retains its credential through renewal just as the
        # production websocket lifetime does; otherwise idle expiry retires it.
        plugin_owner = fixture.service.acquire_lease_owner(plugin)
        token = execution_owner.set(receipt)
        try:
            with bind_credential_lease(requester):
                call = asyncio.create_task(
                    service.execute_tool(
                        "hash", "fixture_mutate", "Fixture@hash", {}, user_id="alice"
                    )
                )
        finally:
            execution_owner.reset(token)
        try:
            await await_signal(entered, "writer credential renewal", producers=(call,))
            assert len(PluginHub._pending) == 1
            if change != "unchanged":
                replacement = ToolDefinitionModel(
                    name="fixture_mutate",
                    parameters=[
                        ToolParameterModel(name="destination", type="string", required=True)
                    ],
                )
                service.replace_project_catalog(
                    "hash",
                    [replacement],
                    generation="session",
                    user_id="alice",
                    publish_global=False,
                    withdraw=change == "withdraw",
                )
            release.set()
            result = await asyncio.wait_for(call, watchdog_timeout())
            if change == "unchanged":
                assert result.success is True
                assert len(command_hub.sent) == 1
                assert command_hub.sent[0]["type"] == "execute"
            else:
                assert result.data["reason"] == "custom_tool_catalog_changed"
                assert result.data["dispatched"] is False
                assert command_hub.sent == []
            assert PluginHub._pending == {}
            assert not PluginHub._retained_results
            assert fixture.service._lease_owners == 1
            if receipt is not None:
                assert receipt.pins == 0
                assert len(receipt.dispatches) == int(change == "unchanged")
                assert receipt.unknown is False
        finally:
            release.set()
            await asyncio.gather(call, return_exceptions=True)
            fixture.service.release_lease_owner(plugin, plugin_owner)
        assert fixture.service._lease_owners == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("receipted", [False, True])
@pytest.mark.parametrize("renew_at", ["hub", "remote_auth", "nested_remote_auth", "inherited_ping"])
@pytest.mark.parametrize("change", ["replace", "withdraw", "generation", "revoked", "unchanged"])
async def test_actual_asgi_writer_guards_after_low_level_credential_renewal(
    monkeypatch, receipted, renew_at, change
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(CustomToolService, "_instance", None)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"):
        monkeypatch.setattr(PluginHub, name, {})
    server = FastMCP("actual-asgi-writer-guard")
    service = CustomToolService(server)
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=server)
    app = RemoteControlAuthMiddleware(Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)]))
    if renew_at == "nested_remote_auth":
        app = RemoteControlAuthMiddleware(app)
    endpoints = []
    connect = PluginHub.on_connect

    async def capture_endpoint(endpoint, websocket):
        endpoints.append(endpoint)
        await connect(endpoint, websocket)

    monkeypatch.setattr(PluginHub, "on_connect", capture_endpoint)
    async with credential_service(monkeypatch) as fixture:
        try:
            async with connected_plugin(app, "plugin-key", "hash") as peer:
                _, incoming, outgoing, registered = peer
                session_id = registered["session_id"]
                websocket = PluginHub._connections[session_id]
                tool = ToolDefinitionModel(name="fixture_mutate")
                await registry.register_tools_for_session(session_id, [tool])
                service.replace_project_catalog(
                    "hash", [tool], generation=session_id, user_id="alice", publish_global=False
                )
                requester = (await fixture.service.validate("request-key")).lease
                entered, release = asyncio.Event(), asyncio.Event()

                async def renew(_key):
                    entered.set()
                    await release.wait()
                    return ValidationResult(
                        valid=change != "revoked", user_id="alice" if change != "revoked" else None
                    )

                monkeypatch.setattr(fixture.service, "_validate_external", renew)
                send_json = websocket.send_json
                endpoint_send = endpoints[0].send

                async def expire_at_wire(message):
                    # Expire only after the command writer calls the real
                    # Starlette socket; its guarded ASGI send must renew now.
                    if message["type"] == "execute":
                        if renew_at == "inherited_ping":
                            await asyncio.create_task(send_json({"type": "ping"}))
                            if receipt is not None:
                                assert receipt.dispatches == []
                        fixture.now[0] = 10
                    await send_json(message)

                async def expire_at_auth(message):
                    if (
                        message["type"] == "websocket.send"
                        and json.loads(message["text"])["type"] == "execute"
                    ):
                        fixture.now[0] = 10
                    await endpoint_send(message)

                if renew_at in {"hub", "inherited_ping"}:
                    monkeypatch.setattr(websocket, "send_json", expire_at_wire)
                else:
                    monkeypatch.setattr(endpoints[0], "send", expire_at_auth)
                sent = []

                async def respond():
                    while True:
                        frame = await outgoing.get()
                        if frame["type"] != "websocket.send":
                            continue
                        message = json.loads(frame["text"])
                        sent.append(message)
                        if message["type"] == "execute":
                            await incoming.put(
                                {
                                    "type": "websocket.receive",
                                    "text": CommandResultMessage(
                                        id=message["id"], result={"success": True}
                                    ).model_dump_json(),
                                }
                            )

                response_worker = asyncio.create_task(respond())
                receipt = ReceiptBoundary(entered, release, pause=False) if receipted else None
                token = execution_owner.set(receipt)
                try:
                    with bind_credential_lease(requester):
                        call = asyncio.create_task(
                            service.execute_tool(
                                "hash", "fixture_mutate", "hash@hash", {}, user_id="alice"
                            )
                        )
                finally:
                    execution_owner.reset(token)
                try:
                    await await_signal(entered, "ASGI send renewal", producers=(call,))
                    if change in {"replace", "withdraw"}:
                        replacement = ToolDefinitionModel(
                            name="fixture_mutate",
                            parameters=[
                                ToolParameterModel(name="destination", type="string", required=True)
                            ],
                        )
                        service.replace_project_catalog(
                            "hash",
                            [replacement],
                            generation=session_id,
                            user_id="alice",
                            publish_global=False,
                            withdraw=change == "withdraw",
                        )
                    elif change == "generation":
                        websocket.state.plugin_generation = "replacement-generation"
                    release.set()
                    if change == "revoked":
                        result = (
                            await asyncio.wait_for(
                                asyncio.gather(call, return_exceptions=True), watchdog_timeout()
                            )
                        )[0]
                    else:
                        result = await asyncio.wait_for(call, watchdog_timeout())
                    expected_frames = ["ping"] if renew_at == "inherited_ping" else []
                    if change == "unchanged":
                        assert result.success is True
                        expected_frames.append("execute")
                    elif change == "revoked":
                        # Revocation cancels the actual requester owner; an HTTP
                        # boundary would translate that cancellation to auth loss.
                        assert isinstance(result, asyncio.CancelledError) or (
                            result.success is False and result.error == "auth_required"
                        )
                    else:
                        assert result.success is False
                        assert result.data["reason"] == (
                            "command_not_dispatched"
                            if change == "generation"
                            else "custom_tool_catalog_changed"
                        )
                    assert [message["type"] for message in sent] == expected_frames
                    assert PluginHub._pending == {}
                    assert not PluginHub._retained_results
                    if change != "revoked":
                        assert fixture.service._lease_owners == 1  # The actual connected socket.
                    if receipt is not None:
                        assert receipt.pins == 0
                        assert len(receipt.dispatches) == int(change == "unchanged")
                        assert receipt.unknown is False
                finally:
                    release.set()
                    await asyncio.gather(call, return_exceptions=True)
                    response_worker.cancel()
                    await asyncio.gather(response_worker, return_exceptions=True)
            assert fixture.service._lease_owners == 0
        finally:
            await PluginHub.shutdown()
