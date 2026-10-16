"""Production bounded-wait binding preserves the ordinary registered tool path."""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.server.middleware import Middleware

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.prepared_tool_registration import prepared_tool_factory  # pylint: disable=unused-import


@asynccontextmanager
async def bound_http(tmp_path, monkeypatch, *, root=None, token=None, hosted=False):
    from main import UnityMCP
    from core.config import config
    from core.local_auth import LOCAL_AUTH_HEADER
    from services.tools.run_tests import get_test_job
    from transport.test_wait_binding import install_test_job_tasks
    from transport.unity_instance_middleware import UnityInstanceMiddleware
    from transport.response_limit_middleware import ResponseLimitMiddleware

    token = token or secrets.token_urlsafe(32)
    monkeypatch.setattr(config, "local_auth_token", token)
    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    server = UnityMCP("bound-test-wait", mask_error_details=True)
    server.add_middleware(UnityInstanceMiddleware())
    server.add_middleware(ResponseLimitMiddleware())
    server.tool(get_test_job)
    runtime = install_test_job_tasks(server, root or tmp_path / "waits", remote_hosted=hosted)
    assert runtime is not None
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={LOCAL_AUTH_HEADER: token, "Accept": "application/json, text/event-stream"},
        ) as client:
            yield client, runtime


async def bound_call(client, method, params, *, tasks=False, selector=None, headers=None):
    from tests.http.test_mcp_protocol_wire import _body, _headers, _message

    body = _body(method, params)
    body["params"]["_meta"]["unity_instance"] = "Fixture@hash"
    if tasks:
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
            "extensions": {"io.modelcontextprotocol/tasks": {}}
        }
    name = params.get("taskId") if method.startswith("tasks/") else params.get("name")
    if selector is not None:
        if method == "tools/call":
            body["params"]["arguments"]["unity_instance"] = selector
        else:
            body["params"]["_meta"]["unity_instance"] = selector
    response = await client.post(
        "/mcp", headers={**_headers(method, name=name), **(headers or {})}, json=body
    )
    return _message(response)


async def completed_wait(client, runtime, params):
    created = await bound_call(client, "tools/call", params, tasks=True)
    assert created["result"]["resultType"] == "task", created
    with anyio.fail_after(3):
        while runtime._workers:
            await anyio.sleep(0)
    result = await bound_call(
        client, "tasks/get", {"taskId": created["result"]["taskId"]}, tasks=True
    )
    assert result["result"]["status"] == "completed", result
    return result["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "terminal",
        "tool_error",
        "invalid_native",
        "inner_running",
        "outer_running",
        "outer_no_reply",
    ],
)
async def test_authenticated_production_tool_task_matches_whole_modern_ordinary_result(
    command_hub, tmp_path, monkeypatch, case
):
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] != "execute" or case == "outer_no_reply":
            return
        response = {
            "success": True,
            "data": {
                "job_id": case,
                "status": "running" if case in {"inner_running", "outer_running"} else "failed",
            },
        }
        if case == "tool_error":
            response = {"success": False, "error": "Existing native job unavailable"}
        elif case == "invalid_native":
            response = {"success": True, "data": {"status": "failed"}}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket, CommandResultMessage(id=message["id"], result=response)
        )

    command_hub.websocket.send_json = answer
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 3 if case == "inner_running" else 0.18)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        params = {"name": "get_test_job", "arguments": {"job_id": case, "wait_timeout": 1}}
        ordinary = await bound_call(client, "tools/call", params)
        assert "error" not in ordinary, ordinary
        created = await bound_call(client, "tools/call", params, tasks=True)
        assert created["result"]["resultType"] == "task", created
        task_id = created["result"]["taskId"]
        with anyio.fail_after(2):
            while runtime._workers:
                await anyio.sleep(0)
        task = await bound_call(client, "tasks/get", {"taskId": task_id}, tasks=True)
        assert task["result"]["status"] == "completed", task
        final = task["result"]["result"]
        assert "io.modelcontextprotocol/serverInfo" in final["_meta"]
        if case.startswith("outer_"):
            native = [message for message in command_hub.sent if message["type"] == "execute"]
            assert final["structuredContent"]["data"]["command_id"] == native[-1]["id"]
            assert ordinary["result"]["structuredContent"]["data"]["command_id"] == native[0]["id"]
            text = json.loads(ordinary["result"]["content"][0]["text"])
            assert text == ordinary["result"]["structuredContent"]
            ordinary["result"]["structuredContent"]["data"]["command_id"] = final[
                "structuredContent"
            ]["data"]["command_id"]
            text["data"]["command_id"] = final["structuredContent"]["data"]["command_id"]
            ordinary["result"]["content"][0]["text"] = json.dumps(
                text, separators=(",", ":"), ensure_ascii=False
            )
        assert final == ordinary["result"]
        assert all(message.get("name") != "run_tests" for message in command_hub.sent)


@pytest.mark.asyncio
async def test_detached_budget_copies_live_evidence_and_cannot_extend_original_deadline():
    from transport.plugin_hub import (
        _CommandBudget,
        _HttpRequestBudget,
        http_request_budget,
        capture_http_request_budget,
        detached_http_request_budget,
    )

    now = asyncio.get_running_loop().time()
    original = _HttpRequestBudget(
        now,
        _CommandBudget(now + 0.5, phase="selection", session_id="original"),
        asyncio.timeout_at(now + 0.5),
    )
    token = http_request_budget.set(original)
    try:
        snapshot = capture_http_request_budget()
    finally:
        http_request_budget.reset(token)
    original.budget.session_id = "mutated-original"
    guard = asyncio.timeout_at(snapshot.expires_at)
    with detached_http_request_budget(snapshot, guard) as owned:
        owned.extend(900)
        command = owned.command_budget("get_test_job", 900)
        assert owned.budget.expires_at == snapshot.expires_at
        assert command.expires_at <= snapshot.expires_at
        command.phase = "reply"
        command.session_id = "captured-target"
        command.command_id = "actual-command"
        command.dispatched = True
        timeout = owned.budget.timeout_response("get_test_job")
        assert timeout["data"]["reason"] == "outcome_unknown"
        assert timeout["data"]["command_id"] == "actual-command"
        assert timeout["data"]["session_id"] == "captured-target"
    assert snapshot.evidence.session_id == "original"
    assert http_request_budget.get() is None


@pytest.mark.asyncio
async def test_registered_wait_honors_captured_inner_deadline_without_fresh_clock(
    command_hub, monkeypatch
):
    from services.tools.test_job_wait import test_job_wait_deadline
    from services.tools.run_tests import get_test_job
    from transport.unity_instance_middleware import UnityInstanceMiddleware
    from transport.plugin_hub import PluginHub
    from transport.models import CommandResultMessage

    server = FastMCP("captured-inner-deadline")
    middleware = UnityInstanceMiddleware()
    server.add_middleware(middleware)

    class CapturedDeadline(Middleware):
        async def on_call_tool(self, context, call_next):
            if context.message.name != "get_test_job":
                return await call_next(context)
            token = test_job_wait_deadline.set(asyncio.get_running_loop().time() + 0.1)
            try:
                return await call_next(context)
            finally:
                test_job_wait_deadline.reset(token)

    server.add_middleware(CapturedDeadline())
    server.tool(get_test_job)

    @server.tool
    async def select_fixture(ctx: Context) -> dict:
        await middleware.set_active_instance(ctx, "Fixture@hash")
        return {"success": True}

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": True, "data": {"job_id": "existing", "status": "running"}},
                ),
            )

    command_hub.websocket.send_json = answer
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.4)
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        result = await client.call_tool_mcp(
            "get_test_job", {"job_id": "existing", "wait_timeout": 60}
        )
    assert result.is_error is False
    assert result.structured_content["result"]["data"]["status"] == "running"
    assert len([message for message in command_hub.sent if message["type"] == "execute"]) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("refuse", [False, True])
async def test_journal_admission_spends_original_inner_deadline_even_when_capacity_falls_back(
    command_hub, tmp_path, monkeypatch, refuse
):
    from threading import Event
    from services.test_wait_journal import JournalCapacity
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 3)
    entered, release = Event(), Event()
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        create = runtime.journal.create
        captured = []
        capture = runtime.capture

        async def remember(*args):
            value = await capture(*args)
            captured.append(value)
            return value

        def hold(operation):
            entered.set()
            assert release.wait(3)
            if refuse:
                raise JournalCapacity("Fixture physical reservation refusal")
            return create(operation)

        runtime.capture = remember
        monkeypatch.setattr(runtime.journal, "create", hold)
        request = asyncio.create_task(
            bound_call(
                client,
                "tools/call",
                {"name": "get_test_job", "arguments": {"job_id": "admission", "wait_timeout": 1}},
                tasks=True,
            )
        )
        try:
            assert await anyio.to_thread.run_sync(entered.wait, 2)
            await anyio.sleep_until(captured[0].wait_deadline + 0.01)
        finally:
            release.set()
        response = await request
        if refuse:
            final = response["result"]
            assert final["resultType"] == "complete"
        else:
            with anyio.fail_after(2):
                while runtime._workers:
                    await anyio.sleep(0)
            final = (
                await bound_call(
                    client, "tasks/get", {"taskId": response["result"]["taskId"]}, tasks=True
                )
            )["result"]["result"]
        assert final["isError"] is False
        assert final["structuredContent"]["result"]["success"] is False
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_cancelled_task_wait_does_not_release_an_ordinary_shared_status_reader(
    command_hub, tmp_path, monkeypatch
):
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub

    dispatched = anyio.Event()
    command_hub.websocket.state.plugin_features = frozenset()

    async def hold(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            dispatched.set()

    command_hub.websocket.send_json = hold
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 3)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        params = {"name": "get_test_job", "arguments": {"job_id": "shared", "wait_timeout": 2}}
        created = await bound_call(client, "tools/call", params, tasks=True)
        with anyio.fail_after(2):
            await dispatched.wait()
        ordinary = asyncio.create_task(bound_call(client, "tools/call", params))
        await anyio.wait_all_tasks_blocked()
        assert len(command_hub.sent) == 1
        task_id = created["result"]["taskId"]
        await bound_call(client, "tasks/cancel", {"taskId": task_id}, tasks=True)
        with anyio.fail_after(2):
            while runtime._workers:
                await anyio.sleep(0)
        assert not ordinary.done()
        # The pending read is still owned by the ordinary caller. Only its real
        # native reply may release that shared result and return its tool schema.
        message = command_hub.sent[0]
        assert message["name"] == "get_test_job"
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"],
                result={"success": True, "data": {"job_id": "shared", "status": "succeeded"}},
            ),
        )
        reply = await ordinary
        assert reply["result"]["structuredContent"]["result"]["data"]["status"] == "succeeded"
        status = await bound_call(client, "tasks/get", {"taskId": task_id}, tasks=True)
        assert status["result"]["status"] == "cancelled"
        assert len(command_hub.sent) == 1


@pytest.mark.asyncio
async def test_explicit_legacy_authenticated_http_keeps_ordinary_wait_with_extension_installed(
    command_hub, tmp_path, monkeypatch
):
    import httpx2
    from fastmcp.client.transports import StreamableHttpTransport
    from core.local_auth import LOCAL_AUTH_HEADER
    from transport.models import CommandResultMessage

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": True, "data": {"job_id": "legacy", "status": "succeeded"}},
                ),
            )

    command_hub.websocket.send_json = answer
    async with bound_http(tmp_path, monkeypatch) as (http, runtime):

        def factory(**kwargs):
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=http._transport.app), **kwargs
            )

        transport = StreamableHttpTransport(
            "http://fixture/mcp",
            headers={LOCAL_AUTH_HEADER: http.headers[LOCAL_AUTH_HEADER]},
            httpx_client_factory=factory,
        )
        async with Client(transport, mode="legacy") as client:
            result = await client.call_tool_mcp(
                "get_test_job", {"job_id": "legacy", "wait_timeout": 1}
            )
        assert result.structured_content["result"]["data"]["status"] == "succeeded"
        assert not runtime._workers
        assert not list(runtime.journal.root.glob("*.json"))


@pytest.mark.asyncio
async def test_task_preserves_ordinary_status_read_reconnect_policy_without_starting_native_run(
    command_hub, tmp_path, monkeypatch
):
    from copy import copy
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 4)
    source = command_hub.websocket
    replacement = copy(source)
    replacement.state = copy(source.state)
    replacement.state.plugin_session_id = "replacement"
    replacement.state.plugin_generation = "replacement-generation"
    sent = []

    async def answer(socket, sid, message):
        if message["type"] != "execute":
            return
        sent.append((sid, message))
        assert message["name"] == "get_test_job"
        await command_hub.endpoint._handle_command_result(
            socket,
            CommandResultMessage(
                id=message["id"],
                result={
                    "success": True,
                    "data": {
                        "job_id": "reload-read",
                        "status": "running" if sid == "session" else "succeeded",
                    },
                },
            ),
        )
        if sid == "session":
            await command_hub.registry.register("replacement", "Fixture", "hash", "fixture")
            PluginHub._connections.pop("session", None)
            PluginHub._connections["replacement"] = replacement

    source.send_json = lambda message: answer(source, "session", message)
    replacement.send_json = lambda message: answer(replacement, "replacement", message)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        params = {"name": "get_test_job", "arguments": {"job_id": "reload-read", "wait_timeout": 3}}
        ordinary = (await bound_call(client, "tools/call", params))["result"]
        assert [sid for sid, _ in sent] == ["session", "replacement"]
        await command_hub.registry.register("session", "Fixture", "hash", "fixture")
        PluginHub._connections.pop("replacement", None)
        PluginHub._connections["session"] = source
        sent.clear()
        created = await completed_wait(client, runtime, params)
        assert created["result"] == ordinary
        assert [sid for sid, _ in sent] == ["session", "replacement"]
        record = runtime.journal.get(created["taskId"], runtime.journal.local_owner)
        assert record.operation.session_id == "session"
        assert record.operation.instance == "Fixture@hash"


@pytest.mark.asyncio
async def test_valid_local_token_rotation_retains_installation_owner_and_stale_token_is_rejected_before_lookup(
    command_hub, tmp_path, monkeypatch
):
    from core.local_auth import LOCAL_AUTH_HEADER
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub
    from tests.http.test_mcp_protocol_wire import _body, _headers

    async def answer(message):
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": True, "data": {"job_id": "durable", "status": "succeeded"}},
                ),
            )

    command_hub.websocket.send_json = answer
    old_token, new_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    root = tmp_path / "private"
    async with bound_http(tmp_path, monkeypatch, root=root, token=old_token) as (client, runtime):
        record = await completed_wait(
            client,
            runtime,
            {"name": "get_test_job", "arguments": {"job_id": "durable", "wait_timeout": 1}},
        )
        owner = runtime.journal.local_owner
        task_id, expected = record["taskId"], record["result"]
    # A completed wait is independent of current plugin connectivity and launch token.
    await command_hub.registry.unregister("session")
    PluginHub._connections.clear()
    async with bound_http(tmp_path, monkeypatch, root=root, token=new_token) as (client, runtime):
        assert runtime.journal.local_owner == owner
        called = []
        lookup = runtime.journal.get

        def audited(*args):
            called.append(args)
            return lookup(*args)

        monkeypatch.setattr(runtime.journal, "get", audited)
        denied = await client.post(
            "/mcp",
            headers={**_headers("tasks/get", name=task_id), LOCAL_AUTH_HEADER: old_token},
            json=_body("tasks/get", {"taskId": task_id}),
        )
        assert denied.status_code == 401
        assert not called
        fetched = await bound_call(client, "tasks/get", {"taskId": task_id}, tasks=True)
        assert fetched["result"]["result"] == expected
        assert called[-1][1] == owner
    stored = b"".join(path.read_bytes() for path in root.iterdir() if path.is_file())
    assert old_token.encode() not in stored
    assert new_token.encode() not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["disabled", "sdk", "shape", "occupied"])
async def test_production_factory_advertises_only_successfully_initialized_opt_in(
    prepared_tool_factory, tmp_path, monkeypatch, refusal
):
    from fastmcp.tools.base import Tool
    from core.config import config
    from main import create_mcp_server
    from transport.test_wait_binding import install_test_job_tasks
    from transport.test_job_tasks import TASKS_EXTENSION
    from tests.http.test_mcp_protocol_wire import _body, _headers

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    root = tmp_path / "private"
    held = None
    if refusal == "sdk":
        monkeypatch.setattr("transport.test_wait_binding.version", lambda name: "unreviewed")
    elif refusal == "shape":
        # A same-version incompatible hook must refuse BEFORE opening a store or
        # registering the extension, rather than silently using a new formatter.
        monkeypatch.setattr(
            "fastmcp.server.mixins.mcp_operations._normalize_call_tool_result",
            lambda result, incompatible: result,
        )
    elif refusal == "occupied":
        held = install_test_job_tasks(FastMCP("writer"), root, remote_hosted=False)
        assert held is not None
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Tool, "from_function", prepared_tool_factory)
            server = create_mcp_server(
                False, test_wait_tasks_root=None if refusal == "disabled" else root
            )
        assert getattr(server, "_test_job_tasks", None) is None
        app = server.http_app(json_response=True)
        from core.local_auth import LOCAL_AUTH_HEADER

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://fixture",
                headers={
                    LOCAL_AUTH_HEADER: config.local_auth_token,
                    "Accept": "application/json, text/event-stream",
                },
            ) as client:
                response = await client.post(
                    "/mcp", headers=_headers("server/discover"), json=_body("server/discover")
                )
                assert TASKS_EXTENSION not in response.json()["result"]["capabilities"].get(
                    "extensions", {}
                )
    finally:
        if held is not None:
            await held.close(grace_seconds=0)


@pytest.mark.asyncio
async def test_actual_factory_hosted_waits_capture_current_tenant_and_project_and_refuse_foreign_task(
    prepared_tool_factory, tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastmcp.tools.base import Tool
    from starlette.websockets import WebSocketState
    from core.config import config
    from services.api_key_service import ApiKeyService, ValidationResult
    from main import create_mcp_server
    from transport.models import CommandResultMessage, ToolDefinitionModel
    from transport.plugin_hub import PluginHub
    from transport.test_job_tasks import TASKS_EXTENSION

    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    validate = AsyncMock(
        side_effect=lambda key, **kwargs: ValidationResult(
            valid=key in {"alice-key", "bob-key"},
            user_id={"alice-key": "alice", "bob-key": "bob"}.get(key),
        )
    )
    monkeypatch.setattr(
        ApiKeyService, "_instance", SimpleNamespace(validate=validate, aclose=AsyncMock())
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        server = create_mcp_server(False, test_wait_tasks_root=tmp_path / "private")
    runtime = getattr(server, "_test_job_tasks")
    app = server.http_app(json_response=True)
    async with app.router.lifespan_context(app):
        sent = []
        endpoint = PluginHub.__new__(PluginHub)
        registry = PluginHub._registry
        for sid, project, user in (
            ("alice-a", "a", "alice"),
            ("alice-other", "other", "alice"),
            ("bob-a", "a", "bob"),
            ("bob-only", "only", "bob"),
        ):
            await registry.register(sid, project.title(), project, "fixture", user_id=user)
            await registry.register_tools_for_session(
                sid, [ToolDefinitionModel(name="get_test_job")]
            )
            socket = SimpleNamespace(
                state=SimpleNamespace(
                    plugin_registered=True,
                    plugin_session_id=sid,
                    plugin_generation=sid,
                    plugin_features=frozenset(),
                    user_id=user,
                ),
                client_state=WebSocketState.CONNECTED,
                application_state=WebSocketState.CONNECTED,
                close=AsyncMock(),
            )

            async def answer(message, socket=socket, sid=sid):
                if message["type"] == "execute":
                    sent.append((sid, message))
                    await endpoint._handle_command_result(
                        socket,
                        CommandResultMessage(
                            id=message["id"],
                            result={
                                "success": True,
                                "data": {"job_id": "owned", "status": "succeeded", "error": sid},
                            },
                        ),
                    )

            socket.send_json = answer
            PluginHub._connections[sid] = socket
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={"X-API-Key": "alice-key", "Accept": "application/json, text/event-stream"},
        ) as client:
            discovered = await bound_call(client, "server/discover", {})
            assert TASKS_EXTENSION in discovered["result"]["capabilities"]["extensions"]
            records = []
            for user, selector, sid in (
                ("alice", "A@a", "alice-a"),
                ("bob", "A@a", "bob-a"),
                ("alice", "Other@other", "alice-other"),
            ):
                client.headers["X-API-Key"] = user + "-key"
                params = {
                    "name": "get_test_job",
                    "arguments": {"job_id": "owned", "wait_timeout": 1},
                }
                ordinary = await bound_call(client, "tools/call", params, selector=selector)
                assert ordinary["result"]["structuredContent"]["result"]["data"]["error"] == sid
                created = await bound_call(
                    client, "tools/call", params, tasks=True, selector=selector
                )
                assert created["result"]["resultType"] == "task", created
                with anyio.fail_after(3):
                    while runtime._workers:
                        await anyio.sleep(0)
                result = await bound_call(
                    client,
                    "tasks/get",
                    {"taskId": created["result"]["taskId"]},
                    tasks=True,
                    selector=selector,
                )
                assert result["result"]["result"] == ordinary["result"]
                stored = runtime.journal.get(result["result"]["taskId"], f"hosted:v1:{user}")
                assert (
                    stored.operation.user_id,
                    stored.operation.session_id,
                    stored.operation.instance,
                ) == (user, sid, selector)
                records.append(result["result"])
            assert [sid for sid, _ in sent] == [
                "alice-a",
                "alice-a",
                "bob-a",
                "bob-a",
                "alice-other",
                "alice-other",
            ]
            # The authenticated principal, never new routing metadata, owns lookups.
            client.headers["X-API-Key"] = "alice-key"
            foreign = await bound_call(
                client,
                "tasks/get",
                {"taskId": records[1]["taskId"]},
                tasks=True,
                selector="Other@other",
            )
            assert foreign["error"]["code"] == -32602
            denied = await bound_call(
                client,
                "tools/call",
                {"name": "get_test_job", "arguments": {"job_id": "owned", "wait_timeout": 1}},
                tasks=True,
                selector="Only@only",
            )
            assert "taskId" not in denied.get("result", {})
            assert len(sent) == 6
            missing_cap = await bound_call(client, "tasks/get", {"taskId": records[0]["taskId"]})
            assert missing_cap["error"]["code"] == -32021
            # A completed wait can be read after its selected source disappears.
            await registry.unregister("alice-a")
            PluginHub._connections.pop("alice-a")
            fetched = await bound_call(
                client,
                "tasks/get",
                {"taskId": records[0]["taskId"]},
                tasks=True,
                selector="Other@other",
            )
            assert fetched["result"]["result"] == records[0]["result"]
            updated = await bound_call(
                client,
                "tasks/update",
                {"taskId": records[0]["taskId"], "inputResponses": {}},
                tasks=True,
            )
            assert updated["result"]["resultType"] == "complete"
            cancelled = await bound_call(
                client, "tasks/cancel", {"taskId": records[0]["taskId"]}, tasks=True
            )
            assert cancelled["result"]["resultType"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", [128 * 1024, 32 * 1024 * 1024])
@pytest.mark.parametrize("over", [False, True])
async def test_detached_wait_reuses_ordinary_response_limits_before_persistence(
    command_hub, tmp_path, monkeypatch, over, ceiling
):
    from models.response_limits import MAX_RESPONSE_BYTES, response_size
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr("transport.response_limit_middleware.MAX_RESPONSE_BYTES", ceiling)

    # Native input fits its existing limit; SDK text+structured duplication is
    # just below/above the SAME ordinary wire budget, including envelope slack.
    # At the real 32MiB ceiling this duplicate SDK graph already exceeds the
    # ordinary 256MiB retained-memory bound; both paths must preserve refusal.
    # The smaller ceiling isolates byte-boundary admission without relaxing that
    # production memory policy or a Task-only record limit.
    text = "x" * ((ceiling - 32_768) // 2 + (2048 if over else -2048))

    async def answer(message):
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={
                        "success": True,
                        "data": {"job_id": "ceiling", "status": "succeeded", "error": text},
                    },
                ),
            )

    command_hub.websocket.send_json = answer
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 5)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        params = {"name": "get_test_job", "arguments": {"job_id": "ceiling", "wait_timeout": 1}}
        ordinary = (await bound_call(client, "tools/call", params))["result"]
        created = await completed_wait(client, runtime, params)
        final = created["result"]
        assert final == ordinary
        refused = over or ceiling == MAX_RESPONSE_BYTES
        assert final["isError"] is refused
        assert response_size(final, max_bytes=MAX_RESPONSE_BYTES) is not None
        if refused:
            assert final["structuredContent"]["data"]["reason"] == "response_payload_limit"
        else:
            assert final["structuredContent"]["result"]["data"]["error"] == text


@pytest.mark.asyncio
async def test_cancel_actual_detached_wait_does_not_cancel_native_test_and_next_status_remains_callable(
    command_hub, tmp_path, monkeypatch
):
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub

    dispatched = anyio.Event()
    command_hub.websocket.state.plugin_features = frozenset()

    async def hold(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            dispatched.set()

    command_hub.websocket.send_json = hold
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 3)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        created = await bound_call(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "still-running", "wait_timeout": 2}},
            tasks=True,
        )
        task_id = created["result"]["taskId"]
        with anyio.fail_after(2):
            await dispatched.wait()
        cancelled = await bound_call(client, "tasks/cancel", {"taskId": task_id}, tasks=True)
        assert cancelled["result"]["resultType"] == "complete"
        with anyio.fail_after(2):
            while runtime._workers:
                await anyio.sleep(0)
        status = await bound_call(client, "tasks/get", {"taskId": task_id}, tasks=True)
        assert status["result"]["status"] == "cancelled"
        assert "result" not in status["result"]
        assert all(
            message["type"] == "execute" and message["name"] == "get_test_job"
            for message in command_hub.sent
        )

        async def answer(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                await command_hub.endpoint._handle_command_result(
                    command_hub.websocket,
                    CommandResultMessage(
                        id=message["id"],
                        result={
                            "success": True,
                            "data": {"job_id": "still-running", "status": "running"},
                        },
                    ),
                )

        command_hub.websocket.send_json = answer
        ordinary = await bound_call(
            client, "tools/call", {"name": "get_test_job", "arguments": {"job_id": "still-running"}}
        )
        assert ordinary["result"]["structuredContent"]["result"]["data"]["status"] == "running"
        assert not runtime.admission.active


@pytest.mark.asyncio
async def test_detached_registered_tool_has_owned_context_without_original_http_request(
    command_hub, tmp_path, monkeypatch
):
    from fastmcp.server.dependencies import get_context, get_http_request
    from transport.models import CommandResultMessage
    from transport.plugin_hub import http_request_budget

    observed = []
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        capture = runtime.capture
        origin = []

        async def remember(*args):
            origin.append((get_context(), get_http_request(), http_request_budget.get()))
            return await capture(*args)

        runtime.capture = remember
        call_tool = runtime.server.call_tool

        async def checked(*args, **kwargs):
            if kwargs.get("run_middleware") is not False:
                return await call_tool(*args, **kwargs)
            context = get_context()
            assert context is not origin[0][0]
            assert context.request_context is None
            with pytest.raises(RuntimeError):
                get_http_request()
            assert http_request_budget.get() is not origin[0][2]
            observed.append(
                (
                    await context.get_state("unity_instance"),
                    await context.get_state("unity_session_id"),
                )
            )
            return await call_tool(*args, **kwargs)

        monkeypatch.setattr(runtime.server, "call_tool", checked)

        async def answer(message):
            if message["type"] == "execute":
                await command_hub.endpoint._handle_command_result(
                    command_hub.websocket,
                    CommandResultMessage(
                        id=message["id"],
                        result={
                            "success": True,
                            "data": {"job_id": "isolated", "status": "succeeded"},
                        },
                    ),
                )

        command_hub.websocket.send_json = answer
        final = await completed_wait(
            client,
            runtime,
            {"name": "get_test_job", "arguments": {"job_id": "isolated", "wait_timeout": 1}},
        )
        assert final["result"]["isError"] is False
        assert observed == [("Fixture@hash", "session")]


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_pinned_sdk_result_kernel_normalizes_list_and_tuple_without_duplicate_formatter(
    tmp_path, monkeypatch, structured
):
    from fastmcp.tools.base import ToolResult
    from transport.test_wait_binding import _SdkResultKernel

    server = FastMCP("sdk-result-variants")
    result = ToolResult(content="existing", structured_content={"value": 1} if structured else None)
    assert type(result.to_mcp_result()) is (tuple if structured else list)
    kernel = _SdkResultKernel(server)
    final = kernel.serialize(result)
    assert final["resultType"] == "complete"
    assert "io.modelcontextprotocol/serverInfo" in final["_meta"]
    assert final["content"] == [{"type": "text", "text": "existing"}]
    assert final.get("structuredContent") == ({"value": 1} if structured else None)


@pytest.mark.asyncio
async def test_journal_delay_cannot_refresh_original_outer_deadline_or_dispatch_after_expiry(
    command_hub, tmp_path, monkeypatch
):
    from threading import Event
    from transport.plugin_hub import PluginHub

    entered, release = Event(), Event()
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.1)
    async with bound_http(tmp_path, monkeypatch) as (client, runtime):
        capture = runtime.capture
        captured = []
        create = runtime.journal.create

        async def remember(*args):
            value = await capture(*args)
            captured.append(value)
            return value

        def hold(operation):
            entered.set()
            assert release.wait(2)
            return create(operation)

        runtime.capture = remember
        monkeypatch.setattr(runtime.journal, "create", hold)
        request = asyncio.create_task(
            bound_call(
                client,
                "tools/call",
                {"name": "get_test_job", "arguments": {"job_id": "expired", "wait_timeout": 1}},
                tasks=True,
            )
        )
        try:
            assert await anyio.to_thread.run_sync(entered.wait, 1)
            await anyio.sleep_until(captured[0].outer_deadline + 0.01)
        finally:
            release.set()
        reply = await request
        with anyio.fail_after(2):
            while runtime._workers or runtime._preparing:
                await anyio.sleep(0)
        if reply["result"]["resultType"] == "task":
            reply = await bound_call(
                client, "tasks/get", {"taskId": reply["result"]["taskId"]}, tasks=True
            )
            final = reply["result"]["result"]
        else:
            final = reply["result"]
        assert final["isError"] is True
        assert final["structuredContent"]["data"]["reason"] == "command_deadline_exceeded"
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_missing_optional_runner_export_preserves_default_http_and_refuses_task_advertisement(
    tmp_path, monkeypatch
):
    import builtins
    import importlib.util
    from pathlib import Path

    from core.config import config
    from core.local_auth import LOCAL_AUTH_HEADER
    from main import UnityMCP
    from tests.http.test_mcp_protocol_wire import _body, _headers

    # Base SDK code remains intact and callable. Only the adapter's named import
    # is unavailable, modelling an otherwise usable SDK with this private hook
    # removed, rather than deleting a class that core runner functions also use.
    original_import = builtins.__import__

    def missing_export(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mcp.server.runner" and "ServerRunner" in fromlist:
            raise ImportError("Optional named runner export unavailable")
        return original_import(name, globals, locals, fromlist, level)

    source = Path(__file__).resolve().parents[2] / "src/transport/test_wait_binding.py"
    spec = importlib.util.spec_from_file_location("transport._missing_runner_probe", source)
    binding = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", missing_export)
    spec.loader.exec_module(binding)
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", token)
    server = UnityMCP("missing-optional-hook")

    @server.tool
    async def ordinary_alive() -> dict:
        return {"available": True}

    root = tmp_path / "must-not-open"
    assert binding.install_test_job_tasks(server, root, remote_hosted=False) is None
    assert not root.exists()
    app = server.http_app(json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={LOCAL_AUTH_HEADER: token, "Accept": "application/json, text/event-stream"},
        ) as client:
            discovery = await client.post(
                "/mcp", headers=_headers("server/discover"), json=_body("server/discover")
            )
            assert "io.modelcontextprotocol/tasks" not in discovery.json()["result"][
                "capabilities"
            ].get("extensions", {})
            ordinary = await client.post(
                "/mcp",
                headers=_headers("tools/call", name="ordinary_alive"),
                json=_body("tools/call", {"name": "ordinary_alive"}),
            )
            assert ordinary.status_code == 200
            assert ordinary.json()["result"]["resultType"] == "complete"
            assert ordinary.json()["result"]["structuredContent"] == {"available": True}
