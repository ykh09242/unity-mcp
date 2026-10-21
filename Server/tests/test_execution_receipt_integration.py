"""Real SDK HTTP + durable receipt integration, with controlled application handlers.

No Unity endpoint is opened. Native worker tests below exercise real local workers;
they do not imply an actual Unity job or editor side effect was validated.
"""

import asyncio
from contextlib import asynccontextmanager
from threading import Event
from types import SimpleNamespace
from typing import Annotated

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp_types import CallToolResult, ImageContent, TextContent
from pydantic import AfterValidator, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from models.response_limits import ResponseOwner, response_owner
from services.execution_receipt_context import execution_owner, owned_to_thread
from services.execution_receipt_context import observe_native_result
from services.execution_receipt_identity import ReceiptConflict, ReceiptOptions, ReceiptUnavailable
from services.execution_receipts import ExecutionReceiptStore
from services.operation_permissions import OperationGrants, Permission
from transport.execution_receipts import (
    META_KEY,
    ExecutionReceipts,
    PreparedExecution,
    execute_receipted_rest,
    install_execution_receipts,
)
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE, LocalControlAuthMiddleware
from transport.response_limit_middleware import ResponseLimitMiddleware, ResponseRetentionMiddleware
from transport.unity_instance_middleware import UnityInstanceMiddleware

PROJECT = "0123456789abcdef"
KEY = "controlled-request-key-0001"


def options(runtime, *, key=KEY, retain=True, project=PROJECT):
    return {
        "version": 1,
        "store_id": runtime.store.store_id,
        "project_hash": project,
        "idempotency_key": key,
        "retain_result": retain,
    }


@asynccontextmanager
async def sdk_wire(
    tmp_path, monkeypatch, protocol="2026-07-28", *, enabled=True, tasks=False, scene=False
):
    """Use the installed public SDK wire adapter and actual receipt middleware/store."""
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    from transport.scene_load_inputs import SceneLoadInputs
    from transport.test_wait_binding import install_test_job_tasks

    scene_inputs = SceneLoadInputs(remote_hosted=False) if scene else None
    server = FastMCP(
        "receipt-integration",
        **({"request_state_security": scene_inputs.policy} if scene_inputs is not None else {}),
    )
    runtime = install_execution_receipts(
        server, tmp_path / "receipts" if enabled else None, private_root=enabled
    )
    counts = {"validator": 0, "default": 0, "body": 0}

    def validate(value):
        counts["validator"] += 1
        return value

    def default():
        counts["default"] += 1
        return 17

    @server.tool(name="receipt_exercise")
    async def exercise(
        checked: Annotated[int, AfterValidator(validate)],
        count: int = Field(default_factory=default),
        as_error: bool = False,
        native_failure: bool = False,
    ) -> ToolResult:
        counts["body"] += 1
        return ToolResult.from_mcp_result(
            CallToolResult(
                content=[
                    TextContent(type="text", text="application output"),
                    ImageContent(type="image", data="AA==", mimeType="image/png"),
                ],
                structuredContent=(
                    {"success": False, "code": "native_refused", "data": {"applied": False}}
                    if native_failure
                    else {"checked": checked, "count": count}
                ),
                isError=as_error,
                _meta={"application": {"sensitive_example": "explicit retained output"}},
            )
        )

    middleware = UnityInstanceMiddleware()

    @server.tool(name="get_test_job")
    async def wait(job_id: str, wait_timeout: int = 1) -> ToolResult:
        counts["body"] += 1
        return ToolResult(structured_content={"job_id": job_id, "status": "running"})

    @server.tool(name="manage_scene")
    async def scene_load(action: str, path: str | None = None) -> ToolResult:
        counts["body"] += 1
        return ToolResult(structured_content={"action": action, "path": path})

    server.tool(name="scene_lifecycle")(scene_load)

    async def select(context, **kwargs):
        # Controlled project selection only; ordinary receipt+SDK execution is real.
        context.message.arguments.pop("unity_instance", None)
        await context.fastmcp_context.set_state(
            "unity_instance", "Controlled@" + PROJECT, serializable=False
        )

    monkeypatch.setattr(middleware, "_inject_unity_instance", select)
    server.add_middleware(ResponseLimitMiddleware())
    server.add_middleware(middleware)
    if scene_inputs is not None:
        server.add_middleware(scene_inputs)
    if tasks:
        installed = install_test_job_tasks(server, tmp_path / "wait-tasks", remote_hosted=False)
        assert installed is not None
    app = server.http_app(path="/mcp", json_response=True)
    app.add_middleware(ResponseRetentionMiddleware)
    app.add_middleware(LocalControlAuthMiddleware, token="controlled-auth")
    await runtime.start()
    session = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://owned.test",
                headers={
                    LOCAL_AUTH_HEADER: "controlled-auth",
                    "Accept": "application/json, text/event-stream",
                },
            ) as client:

                async def exchange(method, params, identifier="call"):
                    nonlocal session
                    headers = {"MCP-Protocol-Version": protocol}
                    if session:
                        headers["Mcp-Session-Id"] = session
                    if protocol == "2026-07-28":
                        headers["Mcp-Method"] = method
                        if method == "tools/call":
                            headers["Mcp-Name"] = params["name"]
                            params = {
                                **params,
                                "_meta": {
                                    "io.modelcontextprotocol/protocolVersion": protocol,
                                    "io.modelcontextprotocol/clientCapabilities": {},
                                    "io.modelcontextprotocol/clientInfo": {
                                        "name": "receipt-test",
                                        "version": "1",
                                    },
                                    **params.get("_meta", {}),
                                },
                            }
                    payload = {"jsonrpc": "2.0", "method": method, "params": params}
                    if identifier is not None:
                        payload["id"] = identifier
                    response = await client.post("/mcp", headers=headers, json=payload)
                    session = response.headers.get("mcp-session-id", session)
                    assert response.status_code in {200, 202}, response.text
                    return response.json() if identifier is not None else None

                if protocol == "2025-11-25":
                    await exchange(
                        "initialize",
                        {
                            "protocolVersion": protocol,
                            "capabilities": {},
                            "clientInfo": {"name": "receipt-test", "version": "1"},
                        },
                    )
                    await exchange("notifications/initialized", {}, None)
                yield runtime, counts, exchange
    finally:
        runtime.close()
        if scene_inputs is not None:
            scene_inputs.close()
    assert runtime.store is None


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
@pytest.mark.parametrize("as_error", [False, True])
async def test_same_key_replays_real_wire_without_callbacks_or_handler(
    tmp_path, monkeypatch, protocol, as_error
):
    # Given a real SDK endpoint, actual private ledger and a controlled typed handler.
    async with sdk_wire(tmp_path, monkeypatch, protocol) as (runtime, counts, call):
        params = {
            "name": "receipt_exercise",
            "arguments": {"checked": 4, "as_error": as_error},
            "_meta": {META_KEY: options(runtime)},
        }
        # When the exact request is repeated after the first final response.
        first = await call("tools/call", params, "initial")
        replay = await call("tools/call", params, "replay")
        # Then content, structured content, application metadata and wire error survive.
        assert counts == {"body": 1, "validator": 1, "default": 1}
        original, repeated = first["result"], replay["result"]
        assert original["content"] == repeated["content"]
        assert original["structuredContent"] == repeated["structuredContent"]
        assert original["isError"] is repeated["isError"] is as_error
        assert original["_meta"]["application"] == repeated["_meta"]["application"]
        assert repeated["_meta"][META_KEY]["replayed"] is True


@pytest.mark.asyncio
async def test_same_key_explicit_default_conflicts_before_callbacks(tmp_path, monkeypatch):
    # Given a completed request whose parsed arguments omitted a defaulted field.
    async with sdk_wire(tmp_path, monkeypatch) as (runtime, counts, call):
        meta = {META_KEY: options(runtime)}
        await call(
            "tools/call", {"name": "receipt_exercise", "arguments": {"checked": 4}, "_meta": meta}
        )
        # When the same key explicitly supplies the eventual default value.
        reply = await call(
            "tools/call",
            {"name": "receipt_exercise", "arguments": {"checked": 4, "count": 17}, "_meta": meta},
        )
        # Then conservative exact-request identity refuses a second callback/body.
        assert reply["result"]["isError"] is True
        assert reply["result"]["content"] == [{"type": "text", "text": "idempotency_conflict"}]
        assert counts == {"body": 1, "validator": 1, "default": 1}


@pytest.mark.asyncio
async def test_metadata_only_duplicate_is_error_and_never_redispatches(tmp_path, monkeypatch):
    # Given explicitly metadata-only completed execution.
    async with sdk_wire(tmp_path, monkeypatch) as (runtime, counts, call):
        params = {
            "name": "receipt_exercise",
            "arguments": {"checked": 4},
            "_meta": {META_KEY: options(runtime, retain=False)},
        }
        await call("tools/call", params)
        # When the client retries after losing its response.
        reply = await call("tools/call", params, "duplicate")
        # Then it receives a truthful unavailable-result error and the same receipt.
        assert reply["result"]["isError"] is True
        assert reply["result"]["_meta"][META_KEY]["result_unavailable_reason"] == "not_requested"
        assert counts["body"] == 1


@pytest.mark.asyncio
async def test_disabled_explicit_option_refuses_before_handler(tmp_path, monkeypatch):
    # Given an ordinary server with receipt storage disabled.
    async with sdk_wire(tmp_path, monkeypatch, enabled=False) as (_, counts, call):
        # When a client explicitly requests protection rather than opting out.
        reply = await call(
            "tools/call",
            {
                "name": "receipt_exercise",
                "arguments": {"checked": 4},
                "_meta": {META_KEY: {"version": 1, "store_id": "0" * 32, "project_hash": PROJECT}},
            },
        )
        # Then no validation callback/default/body is run.
        assert reply["result"]["isError"] is True
        assert reply["result"]["content"] == [
            {"type": "text", "text": "execution_receipts_unavailable"}
        ]
        assert counts == {"body": 0, "validator": 0, "default": 0}


@pytest.mark.asyncio
async def test_control_lookup_is_offline_and_authorized_without_selection(tmp_path, monkeypatch):
    # Given a completed receipt and a selection function that must not be called by lookup.
    async with sdk_wire(tmp_path, monkeypatch) as (runtime, counts, call):
        initial = await call(
            "tools/call",
            {
                "name": "receipt_exercise",
                "arguments": {"checked": 4},
                "_meta": {META_KEY: options(runtime)},
            },
        )
        rid = initial["result"]["_meta"][META_KEY]["receipt_id"]
        # When the server-only control tool retrieves history using exact store/project/ID.
        reply = await call(
            "tools/call",
            {
                "name": "get_execution_receipt",
                "arguments": {
                    "action": "get",
                    "store_id": runtime.store.store_id,
                    "project_hash": PROJECT,
                    "receipt_id": rid,
                    "include_result": True,
                },
            },
        )
        # Then the stored typed output is returned without another application call.
        assert reply["result"]["structuredContent"]["result"]["structuredContent"] == {
            "checked": 4,
            "count": 17,
        }
        assert counts["body"] == 1


@pytest.mark.asyncio
async def test_actual_tasks_adapter_keeps_receipted_wait_on_ordinary_path(tmp_path, monkeypatch):
    # Given the actual installed wait-Tasks extension and an eligible public wait shape.
    async with sdk_wire(tmp_path, monkeypatch, tasks=True) as (runtime, counts, call):
        # When the extension-capable client explicitly requests a receipt.
        reply = await call(
            "tools/call",
            {
                "name": "get_test_job",
                "arguments": {
                    "job_id": "controlled-job",
                    "wait_timeout": 1,
                },
                "_meta": {
                    META_KEY: options(runtime),
                    "io.modelcontextprotocol/clientCapabilities": {
                        "extensions": {"io.modelcontextprotocol/tasks": {}},
                    },
                },
            },
        )
        # Then actual ordinary middleware/store/body run and no Task handle is returned.
        assert reply["result"]["resultType"] == "complete"
        assert reply["result"]["_meta"][META_KEY]["status"] == "completed"
        assert counts["body"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["manage_scene", "scene_lifecycle"])
async def test_actual_scene_adapter_refuses_receipted_ask_before_body(
    tmp_path, monkeypatch, tool_name
):
    # Given the actual SceneLoadInputs adapter and an initial missing-path scene load.
    async with sdk_wire(tmp_path, monkeypatch, scene=True) as (runtime, counts, call):
        # When receipt protection would otherwise cross an interactive MRTR ask.
        reply = await call(
            "tools/call",
            {
                "name": tool_name,
                "arguments": {"action": "load"},
                "_meta": {META_KEY: options(runtime)},
            },
        )
        # Then the actual adapter refuses before controlled handler effects or input ask.
        assert reply["result"]["isError"] is True
        assert reply["result"]["content"] == [
            {"type": "text", "text": "execution_receipt_scene_input_unsupported"}
        ]
        assert counts["body"] == 0


def request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/command",
            "headers": [],
            "state": {AUTHENTICATED_LOCAL_STATE: True},
        }
    )


async def settle_task(task):
    if not task.done():
        task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)


@pytest.mark.asyncio
async def test_rest_route_and_historical_root_conflict_before_live_forward(tmp_path, monkeypatch):
    # Given a completed context-command with its exact historical root.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    owner = ResponseOwner()
    token = response_owner.set(owner)
    calls = []

    async def forward(req):
        calls.append(await req.json())
        return JSONResponse({"success": True, "data": "original"}, status_code=201)

    body = {
        "type": "ping",
        "params": {},
        "unity_instance": PROJECT,
        "expected_project": {"root": str(tmp_path)},
        "execution": options(runtime),
    }
    try:
        original = await execute_receipted_rest(
            runtime, request(), body, "/api/context-command", forward
        )
        # When route or exact expected root changes while reusing the retained key.
        changed_root = {**body, "expected_project": {"root": str(tmp_path / "different")}}
        with pytest.raises(ReceiptConflict):
            await execute_receipted_rest(
                runtime, request(), changed_root, "/api/context-command", forward
            )
        plain = {key: value for key, value in body.items() if key != "expected_project"}
        with pytest.raises(ReceiptConflict):
            await execute_receipted_rest(runtime, request(), plain, "/api/command", forward)
        # Then neither conflicting contract reaches live selection/forwarding.
        assert original.status_code == 201
        assert len(calls) == 1
    finally:
        response_owner.reset(token)
        owner.release()
        runtime.close()


@pytest.mark.asyncio
async def test_real_worker_keeps_key_and_input_charge_after_wrapper_cancellation(
    tmp_path, monkeypatch
):
    # Given a real executor operation held behind a bounded explicit barrier.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    started, release = Event(), Event()
    settled = asyncio.Event()
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime, retain=False)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )
    assert isinstance(prepared, PreparedExecution)
    prepared.memory.on_release.append(settled.set)

    def blocking():
        started.set()
        assert release.wait(5)

    async def invoke():
        await owned_to_thread(blocking)
        return ToolResult(structured_content={"success": True})

    async def wrapper():
        try:
            return await prepared.execute(invoke)
        finally:
            prepared.close()

    task = asyncio.create_task(wrapper())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # When the public wrapper is cancelled while the actual worker remains blocked.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Then UNKNOWN pins the key/store/input graph through real worker completion.
        assert runtime.store.active_count == 1
        assert prepared.memory.entries
        scope = prepared.authority.scope
        view = runtime.store.get(
            scope,
            store_id=runtime.store.store_id,
            idempotency_key=KEY,
            grants=prepared.authority.grants,
        )
        assert view.status == "unknown"
    finally:
        release.set()
        # The actual Future callback releases this exact memory owner.
        await asyncio.wait_for(settled.wait(), timeout=2)
        response_owner.reset(token)
        delivery.release()
        runtime.close()
    assert prepared.memory.released


@pytest.mark.asyncio
async def test_weaker_and_retired_same_owner_cannot_retrieve_retained_output(tmp_path, monkeypatch):
    # Given one stable owner whose historical operation required edit+run.
    from services.execution_receipt_identity import ReceiptNotFound
    import transport.execution_receipts as module

    monkeypatch.setattr(config, "http_remote_hosted", True)
    state = {"permissions": frozenset({Permission.EDIT, Permission.RUN}), "retired": False}

    async def check():
        if state["retired"]:
            raise ReceiptNotFound("receipt_not_found")

    def check_now():
        if state["retired"]:
            raise ReceiptNotFound("receipt_not_found")

    async def lease():
        await check()
        return SimpleNamespace(
            user_id="owner",
            permissions=OperationGrants(state["permissions"], True),
            check=check,
            check_now=check_now,
        )

    monkeypatch.setattr(module, "require_credential_lease", lease)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    try:
        prepared = await runtime.prepare(
            options=ReceiptOptions.parse(options(runtime)),
            surface="mcp-tool",
            name="custom",
            arguments={},
            semantic_context={},
            policy_params={},
            custom=True,
        )

        async def body():
            return ToolResult(structured_content={"private": "retained"})

        await prepared.execute(body)
        prepared.close()
        state["permissions"] = frozenset({Permission.READ})
        # When the same owner presents weaker or retired authority.
        with pytest.raises(ReceiptNotFound):
            await runtime.control(
                action="get",
                project_hash=PROJECT,
                store_id=runtime.store.store_id,
                receipt_id=None,
                idempotency_key=KEY,
                include_result=True,
            )
        state["retired"] = True
        with pytest.raises(ReceiptNotFound):
            await runtime.control(
                action="get",
                project_hash=PROJECT,
                store_id=runtime.store.store_id,
                receipt_id=None,
                idempotency_key=KEY,
                include_result=True,
            )
    finally:
        response_owner.reset(token)
        delivery.release()
        runtime.close()


@pytest.mark.asyncio
async def test_cancel_during_real_admission_adopts_intent_without_entering_handler(
    tmp_path, monkeypatch
):
    # Given actual durable admission held in its owned journal thread.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    entered, release = Event(), Event()
    actual_admit = ExecutionReceiptStore.admit

    def blocked_admit(store, *args, **kwargs):
        entered.set()
        assert release.wait(3)
        return actual_admit(store, *args, **kwargs)

    monkeypatch.setattr(ExecutionReceiptStore, "admit", blocked_admit)
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime, retain=False)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )
    effects = []

    async def body():
        effects.append("must-not-run-before-first-await")
        return ToolResult(structured_content={"success": True})

    async def invoke():
        try:
            return await prepared.execute(body)
        finally:
            prepared.close()

    task = asyncio.create_task(invoke())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # When cancellation precedes admission return, including a non-yielding body.
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        # Then the adopted intent settles as not_dispatched with no orphan wrapper.
        assert effects == []
        assert runtime.store.active_count == 0
        view = runtime.store.get(
            prepared.authority.scope,
            store_id=runtime.store.store_id,
            idempotency_key=KEY,
            grants=prepared.authority.grants,
        )
        assert view.status == "not_dispatched"
    finally:
        release.set()
        await settle_task(task)
        response_owner.reset(token)
        delivery.release()
        runtime.close()
    assert runtime.store is None


@pytest.mark.asyncio
async def test_native_journal_wait_keeps_owner_loop_live_and_cancel_prevents_send(
    tmp_path, monkeypatch
):
    # Given actual receipt native journaling blocked in an owned thread.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime, retain=False)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )
    writing, release = Event(), Event()
    original_write = runtime.store._files.write
    armed = False

    def blocked_write(*args):
        if armed:
            writing.set()
            assert release.wait(3)
        return original_write(*args)

    monkeypatch.setattr(runtime.store._files, "write", blocked_write)
    sends = []

    async def body():
        nonlocal armed
        armed = True
        owner = execution_owner.get()
        await owner.native_async(
            "ping",
            {},
            custom=False,
            kind="http",
            attempt_id="attempt-1",
            session_id="owned-session",
            generation="generation-1",
            project_hash=PROJECT,
        )
        owner.dispatch_started(
            kind="http",
            attempt_id="attempt-1",
            session_id="owned-session",
            generation="generation-1",
        )
        sends.append("native-send-boundary")
        return ToolResult(structured_content={"success": True})

    async def invoke():
        try:
            return await prepared.execute(body)
        finally:
            prepared.close()

    task = asyncio.create_task(invoke())
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        # When the wrapper is cancelled while another owner-loop task still progresses.
        task.cancel()
        progress = asyncio.Event()
        asyncio.get_running_loop().call_soon(progress.set)
        await asyncio.wait_for(progress.wait(), timeout=0.5)
        assert runtime.store.active_count == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        # Then no native boundary is reached, and the durable uncertainty never resends.
        assert sends == []
        view = runtime.store.get(
            prepared.authority.scope,
            store_id=runtime.store.store_id,
            idempotency_key=KEY,
            grants=prepared.authority.grants,
        )
        assert view.status == "unknown"
        assert not any(attempt.dispatched for attempt in view.attempts)
    finally:
        release.set()
        await settle_task(task)
        response_owner.reset(token)
        delivery.release()
        runtime.close()


@pytest.mark.asyncio
async def test_full_pin_budget_still_stops_dispatch_before_async_final_commit(
    tmp_path, monkeypatch
):
    # Given 64 explicitly held ownership capabilities and a gated actual final write.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime, retain=False)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )
    writing, release = Event(), Event()
    original_write = runtime.store._files.write
    pins = []
    armed = False

    def blocked_write(*args):
        if armed:
            writing.set()
            assert release.wait(3)
        return original_write(*args)

    monkeypatch.setattr(runtime.store._files, "write", blocked_write)

    async def body():
        nonlocal armed
        pins.extend(prepared.acquire_worker() for _ in range(64))
        armed = True
        return ToolResult(structured_content={"success": True})

    async def invoke():
        try:
            return await prepared.execute(body)
        finally:
            prepared.close()

    task = asyncio.create_task(invoke())
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        # When finalization cannot acquire its additional native pin.
        assert not task.done()
        assert prepared.stopping
        with pytest.raises(ReceiptUnavailable):
            prepared.dispatch_started(
                kind="http", attempt_id="late", session_id="owned", generation="generation"
            )
        # Then the owner loop remains live and no new dispatch can bypass stopping.
        release.set()
        result = await asyncio.wait_for(task, timeout=3)
        assert result.meta[META_KEY]["status"] == "completed"
        assert runtime.store.active_count == 1
    finally:
        release.set()
        await settle_task(task)
        for pin in pins:
            pin.close()
        response_owner.reset(token)
        delivery.release()
        runtime.close()
    assert prepared.memory.released


@pytest.mark.asyncio
async def test_discarded_core01_unknown_remains_unknown_in_public_receipt(tmp_path, monkeypatch):
    # Given a wrapper that observes native uncertainty but returns its own success body.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )

    async def body():
        observe_native_result({"success": False, "data": {"reason": "outcome_unknown"}})
        return ToolResult(structured_content={"success": True, "wrapper": "observed"})

    try:
        # When the ordinary final response hides that internal native failure.
        result = await prepared.execute(body)
        # Then original application output stays intact but execution remains UNKNOWN.
        assert result.structured_content["success"] is True
        assert result.meta[META_KEY]["status"] == "unknown"
        assert result.meta[META_KEY]["result_available"] is False
    finally:
        prepared.close()
        response_owner.reset(token)
        delivery.release()
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seam",
    [
        "hub-timeout",
        "hub-permission",
        "hub-authentication",
        "transport-authentication",
        "transport-exception",
    ],
)
async def test_outer_transport_unknown_cannot_be_hidden_by_handler(tmp_path, monkeypatch, seam):
    # Given a real receipt around an actual outer transport error-construction path.
    import transport.plugin_hub as hub_module
    import transport.unity_transport as transport_module
    from services.credential_lease import CredentialLeaseError
    from services.operation_permissions import OperationPermissionError

    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    prepared = await runtime.prepare(
        options=ReceiptOptions.parse(options(runtime)),
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )

    async def body():
        if seam.startswith("hub-"):
            errors = {
                "hub-timeout": TimeoutError,
                "hub-permission": OperationPermissionError,
                "hub-authentication": CredentialLeaseError,
            }

            async def fail_after_dispatch(*args, **kwargs):
                kwargs["budget"].dispatched = True
                raise errors[seam]()

            monkeypatch.setattr(
                hub_module.PluginHub, "_send_command_for_instance", fail_after_dispatch
            )
            result = await hub_module.PluginHub.send_command_for_instance(
                "Controlled@" + PROJECT, "ping", {}
            )
        else:
            monkeypatch.setattr(config, "transport_mode", "http")
            monkeypatch.setattr(config, "http_remote_hosted", True)

            async def ended():
                raise CredentialLeaseError()

            async def authority(user_id):
                return SimpleNamespace(user_id="controlled", check=ended)

            async def returned(*args, **kwargs):
                if seam == "transport-exception":
                    raise RuntimeError("controlled outer failure")
                return {"success": True, "data": {}}

            monkeypatch.setattr(transport_module, "require_credential_lease", authority)
            monkeypatch.setattr(hub_module.PluginHub, "send_command_for_instance", returned)
            result = await transport_module.send_with_unity_instance(
                None, "Controlled@" + PROJECT, "ping", {}
            )
        data = result.data if hasattr(result, "data") else result["data"]
        assert data["reason"] == "outcome_unknown"
        # When the application handles that canonical error and supplies its own body.
        return ToolResult(structured_content={"success": True})

    try:
        result = await prepared.execute(body)
        # Then the public body cannot upgrade known execution uncertainty to completion.
        assert result.structured_content["success"] is True
        assert result.meta[META_KEY]["status"] == "unknown"
        assert result.meta[META_KEY]["result_available"] is False
    finally:
        prepared.close()
        response_owner.reset(token)
        delivery.release()
        runtime.close()


@pytest.mark.asyncio
async def test_duplicate_metadata_decode_refuses_before_lookup_when_memory_full(
    tmp_path, monkeypatch
):
    # Given a real completed key and room for its tiny request, but not metadata decode.
    import transport.execution_receipts as module
    from models.response_limits import MAX_RESPONSE_RETAINED_BYTES, response_size

    monkeypatch.setattr(config, "http_remote_hosted", False)
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    initial_owner = ResponseOwner()
    token = response_owner.set(initial_owner)
    opts = ReceiptOptions.parse(options(runtime, retain=False))
    prepared = await runtime.prepare(
        options=opts,
        surface="mcp-tool",
        name="ping",
        arguments={},
        semantic_context={},
        policy_params={},
        request=request(),
    )

    async def body():
        return ToolResult(structured_content={"success": True})

    await prepared.execute(body)
    prepared.close()
    initial_owner.release()
    delivery, filler = ResponseOwner(), ResponseOwner()
    response_owner.set(delivery)
    charge = response_size({"arguments": {}, "context": {}})
    assert charge is not None
    module._working.reserve(MAX_RESPONSE_RETAINED_BYTES - charge - 1, filler)
    calls = []
    original = runtime.store.lookup

    def observed_lookup(*args, **kwargs):
        calls.append("lookup")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.store, "lookup", observed_lookup)
    try:
        # When the same tiny request would require historical metadata read/decode.
        with pytest.raises(ReceiptUnavailable, match="execution_receipt_working_capacity"):
            await runtime.prepare(
                options=opts,
                surface="mcp-tool",
                name="ping",
                arguments={},
                semantic_context={},
                policy_params={},
                request=request(),
            )
        # Then admission refuses before the stored lookup graph is allocated.
        assert calls == []
    finally:
        filler.release()
        delivery.release()
        response_owner.reset(token)
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
async def test_focused_scene_operation_is_receipted_and_replayed_once(
    tmp_path, monkeypatch, protocol
):
    async with sdk_wire(tmp_path, monkeypatch, protocol) as (runtime, counts, call):
        params = {
            "name": "scene_lifecycle",
            "arguments": {"action": "load", "path": "Assets/Scenes/A.unity"},
            "_meta": {META_KEY: options(runtime)},
        }
        first = (await call("tools/call", params, "focused-first"))["result"]
        replay = (await call("tools/call", params, "focused-replay"))["result"]
        assert first["structuredContent"] == {"action": "load", "path": "Assets/Scenes/A.unity"}
        assert replay["structuredContent"] == first["structuredContent"]
        assert first["_meta"][META_KEY]["status"] == "completed"
        assert counts["body"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
async def test_native_failure_is_stored_and_replayed_without_reexecution(
    tmp_path, monkeypatch, protocol
):
    # Given a real SDK endpoint and private ledger receiving a native failure envelope.
    async with sdk_wire(tmp_path, monkeypatch, protocol) as (runtime, counts, call):
        params = {
            "name": "receipt_exercise",
            "arguments": {"checked": 4, "native_failure": True},
            "_meta": {META_KEY: options(runtime)},
        }
        # When a retained result is returned, replayed and retrieved from the durable store.
        first = await call("tools/call", params, "first")
        replay = await call("tools/call", params, "replay")
        rid = first["result"]["_meta"][META_KEY]["receipt_id"]
        lookup = await call(
            "tools/call",
            {
                "name": "get_execution_receipt",
                "arguments": {
                    "action": "get",
                    "store_id": runtime.store.store_id,
                    "project_hash": PROJECT,
                    "receipt_id": rid,
                    "include_result": True,
                },
            },
            "lookup",
        )
        # Then every representation retains failure and the application ran only once.
        assert first["result"]["isError"] is True
        assert replay["result"]["isError"] is True
        stored = lookup["result"]["structuredContent"]["result"]
        assert stored["isError"] is True
        assert stored["structuredContent"]["code"] == "native_refused"
        assert counts["body"] == 1
