"""Foreground progress crosses the installed SDK's real streaming HTTP boundary.

Only the Unity peer is controlled: it supplies one observation while withholding
its final response. This is not evidence of native BatchExecute production.
"""

from __future__ import annotations

from tests.timing import await_signal, watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- existing Hub/uvicorn use one owned asyncio loop.
import json
import socket
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import httpx2
import pytest
import pytest_asyncio
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware import Middleware

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.test_command_progress import RecordingContext
from tests.test_command_progress import progress_api  # pylint: disable=unused-import
from tests.http.test_plugin_command_admission import (
    admission_hub,  # pylint: disable=unused-import
    register_socket,
)
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.unity_instance_middleware import UnityInstanceMiddleware

COMMANDS = [
    {"tool": "manage_scene", "params": {"action": "get_hierarchy"}},
    {"tool": "manage_scene", "params": {"action": "get_hierarchy"}},
]
FINAL = {"success": True, "data": {"attemptedCount": 2, "totalCommands": 2}}


class _ReadyServer(uvicorn.Server):
    """An owned in-process listener with an explicit startup/teardown barrier."""

    def __init__(self, configuration):
        super().__init__(configuration)
        self.ready = asyncio.Event()

    @contextmanager
    def capture_signals(self):
        # This fixture owns only its listener/task, never process signal handlers.
        yield

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self.ready.set()


@pytest_asyncio.fixture
async def progress_http(command_hub, monkeypatch):
    """Serve real SDK streaming; retain one controlled native final response."""
    from main import UnityMCP
    from services.tools.batch_execute import batch_execute, invalidate_cached_max_commands

    monkeypatch.setattr(config, "local_auth_token", "owned-command-progress-token")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    invalidate_cached_max_commands()
    executions = asyncio.Queue(maxsize=1)
    request_tokens, progress_frames = [], []
    command_hub.websocket.state.plugin_features = frozenset({"command_progress_v1"})

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        match message["name"]:
            case "batch_execute":
                await executions.put(message)
                return
            case "get_editor_state":
                result = {"success": True, "data": {"settings": {"batch_execute_max_commands": 25}}}
            case "ping":
                result = {"status": "success", "result": {"message": "pong"}}
            case unexpected:
                raise AssertionError(f"Unexpected preflight/native command: {unexpected}")
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket, CommandResultMessage(id=message["id"], result=result)
        )

    command_hub.websocket.send_json = answer
    server = UnityMCP("owned-command-progress")

    class CaptureRequestToken(Middleware):
        async def on_call_tool(self, context, call_next):
            rc = context.fastmcp_context.request_context
            request_tokens.append(rc.meta.get("progressToken") if rc is not None else None)
            return await call_next(context)

    server.add_middleware(CaptureRequestToken())
    server.add_middleware(ResponseLimitMiddleware())
    server.add_middleware(UnityInstanceMiddleware())
    server.tool(batch_execute)
    PluginHub.configure(command_hub.registry, mcp=server)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=False)

    async def observed(scope, receive, send):
        async def capture(message):
            for line in message.get("body", b"").splitlines():
                if line.startswith(b"data: "):
                    payload = json.loads(line[6:])
                    if payload.get("method") == "notifications/progress":
                        progress_frames.append(payload)
            await send(message)

        await app(scope, receive, capture)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        runner = _ReadyServer(
            uvicorn.Config(
                observed,
                host="127.0.0.1",
                port=listener.getsockname()[1],
                access_log=False,
                log_level="error",
                timeout_graceful_shutdown=2,
                ws="none",
            )
        )
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            await await_signal(runner.ready, "runner.ready")
            assert runner.started and not task.done()
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{listener.getsockname()[1]}/mcp",
                executions=executions,
                hub=command_hub,
                request_tokens=request_tokens,
                progress_frames=progress_frames,
            )
        finally:
            runner.should_exit = True
            try:
                await asyncio.wait_for(task, watchdog_timeout())
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            invalidate_cached_max_commands()


async def _progress(fixture, execution, **changes):
    frame = {
        "type": "command_progress",
        "id": execution["id"],
        "completed": 1,
        "total": 2,
        "stage": "batch_items",
        **changes,
    }
    await fixture.hub.endpoint.on_receive(fixture.hub.websocket, frame)


async def _complete(fixture, execution):
    await fixture.hub.endpoint._handle_command_result(
        fixture.hub.websocket, CommandResultMessage(id=execution["id"], result=FINAL)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"], ids=["2025-11-25", "2026-07-28"])
async def test_streaming_sdk_observes_progress_before_gated_final(progress_http, mode):
    # Given: the actual SDK client, production middleware/tool and one held final.
    received = asyncio.Queue(maxsize=2)

    async def on_progress(progress, total, message):
        received.put_nowait((progress, total, message))

    transport = StreamableHttpTransport(
        progress_http.url, headers={LOCAL_AUTH_HEADER: config.local_auth_token}
    )
    async with Client(transport, mode=mode, timeout=5, progress_handler=on_progress) as client:
        call = asyncio.create_task(
            client.call_tool_mcp(
                "batch_execute",
                {"commands": COMMANDS, "unity_instance": "Fixture@hash"},
                meta={"progressToken": "owned-original-token"},
            )
        )
        execution = None
        try:
            execution = await asyncio.wait_for(progress_http.executions.get(), watchdog_timeout())
            assert not call.done(), "The peer's final response must still be held"
            # When: item one has settled while the second/final remains controlled.
            await _progress(progress_http, execution)
            reported = await asyncio.wait_for(received.get(), watchdog_timeout())
            # Then: real streaming delivers the count on the original request,
            # without completing it or altering its one-batch native payload.
            assert reported[:2] == (1, 2)
            assert not call.done()
            assert execution["params"] == {"commands": COMMANDS}
            assert execution["progress"] == {"version": 1}
            assert len(progress_http.progress_frames) == 1
            assert (
                progress_http.progress_frames[0]["params"]["progressToken"]
                == (progress_http.request_tokens[-1])
            )
            assert progress_http.request_tokens[-1] is not None
            await _complete(progress_http, execution)
            result = await asyncio.wait_for(call, watchdog_timeout())
            assert not result.is_error
            assert result.structured_content == FINAL
            assert json.loads(result.content[0].text) == FINAL
            assert [
                m["name"] for m in progress_http.hub.sent if m.get("name") == "batch_execute"
            ] == ["batch_execute"]
        finally:
            if execution is not None and not call.done():
                await _complete(progress_http, execution)
                # Drain the real final SSE response before closing its SDK/client.
                # Immediate cancellation here created baseline disconnect/reset
                # and uvicorn shutdown noise; retain those original run logs.
                done, _ = await asyncio.wait({call}, timeout=watchdog_timeout())
                assert call in done, "Owned final response did not drain during teardown"
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_peer", [False, True], ids=["no-token", "old-peer"])
async def test_raw_no_token_keeps_execute_and_final_contract_unchanged(progress_http, old_peer):
    from tests.http.test_mcp_protocol_wire import _body, _headers, _message

    # Given: a real modern request without any progress token/callback.
    body = _body(
        "tools/call",
        {
            "name": "batch_execute",
            "arguments": {"commands": COMMANDS, "unity_instance": "Fixture@hash"},
        },
    )
    if old_peer:
        body["params"]["_meta"]["progressToken"] = "owned-token-old-peer"
        progress_http.hub.websocket.state.plugin_features = frozenset()
    async with httpx2.AsyncClient(
        headers={LOCAL_AUTH_HEADER: config.local_auth_token}, trust_env=False, timeout=5
    ) as client:
        call = asyncio.create_task(
            client.post(
                progress_http.url, headers=_headers("tools/call", name="batch_execute"), json=body
            )
        )
        execution = None
        try:
            execution = await asyncio.wait_for(progress_http.executions.get(), watchdog_timeout())
            # When: a peer attempts optional reporting despite no request opt-in.
            await _progress(progress_http, execution)
            await _complete(progress_http, execution)
            response = await asyncio.wait_for(call, watchdog_timeout())
            # Then: native command/result retain their existing shape with no progress.
            assert "progress" not in execution
            assert execution["params"] == {"commands": COMMANDS}
            assert progress_http.progress_frames == []
            assert response.status_code == 200
            assert _message(response)["result"]["structuredContent"] == FINAL
        finally:
            if execution is not None and not call.done():
                await _complete(progress_http, execution)
                # Drain the real final SSE response before closing its SDK/client.
                # Immediate cancellation here created baseline disconnect/reset
                # and uvicorn shutdown noise; retain those original run logs.
                done, _ = await asyncio.wait({call}, timeout=watchdog_timeout())
                assert call in done, "Owned final response did not drain during teardown"
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)


@asynccontextmanager
async def _held_hub_progress(hub, api, *, auth=None, session="session"):
    """Use the real Hub/actor with a wire-only controlled final response."""
    ctx = RecordingContext()
    sent = asyncio.Queue(maxsize=1)
    hub.websocket.state.plugin_features = frozenset({"command_progress_v1"})

    async def hold(message):
        hub.sent.append(message)
        if message["type"] == "execute":
            await sent.put(message)

    hub.websocket.send_json = hold
    async with api.foreground_progress(ctx):
        sink = api.claim_command_progress(total=2)

        async def send():
            if auth is None:
                return await PluginHub.send_command(
                    session, "batch_execute", {"commands": COMMANDS}, _progress=sink
                )
            return await auth.call(
                "requester:alice",
                PluginHub.send_command,
                session,
                "batch_execute",
                {"commands": COMMANDS},
                _progress=sink,
            )

        call = asyncio.create_task(send())
        dispatched = asyncio.create_task(sent.get())
        try:
            # Readiness is an event, not a one-second performance requirement.
            # Observe early command failures as well; the configurable watchdog
            # bounds hangs without changing the production command deadline.
            done, _ = await asyncio.wait(
                {dispatched, call}, timeout=watchdog_timeout(), return_when=asyncio.FIRST_COMPLETED
            )
            if call in done:
                result = await call
                pytest.fail(f"Command settled before controlled dispatch: {result!r}")
            assert dispatched in done, "Owned progress fixture did not dispatch"
            yield ctx, call, dispatched.result()
        finally:
            for task in (dispatched, call):
                task.cancel()
            await asyncio.gather(dispatched, call, return_exceptions=True)


async def _sender_turns():
    # Deterministic event-loop turns, not an elapsed-time wait: notify, wake,
    # then a ready sender can enter its immediate public callback.
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"completed": True},
        {"completed": -1},
        {"completed": 0},
        {"completed": float("nan")},
        {"completed": "1"},
        {"completed": 3},
        {"total": True},
        {"total": 3},
        {"stage": "application-secret"},
        {"extra": "not permitted"},
        {"extra": "x" * 4096},
        {"id": "wrong-command"},
    ],
    ids=[
        "bool",
        "negative",
        "zero",
        "nan",
        "coercion",
        "over-total",
        "bool-total",
        "different-total",
        "unknown-stage",
        "extra-field",
        "oversize",
        "wrong-id",
    ],
)
async def test_malformed_progress_is_dropped_without_settling_command(
    command_hub, progress_api, changes
):
    # Given: a real live pending batch and request-owned reporter.
    fixture = SimpleNamespace(hub=command_hub)
    async with _held_hub_progress(command_hub, progress_api) as (ctx, call, execution):
        # When: one malformed/foreign observation reaches the real dispatcher.
        await _progress(fixture, execution, **changes)
        await _sender_turns()
        # Then: no callback/result/deadline or replacement execution is created.
        assert ctx.calls == []
        assert not call.done()
        assert execution["id"] in PluginHub._pending
        await _progress(fixture, execution)
        assert (await asyncio.wait_for(ctx.received.get(), watchdog_timeout()))[:2] == (1, 2)
        await _complete(fixture, execution)
        assert await asyncio.wait_for(call, watchdog_timeout()) == FINAL


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_owner", ["socket", "user", "generation"])
async def test_stale_or_foreign_progress_owner_never_reaches_request(
    command_hub, progress_api, wrong_owner
):
    # Given: an admitted producer captures this exact socket/principal/generation.
    fixture = SimpleNamespace(hub=command_hub)
    async with _held_hub_progress(command_hub, progress_api) as (ctx, call, execution):
        socket_owner = command_hub.websocket
        original_state = vars(socket_owner.state).copy()
        sender = socket_owner
        try:
            match wrong_owner:
                case "socket":
                    sender = SimpleNamespace(state=SimpleNamespace(**original_state))
                case "user":
                    socket_owner.state.user_id = "other-user"
                case "generation":
                    socket_owner.state.plugin_generation = "replacement-generation"
            # When: the otherwise valid frame has the wrong live owner.
            frame = {
                "type": "command_progress",
                "id": execution["id"],
                "completed": 1,
                "total": 2,
                "stage": "batch_items",
            }
            await command_hub.endpoint.on_receive(sender, frame)
            await _sender_turns()
            # Then: the original request is neither notified nor completed.
            assert ctx.calls == []
            assert not call.done()
        finally:
            vars(socket_owner.state).clear()
            vars(socket_owner.state).update(original_state)


@pytest.mark.asyncio
async def test_optional_frame_flood_stops_parsing_but_retains_original_final(
    command_hub, progress_api
):
    # Given: one pending batch receives the maximum optional observation count.
    fixture = SimpleNamespace(hub=command_hub)
    async with _held_hub_progress(command_hub, progress_api) as (ctx, call, execution):
        # When: rejected frames exhaust the fixed frame budget before a valid count.
        for _ in range(256):
            await _progress(fixture, execution, stage="unrecognized")
        await _progress(fixture, execution)
        await _sender_turns()
        # Then: optional processing stops; the native result still settles once.
        assert ctx.calls == []
        assert not call.done()
        await _complete(fixture, execution)
        assert await asyncio.wait_for(call, watchdog_timeout()) == FINAL


@pytest.mark.asyncio
async def test_terminal_result_closes_pending_and_scheduled_progress(command_hub, progress_api):
    # Given: a valid progress update is queued but has not had a sender turn.
    fixture = SimpleNamespace(hub=command_hub)
    async with _held_hub_progress(command_hub, progress_api) as (ctx, call, execution):
        await _progress(fixture, execution)
        # When: the actual result wins before that scheduled optional send.
        await _complete(fixture, execution)
        result = await asyncio.wait_for(call, watchdog_timeout())
        await _progress(fixture, execution, completed=2)
        await _sender_turns()
        # Then: neither queued nor late work can notify after terminal settlement.
        assert result == FINAL
        assert ctx.calls == []
        assert execution["id"] not in PluginHub._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["requester", "plugin"])
async def test_retired_captured_credential_drops_scheduled_progress(
    admission_hub, progress_api, role
):
    # Given: real separately-owned requester/plugin lease generations.
    ws = await register_socket(admission_hub, "a", "alice")
    hub = SimpleNamespace(
        websocket=ws,
        endpoint=PluginHub({"type": "websocket"}, None, None),
        sent=[],
    )
    auth = admission_hub._test_credentials
    async with _held_hub_progress(hub, progress_api, auth=auth, session="a") as (
        ctx,
        call,
        execution,
    ):
        fixture = SimpleNamespace(hub=hub)
        await _progress(fixture, execution)
        # When: the captured generation retires before its scheduled SDK send.
        await auth.service.invalidate_cache(f"{role}:alice")
        await _sender_turns()
        # Then: no new authentication/replacement handle grants old reporting.
        assert ctx.calls == []
        assert all(message.get("type") == "execute" for message in hub.sent)


@pytest.mark.asyncio
async def test_valid_maximum_command_id_progress_is_admitted_before_result(
    command_hub, progress_api
):
    # Given: the actual36-character UUID owned by a pending native batch.
    fixture = SimpleNamespace(hub=command_hub)
    async with _held_hub_progress(command_hub, progress_api) as (ctx, call, execution):
        assert len(execution["id"]) == 36
        # When: its valid five-field progress graph crosses bounded size admission.
        await _progress(fixture, execution)
        reported = await asyncio.wait_for(ctx.received.get(), watchdog_timeout())
        # Then: conservative decoded-memory charging must permit the supported
        # wire schema; admission does not settle or resend the command.
        assert reported[:2] == (1, 2)
        assert not call.done()
        await _complete(fixture, execution)
        assert await asyncio.wait_for(call, watchdog_timeout()) == FINAL


@pytest.mark.asyncio
async def test_progress_fixture_waits_for_dispatch_under_scheduler_delay(
    command_hub, progress_api, monkeypatch
):
    original = PluginHub.send_command

    async def delayed(*args, **kwargs):
        # Reproduce a loaded runner before the first native dispatch. This is
        # fixture readiness, not a product deadline or progress timing assertion.
        await asyncio.sleep(1.1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(PluginHub, "send_command", delayed)
    async with _held_hub_progress(command_hub, progress_api) as (_, call, execution):
        await _complete(SimpleNamespace(hub=command_hub), execution)
        assert await call == FINAL
    assert not PluginHub._pending


@pytest.mark.asyncio
async def test_progress_fixture_surfaces_failure_before_dispatch(
    command_hub, progress_api, monkeypatch
):
    async def failed(*_args, **_kwargs):
        raise RuntimeError("controlled failure before dispatch")

    monkeypatch.setattr(PluginHub, "send_command", failed)
    with pytest.raises(RuntimeError, match="controlled failure before dispatch"):
        async with _held_hub_progress(command_hub, progress_api):
            pytest.fail("A failed command must not produce a ready fixture")
    assert not PluginHub._pending
