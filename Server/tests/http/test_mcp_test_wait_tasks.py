"""Isolated SDK Tasks composition; production main integration is deliberately absent."""

from typing import Literal
import asyncio
import json
import time
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.extensions import ServerExtension
from mcp_types import Result
from pydantic import Field

from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.test_job_tasks import CreateWaitTaskResult
from transport.test_job_tasks import (
    CapturedWait,
    TestJobTasks,
    WaitAdmission,
    WaitTaskDeliveryMiddleware,
)
from services.test_wait_journal import DeadlineCheckpoint, WaitJournal, WaitOperation
from services.api_key_service import ApiKeyService, ValidationResult
from services.credential_lease import require_credential_lease
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE, RemoteControlAuthMiddleware
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.mark.asyncio
async def test_successful_local_auth_marks_only_the_copied_server_scope():
    import secrets
    from core.local_auth import LOCAL_AUTH_HEADER
    from transport.local_auth_middleware import (
        AUTHENTICATED_LOCAL_STATE,
        LocalControlAuthMiddleware,
    )
    from tests.http.test_request_body_limit import scope_for, invoke

    reached = []

    async def downstream(scope, _receive, send):
        reached.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    token = secrets.token_urlsafe(32)
    app = LocalControlAuthMiddleware(downstream, token)
    scope = scope_for(
        [
            (LOCAL_AUTH_HEADER.lower().encode(), token.encode()),
            (b"content-type", b"application/json"),
        ],
        "/mcp",
    )
    await invoke(app, scope)
    assert reached[0]["state"][AUTHENTICATED_LOCAL_STATE] is True
    assert AUTHENTICATED_LOCAL_STATE not in scope["state"]
    reached.clear()
    denied = scope_for(
        [(LOCAL_AUTH_HEADER.lower().encode(), b"stale"), (b"content-type", b"application/json")],
        "/mcp",
    )
    denied["state"][AUTHENTICATED_LOCAL_STATE] = True
    frames, _ = await invoke(app, denied)
    assert frames[0]["status"] == 401
    assert not reached


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "terminal",
        "tool_error",
        "inner_running",
        "outer_running",
        "outer_no_reply",
        "invalid_native",
    ],
)
async def test_captured_ordinary_get_test_job_oracle(
    command_hub, monkeypatch, case, record_testsuite_property
):
    """Existing registered tool + real selection/Hub/shared-read/outer middleware."""
    from fastmcp import Client, Context
    from services.tools.run_tests import get_test_job
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    server = FastMCP("ordinary-wait-oracle")
    middleware = UnityInstanceMiddleware()
    server.add_middleware(middleware)
    server.add_middleware(ResponseLimitMiddleware())
    server.tool(get_test_job)

    @server.tool
    async def select_fixture(ctx: Context) -> dict:
        await middleware.set_active_instance(ctx, "Fixture@hash")
        return {"success": True}

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] != "execute" or case == "outer_no_reply":
            return
        response = {
            "success": True,
            "data": {"job_id": case, "status": "failed" if case == "terminal" else "running"},
        }
        if case == "tool_error":
            response = {"success": False, "error": "Existing native job unavailable"}
        elif case == "invalid_native":
            response = {"success": True, "data": {"status": "failed"}}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket, CommandResultMessage(id=message["id"], result=response)
        )

    command_hub.websocket.send_json = answer
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 2 if case == "inner_running" else 0.08)
        result = await client.call_tool_mcp("get_test_job", {"job_id": case, "wait_timeout": 1})
    native = [message for message in command_hub.sent if message["type"] == "execute"]
    assert len(native) == 1
    assert native[0]["name"] == "get_test_job"
    payload = result.structured_content
    record_testsuite_property(
        f"test_captured_ordinary_get_test_job_oracle[{case}].ordinary_result",
        result.model_dump_json(by_alias=True, exclude_none=True),
    )
    if case.startswith("outer_"):
        assert result.is_error is True
        assert payload["data"]["reason"] in {"outcome_unknown", "command_deadline_exceeded"}
        if case == "outer_no_reply":
            assert payload["data"]["reason"] == "outcome_unknown"
            assert payload["data"]["command_id"] == native[0]["id"]
    elif case == "invalid_native":
        assert result.is_error is True
        assert result.content
        assert result.structured_content is None
    elif case == "tool_error":
        assert set(payload) == {"result"}
        assert payload["result"]["error"] == "Existing native job unavailable"
        assert result.is_error is False
    else:
        assert set(payload) == {"result"}
        assert payload["result"]["data"]["status"] == (
            "failed" if case == "terminal" else "running"
        )
        assert result.is_error is False


@asynccontextmanager
async def isolated_tasks(tmp_path, monkeypatch, execute, *, outer_timeout=0.2):
    """Actual auth/SDK/extension, injected bounded wait; this is not main.py."""
    validator = ApiKeyService("https://fixture.invalid/validate", cache_ttl=0)
    monkeypatch.setattr(
        validator,
        "_validate_external",
        AsyncMock(
            side_effect=lambda key: ValidationResult(
                valid=key in {"alice", "bob"}, user_id=key if key in {"alice", "bob"} else None
            )
        ),
    )
    server = FastMCP("isolated-test-wait")
    journal = WaitJournal(tmp_path / "waits")

    async def owner(ctx):
        return ctx.request.scope["state"][AUTHENTICATED_USER_STATE]

    async def capture(params, ctx):
        now = asyncio.get_running_loop().time()
        wall = time.time()
        op = WaitOperation(
            owner=await owner(ctx.request_context._srctx),
            project="project",
            instance="Fixture@project",
            session_id="generation",
            job_id=params.arguments["job_id"],
            wait=DeadlineCheckpoint.capture(0.1, now=wall),
            outer=DeadlineCheckpoint.capture(outer_timeout, now=wall),
        )
        return CapturedWait(
            op,
            now + 0.1,
            now + outer_timeout,
            {
                "content": [],
                "isError": True,
                "structuredContent": {"reason": "ordinary-outer-deadline"},
            },
            credential_lease=await require_credential_lease(),
        )

    extension = TestJobTasks(
        journal, owner=owner, capture=capture, execute=execute, admission=WaitAdmission()
    )
    server.add_middleware(ResponseLimitMiddleware())
    server.add_extension(extension)

    @server.tool
    async def get_test_job(
        job_id: str, wait_timeout: int = 1, include_details: bool = False
    ) -> dict:
        return {"ordinary": True, "job_id": job_id}

    sdk = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    app = RemoteControlAuthMiddleware(WaitTaskDeliveryMiddleware(sdk))
    try:
        async with sdk.router.lifespan_context(sdk):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as client:
                yield client, extension, validator
    finally:
        await validator.aclose()


async def wire(client, method, params, *, owner="alice", opt_in=True, name=None):
    body = _body(method, params)
    if opt_in:
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
            "extensions": {"io.modelcontextprotocol/tasks": {}}
        }
    if name is None:
        name = params.get("taskId") if method.startswith("tasks/") else params.get("name")
    response = await client.post(
        "/mcp",
        headers={
            **_headers(method, name=name),
            "x-api-key": owner,
            "Accept": "application/json, text/event-stream",
        },
        json=body,
    )
    return response, _message(response)


@pytest.mark.asyncio
async def test_isolated_authenticated_task_owner_result_and_per_request_opt_in(
    tmp_path, monkeypatch
):
    started, release = anyio.Event(), anyio.Event()
    calls = []
    ordinary = {
        "resultType": "complete",
        "content": [],
        "structuredContent": {"status": "running"},
        "isError": True,
    }

    async def execute(captured):
        calls.append(captured.operation)
        started.set()
        await release.wait()
        return ordinary

    # This scenario releases execution after multiple authentication probes. Its
    # outcome must follow that event, not race the separate short-deadline fixture.
    async with isolated_tasks(tmp_path, monkeypatch, execute, outer_timeout=30) as (
        client,
        extension,
        _,
    ):
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        assert created["result"]["resultType"] == "task", created
        task_id = created["result"]["taskId"]
        await started.wait()
        _, working = await wire(client, "tasks/get", {"taskId": task_id})
        assert working["result"]["status"] == "working"
        for owner, capability, routing in (
            ("bob", True, None),
            ("alice", False, None),
            ("alice", True, "other"),
        ):
            _, refused = await wire(
                client,
                "tasks/get",
                {"taskId": task_id},
                owner=owner,
                opt_in=capability,
                name=routing,
            )
            assert refused["error"]["code"] == (
                -32021 if not capability else -32020 if routing else -32602
            )
        _, ordinary_call = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
            opt_in=False,
        )
        assert ordinary_call["result"]["structuredContent"]["ordinary"] is True
        release.set()
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        _, completed = await wire(client, "tasks/get", {"taskId": task_id})
        assert completed["result"]["result"] == ordinary
        assert completed["result"]["status"] == "completed"
        assert len(calls) == 1
        _, update = await wire(client, "tasks/update", {"taskId": task_id, "inputResponses": {}})
        assert update["result"]["resultType"] == "complete"
        _, cancelled = await wire(client, "tasks/cancel", {"taskId": task_id})
        assert cancelled["result"]["resultType"] == "complete"
        assert extension.journal.get(task_id, "alice").result == ordinary


@pytest.mark.asyncio
async def test_isolated_wait_cancel_is_intent_until_actual_cleanup(tmp_path, monkeypatch):
    started, cleanup, release = anyio.Event(), anyio.Event(), anyio.Event()

    async def execute(_captured):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            cleanup.set()
            with anyio.CancelScope(shield=True):
                await release.wait()

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = created["result"]["taskId"]
        await started.wait()
        _, ack = await wire(client, "tasks/cancel", {"taskId": task_id})
        assert ack["result"]["resultType"] == "complete"
        await cleanup.wait()
        assert extension.journal.get(task_id, "alice").status == "working"
        assert len(extension.admission.active) == 1
        # Repeating intent must not interrupt cleanup and falsely release its slot.
        await wire(client, "tasks/cancel", {"taskId": task_id})
        assert len(extension.admission.active) == 1
        release.set()
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        assert extension.journal.get(task_id, "alice").status == "cancelled"
        assert not extension.admission.active


@pytest.mark.asyncio
async def test_detached_wait_keeps_original_outer_deadline(tmp_path, monkeypatch):
    calls = []

    async def execute(captured):
        calls.append(captured)
        await anyio.sleep_forever()

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        original = extension.capture

        async def slow_capture(params, ctx):
            captured = await original(params, ctx)
            await anyio.sleep(0.12)
            return captured

        extension.capture = slow_capture
        started = asyncio.get_running_loop().time()
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 60}},
        )
        task_id = created["result"]["taskId"]
        with anyio.fail_after(1):
            while extension._workers:
                await anyio.sleep(0)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 0.3
        assert len(calls) == 1
        record = extension.journal.get(task_id, "alice")
        assert record.status == "completed"
        assert record.result == calls[0].outer_timeout_result


@pytest.mark.asyncio
async def test_sdk_client_claim_and_typed_task_name_encoding(tmp_path, monkeypatch):
    import httpx2
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from mcp.client.extension import ClientExtension, ResultClaim
    from mcp.shared.exceptions import MCPError
    from mcp.shared.inbound import encode_header_value
    from mcp_types import CallToolResult
    from transport.test_job_tasks import GetWaitTaskRequest, GetWaitTaskResult, TaskParams

    headers = []
    final = {
        "resultType": "complete",
        "content": [],
        "structuredContent": {"status": "running"},
        "isError": False,
    }

    async def execute(_captured):
        return final

    class WaitClient(ClientExtension):
        identifier = "io.modelcontextprotocol/tasks"

        def claims(self):
            async def resolve(handle, ctx):
                with anyio.fail_after(2):
                    while True:
                        reply = await ctx.session.send_request(
                            GetWaitTaskRequest(params=TaskParams(taskId=handle.task_id)),
                            GetWaitTaskResult,
                        )
                        wire_result = reply.model_dump(by_alias=True, exclude_none=True)
                        if wire_result["status"] == "completed":
                            return CallToolResult.model_validate(wire_result["result"])
                        await anyio.sleep(0.01)

            return [ResultClaim(result_type="task", model=CreateWaitTaskResult, resolve=resolve)]

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (raw_client, _, _):

        async def observe(request):
            headers.append((request.headers.get("mcp-method"), request.headers.get("mcp-name")))

        def factory(**kwargs):
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=raw_client._transport.app),
                event_hooks={"request": [observe]},
                **kwargs,
            )

        transport = StreamableHttpTransport(
            "http://fixture/mcp", headers={"x-api-key": "alice"}, httpx_client_factory=factory
        )
        async with Client(transport, extensions=[WaitClient()]) as client:
            result = await client.call_tool(
                "get_test_job", {"job_id": "existing", "wait_timeout": 1}
            )
            assert result.structured_content == final["structuredContent"]
            invalid_id = "foreign \u00e9"
            with pytest.raises(MCPError):
                await client.session.send_request(
                    GetWaitTaskRequest(params=TaskParams(taskId=invalid_id)), GetWaitTaskResult
                )
        assert ("tasks/get", encode_header_value(invalid_id)) in headers
        assert any(method == "tasks/get" and name and len(name) == 32 for method, name in headers)
        async with Client(transport, mode="legacy", extensions=[WaitClient()]) as legacy:
            result = await legacy.call_tool(
                "get_test_job", {"job_id": "existing", "wait_timeout": 1}
            )
            assert result.structured_content["ordinary"] is True
            with pytest.raises(MCPError) as error:
                await legacy.session.send_request(
                    GetWaitTaskRequest(params=TaskParams(taskId="missing")), GetWaitTaskResult
                )
            assert error.value.code == -32601


@pytest.mark.asyncio
async def test_jsonrpc_failure_preserves_error_and_journal_capacity_falls_back(
    tmp_path, monkeypatch
):
    from mcp.shared.exceptions import MCPError

    started, release = anyio.Event(), anyio.Event()
    calls = []

    async def execute(captured):
        calls.append(captured)
        started.set()
        await release.wait()
        raise MCPError(-32000, "Ordinary execution interrupted", {"reason": "captured-boundary"})

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        _, first = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = first["result"]["taskId"]
        await started.wait()
        _, fallback = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "second", "wait_timeout": 1}},
        )
        assert fallback["result"]["structuredContent"]["ordinary"] is True
        assert len(extension.admission.active) == 1
        assert len(calls) == 1
        release.set()
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        _, failed = await wire(client, "tasks/get", {"taskId": task_id})
        assert failed["result"]["status"] == "failed"
        assert failed["result"]["error"] == {
            "code": -32000,
            "message": "Ordinary execution interrupted",
            "data": {"reason": "captured-boundary"},
        }
        assert "result" not in failed["result"]


@pytest.mark.asyncio
async def test_task_auth_precedes_lookup_and_sdk_routing_guards(tmp_path, monkeypatch):
    async def execute(_captured):
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        original = extension.journal.get
        lookup = AsyncMock(side_effect=AssertionError("Unauthenticated request reached journal"))
        monkeypatch.setattr(extension.journal, "get", lookup)
        response = await client.post(
            "/mcp",
            headers={
                **_headers("tasks/get", name="missing"),
                "x-api-key": "invalid",
                "Content-Type": "application/json",
            },
            content=b"not json",
        )
        assert response.status_code == 401
        lookup.assert_not_called()
        monkeypatch.setattr(extension.journal, "get", original)
        body = _body("tasks/get", {"taskId": "missing"})
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
            "extensions": {"io.modelcontextprotocol/tasks": {}}
        }
        for names in (
            [],
            [("Mcp-Name", "other")],
            [("Mcp-Name", "=?base64?!!!?=")],
            [("Mcp-Name", "missing"), ("Mcp-Name", "missing")],
        ):
            response = await client.post(
                "/mcp",
                headers=[
                    *_headers("tasks/get").items(),
                    ("x-api-key", "alice"),
                    ("Accept", "application/json, text/event-stream"),
                    *names,
                ],
                json=body,
            )
            message = _message(response)
            assert message["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_fresh_authenticated_client_reads_exact_completed_result_after_reopen(
    tmp_path, monkeypatch
):
    calls = []
    final = {
        "resultType": "complete",
        "content": [{"type": "text", "text": "ordinary"}],
        "structuredContent": {"result": {"data": {"status": "running"}}},
        "_meta": {"fastmcp": {"wrap_result": True}},
        "isError": False,
    }

    async def execute(captured):
        calls.append(captured)
        return final

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = created["result"]["taskId"]
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, _, _):
        _, restored = await wire(client, "tasks/get", {"taskId": task_id})
        assert restored["result"]["result"] == final
        assert restored["result"]["status"] == "completed"
        _, foreign = await wire(client, "tasks/get", {"taskId": task_id}, owner="bob")
        _, absent = await wire(client, "tasks/get", {"taskId": "missing"}, owner="bob")
        assert foreign["error"] == absent["error"]
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_shutdown_during_journal_creation_holds_admission_and_writer(tmp_path, monkeypatch):
    from services.test_wait_journal import JournalUnavailable

    entered, release = threading.Event(), threading.Event()
    calls = []

    async def execute(captured):
        calls.append(captured)
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        original = extension.journal.create

        def held_create(operation):
            entered.set()
            assert release.wait(2)
            return original(operation)

        monkeypatch.setattr(extension.journal, "create", held_create)
        request = asyncio.create_task(
            wire(
                client,
                "tools/call",
                {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
            )
        )
        try:
            with anyio.fail_after(1):
                while not entered.is_set():
                    await anyio.sleep(0)
            await extension.close(grace_seconds=0)
            assert len(extension.admission.active) == 1
            with pytest.raises(JournalUnavailable):
                WaitJournal(extension.journal.root)
        finally:
            release.set()
        _, response = await request
        assert response["error"]["code"] == -32603
        assert not calls
        assert not extension.admission.active
        with WaitJournal(extension.journal.root) as reopened:
            paths = list(reopened.root.glob("*.json"))
            record = reopened.get(paths[0].stem, "alice")
            assert record.status == "failed"


@pytest.mark.asyncio
async def test_shutdown_retains_admitted_cancel_between_lookup_and_commit(tmp_path, monkeypatch):
    entered, release = anyio.Event(), anyio.Event()

    async def execute(_captured):
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = created["result"]["taskId"]
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        lookup = extension._lookup

        async def between_lookup_and_commit(*args):
            record = await lookup(*args)
            entered.set()
            await release.wait()
            return record

        monkeypatch.setattr(extension, "_lookup", between_lookup_and_commit)
        request = asyncio.create_task(wire(client, "tasks/cancel", {"taskId": task_id}))
        try:
            with anyio.fail_after(1):
                await entered.wait()
            await extension.close(grace_seconds=0)
            assert extension._io_owners == 0
            assert extension._method_owners == 1
            assert extension.journal._writer is not None
            assert extension.delivery_admission.active
        finally:
            release.set()
        _, ack = await request
        assert ack["result"]["resultType"] == "complete"
        assert extension._method_owners == 0
        assert extension.journal._writer is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"wait_timeout": 0},
        {"wait_timeout": -1},
        {"wait_timeout": 1.5},
        {"wait_timeout": 1, "include_details": True},
        {"wait_timeout": 1, "page_size": 0},
        {"wait_timeout": 1, "cursor": ""},
    ],
)
async def test_ineligible_arguments_do_not_create_or_execute_wait(tmp_path, monkeypatch, arguments):
    calls = []

    async def execute(captured):
        calls.append(captured)
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", **arguments}},
        )
        assert not extension._workers
        assert not list(extension.journal.root.glob("*.json"))
        assert not calls


@pytest.mark.asyncio
async def test_task_result_delivery_is_held_through_shielded_asgi_send(tmp_path, monkeypatch):
    from tests.http.test_request_body_limit import scope_for
    from transport.test_job_tasks import WaitDeliveryAdmission

    async def execute(_captured):
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        extension.delivery_admission = WaitDeliveryAdmission()
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = created["result"]["taskId"]
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        body = _body("tasks/get", {"taskId": task_id})
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
            "extensions": {"io.modelcontextprotocol/tasks": {}}
        }
        headers = [
            (key.lower().encode(), value.encode())
            for key, value in _headers("tasks/get", name=task_id).items()
        ]
        headers += [
            (b"x-api-key", b"alice"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
        ]
        entered, release, done = anyio.Event(), anyio.Event(), anyio.Event()
        cancel = anyio.CancelScope()
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": json.dumps(body).encode()}
            await anyio.sleep_forever()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                entered.set()
                with anyio.CancelScope(shield=True):
                    await release.wait()

        async def held_request():
            try:
                with cancel:
                    await client._transport.app(scope_for(headers, "/mcp"), receive, send)
            finally:
                done.set()

        async with anyio.create_task_group() as group:
            group.start_soon(held_request)
            with anyio.fail_after(2):
                await entered.wait()
            cancel.cancel()
            await anyio.wait_all_tasks_blocked()
            assert not done.is_set()
            assert extension.delivery_admission.active
            _, refused = await wire(client, "tasks/get", {"taskId": task_id})
            assert refused["error"]["code"] == -32603
            assert "delivery capacity" in refused["error"]["message"]
            release.set()
            with anyio.fail_after(2):
                await done.wait()
        assert not extension.delivery_admission.active
        _, healthy = await wire(client, "tasks/get", {"taskId": task_id})
        assert healthy["result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_request_cancel_cannot_abandon_journal_thread_or_its_admission(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    async def execute(_captured):
        return {"content": []}

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        original = extension.journal.create

        def held_create(operation):
            entered.set()
            assert release.wait(2), "test must release blocked journal writer"
            return original(operation)

        monkeypatch.setattr(extension.journal, "create", held_create)
        request = asyncio.create_task(
            wire(
                client,
                "tools/call",
                {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
            )
        )
        try:
            with anyio.fail_after(1):
                while not entered.is_set():
                    await anyio.sleep(0)
            request.cancel()
            await anyio.wait_all_tasks_blocked()
            assert not request.done()
            assert len(extension.admission.active) == 1
        finally:
            release.set()
        try:
            await request
        except asyncio.CancelledError:
            pass
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        assert not extension.admission.active
        assert len(list(extension.journal.root.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_retired_noncooperative_wait_keeps_worker_and_writer_charges(tmp_path, monkeypatch):
    from services.test_wait_journal import JournalCapacity, JournalUnavailable

    entered, cleanup, release = anyio.Event(), anyio.Event(), anyio.Event()

    async def execute(_captured):
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            cleanup.set()
            with anyio.CancelScope(shield=True):
                await release.wait()

    async with isolated_tasks(tmp_path, monkeypatch, execute) as (client, extension, _):
        extension.admission = WaitAdmission(total=1)
        _, created = await wire(
            client,
            "tools/call",
            {"name": "get_test_job", "arguments": {"job_id": "existing", "wait_timeout": 1}},
        )
        task_id = created["result"]["taskId"]
        await entered.wait()
        operation = extension.journal.get(task_id, "alice").operation
        await extension.close(grace_seconds=0)
        await cleanup.wait()
        assert len(extension.admission.active) == 1
        assert task_id in extension._workers
        with pytest.raises(JournalCapacity):
            extension.admission.acquire(operation)
        with pytest.raises(JournalUnavailable):
            WaitJournal(extension.journal.root)
        release.set()
        with anyio.fail_after(2):
            while extension._workers:
                await anyio.sleep(0)
        assert not extension.admission.active
        with WaitJournal(extension.journal.root) as reopened:
            record = reopened.get(task_id, "alice")
            assert record.status == "failed"
            assert "shutdown" in record.error["message"]


class BareTask(Result):
    result_type: Literal["task"] = Field(default="task", alias="resultType")
    task_id: str = Field(default="fixture-task", alias="taskId")
    status: Literal["working"] = "working"
    created_at: str = Field(default="2026-10-17T00:00:00Z", alias="createdAt")
    last_updated_at: str = Field(default="2026-10-17T00:00:00Z", alias="lastUpdatedAt")
    ttl_ms: int = Field(default=3_600_000, alias="ttlMs")


class BareTaskExtension(ServerExtension):
    identifier = "io.modelcontextprotocol/tasks"

    async def intercept_tool_call(self, params, context, call_next):
        return CreateWaitTaskResult(
            taskId="fixture-task",
            status="working",
            createdAt="2026-10-17T00:00:00Z",
            lastUpdatedAt="2026-10-17T00:00:00Z",
            ttlMs=3_600_000,
        )


@pytest.mark.asyncio
async def test_existing_response_middleware_accepts_sdk_bare_task_result():
    # This mounts the existing middleware and public extension API, not main.py.
    server = FastMCP("wait-task-seam")
    server.add_middleware(ResponseLimitMiddleware())
    server.add_extension(BareTaskExtension())

    @server.tool
    async def get_test_job() -> dict:
        pytest.fail("The extension must intercept before native execution")

    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/mcp",
                headers={
                    **_headers("tools/call", name="get_test_job"),
                    "Accept": "application/json, text/event-stream",
                },
                json=_body("tools/call", {"name": "get_test_job", "arguments": {}}),
            )
    message = _message(response)
    assert "error" not in message, message
    assert message["result"]["resultType"] == "task"
    assert message["result"]["taskId"] == "fixture-task"


@pytest.mark.asyncio
async def test_trusted_task_result_uses_existing_byte_ceiling(monkeypatch):
    from transport import response_limit_middleware as module

    result = await BareTaskExtension().intercept_tool_call(None, None, None)
    size = len(
        json.dumps(result.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False).encode()
    )

    async def following(_context):
        return result

    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", size + 32_768)
    assert await ResponseLimitMiddleware().on_call_tool(None, following) is result
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", size + 32_767)
    refused = await ResponseLimitMiddleware().on_call_tool(None, following)
    assert refused.is_error
    assert refused.structured_content["data"]["reason"] == "response_payload_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unrelated", "subclass"])
async def test_untrusted_model_does_not_enter_exact_task_branch(kind):
    class UntrustedTask(CreateWaitTaskResult):
        pass

    result = (
        BareTask()
        if kind == "unrelated"
        else UntrustedTask(
            taskId="fixture",
            status="working",
            createdAt="2026-10-17T00:00:00Z",
            lastUpdatedAt="2026-10-17T00:00:00Z",
            ttlMs=1000,
        )
    )

    async def following(_context):
        return result

    with pytest.raises(AttributeError):
        await ResponseLimitMiddleware().on_call_tool(None, following)
