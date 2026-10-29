"""The real SDK tool path shares selection and transport execution deadlines."""

from tests.timing import await_completion, await_signal, watchdog_timeout

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from starlette.websockets import WebSocketState

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub, http_request_budget
from transport.unity_instance_middleware import UnityInstanceMiddleware
from transport.unity_transport import send_with_unity_instance


def request_server():
    server = FastMCP("http-request-deadline")
    middleware = UnityInstanceMiddleware()
    server.add_middleware(middleware)

    @server.tool
    async def select_fixture(ctx: Context) -> dict:
        await middleware.set_active_instance(ctx, "Fixture@hash")
        return {"success": True}

    @server.tool
    async def manage_scene(
        ctx: Context, timeout_seconds: float | None = None, retry_on_reload: bool = True
    ) -> dict:
        return await send_with_unity_instance(
            None,
            await ctx.get_state("unity_instance"),
            "manage_scene",
            {} if timeout_seconds is None else {"timeoutSeconds": timeout_seconds},
            retry_on_reload=retry_on_reload,
        )

    from services.tools.blender_bridge import blender_bridge
    from services.tools.manage_play_scenario import manage_play_scenario
    from services.tools.manage_build import manage_build
    from services.tools.get_compile_job import get_compile_job

    server.tool(blender_bridge)
    server.tool(manage_play_scenario)
    server.tool(manage_build)
    server.tool(get_compile_job)

    @server.tool
    async def request_probe(ctx: Context) -> dict:
        scope = http_request_budget.get()
        return {
            "success": True,
            "instance": await ctx.get_state("unity_instance"),
            "budget": scope.budget.expires_at - scope.started_at,
        }

    return server


async def answer(hub, websocket, message):
    await hub.endpoint._handle_command_result(
        websocket, CommandResultMessage(id=message["id"], result={"success": True})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("other_connected", [False, True])
@pytest.mark.parametrize("selector", ["Main@99a6a4da94b65b84", "99a6a4da94b65b84"])
async def test_sdk_explicit_compile_poll_recovers_registry_gap(
    command_hub, monkeypatch, protocol, other_connected, selector
):
    from tests.test_compile_job_contracts import native_job

    target_hash = "99a6a4da94b65b84"
    compile_id = "a" * 32
    native = native_job(compile_id)
    # Public status is derived from compiler evidence, not the raw peer label.
    expected = {**native, "status": "failed"}
    if not other_connected:
        await command_hub.registry.unregister("session")
    waiting = asyncio.Event()
    wait_for_change = command_hub.registry.wait_for_change

    async def observe_wait(changed, timeout):
        waiting.set()
        return await wait_for_change(changed, timeout)

    monkeypatch.setattr(command_hub.registry, "wait_for_change", observe_wait)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "30")

    async def compile_result(message):
        command_hub.sent.append(message)
        assert message["name"] == "get_compile_job"
        assert message["params"] == {"compile_id": compile_id}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": native}),
        )

    command_hub.websocket.send_json = compile_result
    async with Client(request_server(), mode=protocol) as client:
        task = asyncio.create_task(
            client.call_tool(
                "get_compile_job", {"compile_id": compile_id, "unity_instance": selector}
            )
        )
        try:
            await await_signal(waiting, "explicit target reconnect wait", producers=(task,))
            assert command_hub.sent == []
            # A different project's presence must never redirect the compile observation.
            await command_hub.registry.register("reconnected", "Main", target_hash, "6000")
            command_hub.websocket.state.plugin_session_id = "reconnected"
            PluginHub._connections["reconnected"] = command_hub.websocket
            result = await await_completion(task, "compile poll after reconnect")
            assert result.structured_content["success"] is True
            assert result.structured_content["data"] == expected
            assert len(command_hub.sent) == 1
            assert PluginHub._pending == {}
        finally:
            if not task.done():
                task.cancel()
            await await_completion(
                asyncio.gather(task, return_exceptions=True), "compile poll teardown"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
async def test_sdk_explicit_reconnect_stops_before_dispatch(
    command_hub, monkeypatch, protocol, stop
):
    await command_hub.registry.unregister("session")
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "30")
    waiting, stopped = asyncio.Event(), asyncio.Event()
    wait_for_change = command_hub.registry.wait_for_change
    budgets = []

    async def observe_wait(changed, timeout):
        budgets.append(http_request_budget.get())
        waiting.set()
        try:
            return await wait_for_change(changed, timeout)
        finally:
            stopped.set()

    monkeypatch.setattr(command_hub.registry, "wait_for_change", observe_wait)
    async with Client(request_server(), mode=protocol) as client:
        task = asyncio.create_task(
            client.call_tool(
                "get_compile_job",
                {"compile_id": "a" * 32, "unity_instance": "Main@99a6a4da94b65b84"},
                raise_on_error=False,
            )
        )
        try:
            await await_signal(waiting, "explicit reconnect wait", producers=(task,))
            assert len(budgets) == 1
            if stop == "deadline":
                budgets[0].scope.reschedule(asyncio.get_running_loop().time())
                result = await await_completion(task, "expired explicit selection")
                assert result.is_error
                assert result.structured_content["data"]["reason"] == "command_deadline_exceeded"
                assert result.structured_content["data"]["phase"] == "session_selection"
                assert result.structured_content["hint"] == "retry"
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await await_signal(stopped, "reconnect wait stopped")
            assert command_hub.sent == []
            assert PluginHub._pending == {}
        finally:
            if not task.done():
                task.cancel()
            await await_completion(
                asyncio.gather(task, return_exceptions=True), "explicit selection teardown"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,error",
    [
        ("WrongName@99a6a4da94b65b84", "not found"),
        ("abc", "No running Unity instance"),
        ("99a6", "ambiguous"),
    ],
)
async def test_sdk_explicit_reconnect_preserves_selector_validation(
    command_hub, monkeypatch, selector, error
):
    from unittest.mock import AsyncMock
    from mcp.shared.exceptions import MCPError

    await command_hub.registry.register("session", "Main", "99a6a4da94b65b84", "6000")
    await command_hub.registry.register("other", "Other", "99a6000000000000", "6000")
    wait = AsyncMock(side_effect=AssertionError("Invalid selectors must not wait"))
    monkeypatch.setattr(command_hub.registry, "wait_for_change", wait)
    async with Client(request_server(), mode="legacy") as client:
        with pytest.raises(MCPError, match=error):
            await client.call_tool(
                "get_compile_job", {"compile_id": "a" * 32, "unity_instance": selector}
            )
    wait.assert_not_awaited()
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_explicit_reconnect_wait_is_scoped_to_authenticated_user(command_hub, monkeypatch):
    from core.config import config
    from tests.integration.test_helpers import DummyContext
    from transport.plugin_hub import _CommandBudget, _HttpRequestBudget

    await command_hub.registry.unregister("session")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "30")
    target_hash = "99a6a4da94b65b84"
    await command_hub.registry.register("foreign", "Main", target_hash, "6000", user_id="other")
    waiting = asyncio.Event()
    wait_for_change = command_hub.registry.wait_for_change

    async def observe_wait(changed, timeout):
        waiting.set()
        return await wait_for_change(changed, timeout)

    monkeypatch.setattr(command_hub.registry, "wait_for_change", observe_wait)
    ctx = DummyContext()
    context = SimpleNamespace(
        fastmcp_context=ctx,
        message=SimpleNamespace(arguments={"unity_instance": f"Main@{target_hash}"}),
    )
    started = asyncio.get_running_loop().time()
    budget = _CommandBudget(started + watchdog_timeout())
    async with asyncio.timeout_at(budget.expires_at) as scope:
        token = http_request_budget.set(_HttpRequestBudget(started, budget, scope))
        task = asyncio.create_task(
            UnityInstanceMiddleware()._inject_unity_instance(context, authenticated_user_id="owner")
        )
        try:
            await await_signal(waiting, "own user's reconnect wait", producers=(task,))
            assert await ctx.get_state("unity_instance") is None
            assert await ctx.get_state("unity_session_id") is None
            await command_hub.registry.register(
                "owned", "Main", target_hash, "6000", user_id="owner"
            )
            await await_completion(task, "own user's instance selection")
            assert await ctx.get_state("unity_instance") == f"Main@{target_hash}"
            assert await ctx.get_state("unity_session_id") == "owned"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            http_request_budget.reset(token)
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["resource", "no_reconnect", "unbudgeted", "renamed"])
async def test_explicit_reconnect_keeps_discovery_bounds_and_exact_name(
    command_hub, monkeypatch, case
):
    from tests.integration.test_helpers import DummyContext
    from transport.plugin_hub import _CommandBudget, _HttpRequestBudget

    await command_hub.registry.unregister("session")
    selector = "Main@99a6a4da94b65b84"
    ctx = DummyContext(unity_instance=selector)
    context = SimpleNamespace(
        fastmcp_context=ctx,
        message=SimpleNamespace(uri="mcpforunity://capabilities")
        if case == "resource"
        else SimpleNamespace(arguments={"unity_instance": selector}),
    )
    waits = []

    async def reconnect(changed, timeout):
        assert case == "renamed", "Discovery must not acquire an unbounded reconnect wait"
        waits.append(changed)
        await command_hub.registry.register("renamed", "Renamed", "99a6a4da94b65b84", "6000")
        return True

    monkeypatch.setattr(command_hub.registry, "wait_for_change", reconnect)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "30")
    started = asyncio.get_running_loop().time()
    budget = _CommandBudget(started + watchdog_timeout())
    async with asyncio.timeout_at(budget.expires_at) as scope:
        token = http_request_budget.set(
            None if case == "unbudgeted" else _HttpRequestBudget(started, budget, scope)
        )
        try:
            with pytest.raises(ValueError, match="not found"):
                await UnityInstanceMiddleware()._inject_unity_instance(
                    context, allow_reconnect=case != "no_reconnect"
                )
        finally:
            http_request_budget.reset(token)
    assert len(waits) == (1 if case == "renamed" else 0)
    assert await ctx.get_state("unity_instance") is None
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
async def test_sdk_explicit_nonretrying_tool_does_not_acquire_compile_reconnect_wait(
    command_hub, monkeypatch, protocol
):
    from mcp.shared.exceptions import MCPError

    await command_hub.registry.unregister("session")
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "30")
    waits = []

    async def reconnect(changed, timeout):
        waits.append(changed)
        await command_hub.registry.register("session", "Main", "99a6a4da94b65b84", "6000")
        return True

    monkeypatch.setattr(command_hub.registry, "wait_for_change", reconnect)
    async with Client(request_server(), mode=protocol) as client:
        # Modern SDK responses mask selection ValueErrors; both protocols must
        # still reject this request without acquiring or dispatching a new route.
        error = "not found" if protocol == "legacy" else "Internal server error"
        with pytest.raises(MCPError, match=error):
            await client.call_tool(
                "manage_scene",
                {"unity_instance": "Main@99a6a4da94b65b84", "retry_on_reload": False},
            )
    assert waits == []
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_sdk_selected_reconnect_and_send_spend_one_budget(command_hub, monkeypatch):
    server = request_server()
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.16)
        monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "1")
        await command_hub.registry.unregister("session")

        async def reconnect():
            await asyncio.sleep(0.1)
            await command_hub.registry.register("session", "Fixture", "hash", "6000")

        async def delayed_send(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                await asyncio.sleep(0.1)
                await answer(command_hub, command_hub.websocket, message)

        command_hub.websocket.send_json = delayed_send
        connection = asyncio.create_task(reconnect())
        started = asyncio.get_running_loop().time()
        try:
            result = await client.call_tool("manage_scene", raise_on_error=False)
        finally:
            await connection
        assert result.structured_content["success"] is False
        assert result.structured_content["data"]["reason"] == "outcome_unknown"
        assert asyncio.get_running_loop().time() - started < 0.25
        assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
        assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_sdk_selected_registry_lock_expires_before_dispatch(command_hub, monkeypatch):
    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.06)
        async with command_hub.registry._lock:
            result = await asyncio.wait_for(
                client.call_tool("manage_scene", raise_on_error=False), watchdog_timeout()
            )
        assert result.structured_content["data"]["reason"] == "command_deadline_exceeded"
        assert result.structured_content["data"]["phase"] == "session_selection"
        assert result.structured_content["hint"] == "retry"
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_sdk_long_budget_preserves_timeout_alias_and_request_isolation(
    command_hub, monkeypatch
):
    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02)

        async def delayed_send(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                await asyncio.sleep(0.05)
                await answer(command_hub, command_hub.websocket, message)

        command_hub.websocket.send_json = delayed_send
        long_result = await client.call_tool(
            "blender_bridge", {"action": "run_python", "code": "pass", "timeout_seconds": 900}
        )
        assert long_result.structured_content["success"] is True
        assert command_hub.sent[0]["timeout"] == 930
        following = await client.call_tool("manage_scene", raise_on_error=False)
        assert following.structured_content["data"]["reason"] == "outcome_unknown"
        assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_sdk_stale_cleanup_survives_deadline_and_preserves_replacement(
    command_hub, monkeypatch
):
    import transport.unity_instance_middleware as middleware_module

    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", watchdog_timeout())
        entered, release = asyncio.Event(), asyncio.Event()
        budgets = []
        request_budget = middleware_module._HttpRequestBudget

        def capture_budget(*args, **kwargs):
            budget = request_budget(*args, **kwargs)
            budgets.append(budget)
            return budget

        monkeypatch.setattr(middleware_module, "_HttpRequestBudget", capture_budget)
        old = command_hub.websocket
        old.client_state = WebSocketState.DISCONNECTED

        async def blocked_close(**_kwargs):
            entered.set()
            await release.wait()

        old.close = blocked_close
        task = asyncio.create_task(client.call_tool("manage_scene", raise_on_error=False))
        try:
            await await_signal(entered, "stale socket close", producers=(task,))
            assert len(budgets) == 1
            # Expire the real SDK scope only after server-owned cleanup starts.
            budgets[0].scope.reschedule(asyncio.get_running_loop().time())
            failed = await await_completion(task, "expired SDK request")
            assert budgets[0].scope.expired()
            assert failed.structured_content["data"]["reason"] == "command_deadline_exceeded"
            replacement = SimpleNamespace(
                state=SimpleNamespace(
                    plugin_registered=True,
                    plugin_session_id="replacement",
                    plugin_generation="new-generation",
                    plugin_features=frozenset(),
                    user_id=None,
                ),
                application_state=WebSocketState.CONNECTED,
                client_state=WebSocketState.CONNECTED,
                close=lambda **_kwargs: asyncio.sleep(0),
            )

            async def send_json(message):
                command_hub.sent.append(message)
                await answer(command_hub, replacement, message)

            replacement.send_json = send_json
            await command_hub.registry.register("replacement", "Fixture", "hash", "6000")
            PluginHub._connections["replacement"] = replacement
            release.set()
            await await_completion(
                asyncio.gather(*list(PluginHub._eviction_tasks.values())), "stale socket cleanup"
            )
            result = await client.call_tool("manage_scene")
            assert result.structured_content["success"] is True
            assert PluginHub._connections["replacement"] is replacement
            assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await await_completion(
                asyncio.gather(task, return_exceptions=True), "SDK request teardown"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments,wire_timeout,request_timeout",
    [
        ("manage_play_scenario", {"action": "run", "name": "fixture"}, 300, 305),
        (
            "manage_play_scenario",
            {"action": "run", "name": "fixture", "timeout_seconds": 900},
            900,
            905,
        ),
        ("manage_build", {"action": "build"}, 5, 5),
    ],
)
async def test_sdk_production_long_run_and_build_policy(
    command_hub, monkeypatch, tool, arguments, wire_timeout, request_timeout
):
    """Observe production policy directly, independently of subsecond scheduler latency."""
    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 5)
        entered, release = asyncio.Event(), asyncio.Event()
        observed = {}

        async def controlled_send(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                request = http_request_budget.get()
                assert request is not None
                observed["request_timeout"] = request.budget.expires_at - request.started_at
                observed["scope_deadline"] = request.scope.when()
                observed["request_deadline"] = request.budget.expires_at
                assert request.budget.active is not None
                observed["command_deadline"] = request.budget.active.expires_at
                entered.set()
                await release.wait()
                await answer(command_hub, command_hub.websocket, message)

        command_hub.websocket.send_json = controlled_send
        task = asyncio.create_task(client.call_tool(tool, arguments, raise_on_error=False))
        try:
            await await_signal(entered, "entered")
            assert observed["request_timeout"] == pytest.approx(request_timeout)
            assert observed["scope_deadline"] == pytest.approx(observed["request_deadline"])
            assert observed["command_deadline"] == observed["request_deadline"]
            assert not task.done()
            assert len(PluginHub._pending) == 1
            assert command_hub.sent[0]["id"] in PluginHub._pending
            release.set()
            result = await asyncio.wait_for(task, watchdog_timeout())
            assert result.structured_content["success"] is True
            assert command_hub.sent[0]["timeout"] == wire_timeout
            assert command_hub.sent[0]["name"] == tool
            assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
            assert PluginHub._pending == {}
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sdk_scope_restores_prior_context_and_concurrent_budgets(command_hub, monkeypatch):
    server = request_server()
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02)
        before = http_request_budget.get()

        async def delayed_send(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                await asyncio.sleep(0.05)
                await answer(command_hub, command_hub.websocket, message)

        command_hub.websocket.send_json = delayed_send
        long_result, short_result = await asyncio.gather(
            client.call_tool(
                "blender_bridge", {"action": "run_python", "code": "pass", "timeout_seconds": 900}
            ),
            client.call_tool("manage_scene", raise_on_error=False),
        )
        assert long_result.structured_content["success"] is True
        short_dispatches = [
            message
            for message in command_hub.sent
            if message["type"] == "execute" and message["name"] == "manage_scene"
        ]
        assert len(short_dispatches) <= 1
        # A short request may expire behind the long writer. Only an observed
        # send boundary can establish the old in-flight unknown-outcome case.
        expected = "outcome_unknown" if short_dispatches else "command_deadline_exceeded"
        assert short_result.structured_content["data"]["reason"] == expected
        assert (
            len(
                [
                    message
                    for message in command_hub.sent
                    if message["type"] == "execute" and message["name"] == "blender_bridge"
                ]
            )
            == 1
        )
        probe = await client.call_tool("request_probe")
        assert probe.structured_content["budget"] == pytest.approx(0.02)
        assert probe.structured_content["instance"] == "Fixture@hash"
        assert http_request_budget.get() is before


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [True, False])
async def test_sdk_custom_polling_uses_its_existing_bound_from_request_start(
    command_hub, monkeypatch, finish
):
    from models.models import ToolDefinitionModel
    from services.custom_tool_service import CustomToolService
    from services.tools.execute_custom_tool import execute_custom_tool

    server = request_server()
    server.tool(execute_custom_tool)
    previous = CustomToolService._instance
    service = CustomToolService(server)
    poll_seconds = 60
    service.register_global_tools(
        [
            ToolDefinitionModel(
                name="fixture_job", requires_polling=True, max_poll_seconds=poll_seconds
            )
        ]
    )
    calls = []
    definition_entered, release_definition = asyncio.Event(), asyncio.Event()
    polling_entered = asyncio.Event()
    observed = {}
    get_definition = service.get_tool_definition

    async def observe_definition(*args, **kwargs):
        request = http_request_budget.get()
        observed["started_at"] = request.started_at
        observed["initial_budget"] = request.budget.expires_at - request.started_at
        definition_entered.set()
        await release_definition.wait()
        return await get_definition(*args, **kwargs)

    monkeypatch.setattr(service, "get_tool_definition", observe_definition)
    interpret_status = service._interpret_status

    def observe_status(*args, **kwargs):
        status, interval = interpret_status(*args, **kwargs)
        if status == "pending":
            observed["request"] = http_request_budget.get()
            polling_entered.set()
        return status, interval

    monkeypatch.setattr(service, "_interpret_status", observe_status)

    async def send_json(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            calls.append(message)
            result = (
                {"_mcp_status": "complete", "success": True}
                if finish and len(calls) > 1
                else {"_mcp_status": "pending", "_mcp_poll_interval": 0.1 if finish else 5}
            )
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket, CommandResultMessage(id=message["id"], result=result)
            )

    command_hub.websocket.send_json = send_json
    try:
        async with Client(server, mode="legacy") as client:
            await client.call_tool("select_fixture")
            monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 30)
            task = asyncio.create_task(
                client.call_tool(
                    "execute_custom_tool", {"tool_name": "fixture_job"}, raise_on_error=False
                )
            )
            try:
                await await_signal(
                    definition_entered, "custom descriptor lookup", producers=(task,)
                )
                assert observed["initial_budget"] == pytest.approx(30)
                release_definition.set()
                await await_signal(polling_entered, "custom polling wait", producers=(task,))
                request = observed["request"]
                # Verify the policy at its boundary, without a 50 ms scheduler race.
                assert request.budget.expires_at == observed["started_at"] + poll_seconds
                assert request.scope.when() == request.budget.expires_at
                if not finish:
                    request.scope.reschedule(asyncio.get_running_loop().time())
                result = await await_completion(task, "custom polling result")
                if finish:
                    assert result.structured_content["success"] is True, result.structured_content
                    assert len(calls) == 2
                else:
                    assert result.structured_content["success"] is False
                    assert result.structured_content["data"]["reason"] == "outcome_unknown"
                    assert len(calls) == 1
                assert service._active_polls == 0
                assert PluginHub._pending == {}
            finally:
                release_definition.set()
                if not task.done():
                    task.cancel()
                await await_completion(
                    asyncio.gather(task, return_exceptions=True), "custom polling teardown"
                )
    finally:
        CustomToolService._instance = previous


@pytest.mark.asyncio
async def test_sdk_partial_tool_execution_cannot_be_reported_safe_to_retry(
    command_hub, monkeypatch
):
    server = request_server()

    @server.tool
    async def two_commands(ctx: Context) -> dict:
        instance = await ctx.get_state("unity_instance")
        first = await send_with_unity_instance(None, instance, "manage_scene", {})
        assert first["success"]
        return await send_with_unity_instance(None, instance, "manage_scene", {})

    async def answer_then_block_next_admission(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            await answer(command_hub, command_hub.websocket, message)
            await command_hub.registry._lock.acquire()

    command_hub.websocket.send_json = answer_then_block_next_admission
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.06)
        try:
            result = await client.call_tool("two_commands", raise_on_error=False)
        finally:
            if command_hub.registry._lock.locked():
                command_hub.registry._lock.release()
        assert result.structured_content["hint"] == "inspect_state_before_retry"
        assert result.structured_content["data"]["command_id"] == command_hub.sent[0]["id"]
        assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1


@pytest.mark.asyncio
async def test_sdk_nonretrying_command_does_not_wait_for_reconnect(command_hub, monkeypatch):
    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        await command_hub.registry.unregister("session")
        monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "1")
        started = asyncio.get_running_loop().time()
        result = await client.call_tool("manage_scene", {"retry_on_reload": False})
        assert result.structured_content["hint"] == "retry"
        assert asyncio.get_running_loop().time() - started < 0.3
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_sdk_registered_alias_uses_target_policy_during_selection(command_hub, monkeypatch):
    from services.tools.blender_bridge import blender_bridge
    import transport.unity_instance_middleware as module

    monkeypatch.setattr(
        module,
        "get_registered_tools",
        lambda: [{"name": "bridge_alias", "unity_target": "blender_bridge"}],
    )
    server = request_server()
    server.tool(blender_bridge, name="bridge_alias")
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02)
        await command_hub.registry._lock.acquire()

        async def release_selection():
            await asyncio.sleep(0.06)
            command_hub.registry._lock.release()

        release = asyncio.create_task(release_selection())
        try:
            result = await client.call_tool(
                "bridge_alias", {"action": "run_python", "code": "pass"}, raise_on_error=False
            )
        finally:
            await release
        assert result.structured_content["success"] is True
        assert command_hub.sent[0]["timeout"] == 210
        assert command_hub.sent[0]["name"] == "blender_bridge"


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["refresh_unity", "prepared_refresh", "dispatch_only"])
async def test_sdk_refresh_retains_declared_and_prepared_readiness_policy(
    command_hub, monkeypatch, entry
):
    import services.tools.refresh_unity as refresh_module

    server = request_server()
    server.tool(refresh_module.refresh_unity)

    @server.tool
    async def prepared_refresh(ctx: Context) -> dict:
        result = await refresh_module.refresh_unity(ctx, wait_for_ready=True)
        return result.model_dump() if hasattr(result, "model_dump") else result

    # Exercise the actual readiness loop; production intentionally shortcuts it in pytest.
    monkeypatch.setattr(refresh_module, "_in_pytest", lambda: False)
    probes = []

    async def respond(message):
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        result = {"success": True}
        if message["name"] == "get_editor_state":
            probes.append(message)
            result["data"] = {"compilation": {"is_compiling": len(probes) == 1}}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket, CommandResultMessage(id=message["id"], result=result)
        )

    command_hub.websocket.send_json = respond
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.05)
        monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.02)
        monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", "0.02")
        release = None
        if entry == "refresh_unity":
            await command_hub.registry._lock.acquire()

            async def release_selection():
                await asyncio.sleep(0.08)
                command_hub.registry._lock.release()

            release = asyncio.create_task(release_selection())
        started = asyncio.get_running_loop().time()
        try:
            result = await client.call_tool(
                "refresh_unity" if entry == "dispatch_only" else entry,
                {"wait_for_ready": False} if entry == "dispatch_only" else {},
                raise_on_error=False,
            )
        finally:
            if release is not None:
                await release
        payload = result.structured_content
        assert payload.get("result", payload)["success"] is True
        assert len([m for m in command_hub.sent if m.get("name") == "refresh_unity"]) == 1
        if entry == "dispatch_only":
            assert probes == []
        else:
            assert len(probes) == 2
            assert all(probe["timeout"] == 0.02 for probe in probes)
            assert asyncio.get_running_loop().time() - started > 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["selection_lock", "cumulative", "long", "custom_long"])
async def test_rest_command_selection_and_dispatch_share_budget(command_hub, monkeypatch, case):
    import httpx
    from core.config import config
    import main as main_module

    request_budgets = []
    request_started, send_entered, release_reply = (asyncio.Event() for _ in range(3))
    observed_deadlines = []
    original_request_budget = main_module._HttpRequestBudget

    def observe_request_budget(started_at, budget, scope):
        request_budget = original_request_budget(started_at, budget, scope)
        request_budgets.append((request_budget, budget.expires_at))
        request_started.set()
        return request_budget

    monkeypatch.setattr(main_module, "_HttpRequestBudget", observe_request_budget)
    monkeypatch.setattr(config, "local_auth_token", "deadline-fixture")
    app = main_module.create_mcp_server(False).http_app()
    if case == "custom_long":
        from models.models import ToolDefinitionModel, ToolParameterModel
        from services.custom_tool_service import CustomToolService

        CustomToolService.get_instance()._project_tools["hash"] = {
            "fixture_custom": ToolDefinitionModel(
                name="fixture_custom",
                parameters=[
                    ToolParameterModel(name="timeoutSeconds", type="integer", required=True)
                ],
            )
        }
    # Phase gates exercise expiry without racing subsecond client/runner scheduling.
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 300)

    async def controlled_send(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            send_entered.set()
            await release_reply.wait()
            await answer(command_hub, command_hub.websocket, message)

    command_hub.websocket.send_json = controlled_send
    await command_hub.registry._lock.acquire()

    async def control_phases():
        await await_signal(request_started, "REST request budget installed")
        request_budget, _ = request_budgets[0]
        observed_deadlines.append(request_budget.scope.when())
        if case != "selection_lock":
            command_hub.registry._lock.release()
            await await_signal(send_entered, "REST command dispatched")
            observed_deadlines.append(request_budget.scope.when())
        if case in {"selection_lock", "cumulative"}:
            # Trigger the real timeout scope only after the phase under test is reached.
            # The production command budget must still retain its original deadline.
            request_budget.scope.reschedule(asyncio.get_running_loop().time())
        else:
            release_reply.set()

    controller = asyncio.create_task(control_phases())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://deadline.invalid"
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/api/command",
                    headers={"X-Unity-MCP-Token": "deadline-fixture"},
                    json={
                        "type": "execute_custom_tool"
                        if case == "custom_long"
                        else "blender_bridge"
                        if case == "long"
                        else "manage_scene",
                        "unity_instance": "Fixture@hash",
                        "params": {"name": "fixture_custom", "params": {"timeoutSeconds": 900}}
                        if case == "custom_long"
                        else {"timeout_seconds": 900}
                        if case == "long"
                        else {},
                    },
                ),
                watchdog_timeout(),
            )
            await controller
    finally:
        release_reply.set()
        if not controller.done():
            controller.cancel()
        await asyncio.gather(controller, return_exceptions=True)
        if command_hub.registry._lock.locked():
            command_hub.registry._lock.release()
    result = response.json()
    assert response.status_code == 200
    assert len(request_budgets) == 1
    request_budget, original_expiry = request_budgets[0]
    expected_timeout = (
        935 if case == "long" else 905 if case == "custom_long" else PluginHub.COMMAND_TIMEOUT
    )
    # Client delivery and runner scheduling can outlast the server's timeout.
    # Verify the deadline installed at entry, and that dispatch never restarts it.
    assert original_expiry - request_budget.started_at == pytest.approx(expected_timeout)
    assert request_budget.budget.expires_at == original_expiry
    assert observed_deadlines == [original_expiry] * (1 if case == "selection_lock" else 2)
    if case in {"selection_lock", "cumulative"}:
        assert request_budget.scope.expired()
    else:
        assert request_budget.scope.when() == original_expiry
    if case != "selection_lock":
        assert request_budget.budget.active.expires_at == original_expiry
    if case in {"long", "custom_long"}:
        assert result["success"] is True
        assert command_hub.sent[0]["timeout"] == (930 if case == "long" else 900)
    else:
        assert result["data"]["reason"] == (
            "command_deadline_exceeded" if case == "selection_lock" else "outcome_unknown"
        )
        if case == "selection_lock":
            assert command_hub.sent == []
        else:
            assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
    assert PluginHub._pending == {}
    assert http_request_budget.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["expired", "disconnected", "rejected"])
async def test_sdk_refresh_deadline_recovery_uses_structured_outcome_without_replay(
    command_hub, monkeypatch, reply
):
    import services.tools.refresh_unity as refresh_module

    server = request_server()
    server.tool(refresh_module.refresh_unity)
    monkeypatch.setattr(refresh_module, "_in_pytest", lambda: False)

    async def respond(message):
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        if message["name"] == "refresh_unity":
            if reply == "expired":
                return
            result = {
                "success": False,
                "error": "Connection closed after dispatch"
                if reply == "disconnected"
                else "native refresh rejected",
            }
        else:
            result = {"success": True, "data": {"compilation": {"is_compiling": False}}}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket, CommandResultMessage(id=message["id"], result=result)
        )

    command_hub.websocket.send_json = respond
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.05)
        monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.02)
        result = await client.call_tool(
            "refresh_unity",
            {"compile": "request", "timeout_seconds": 1 if reply == "expired" else 300},
            raise_on_error=False,
        )
        payload = result.structured_content
        payload = payload.get("result", payload)
        assert len([m for m in command_hub.sent if m.get("name") == "refresh_unity"]) == 1
        if reply == "expired":
            # The explicit observation budget is exhausted. Readiness cannot be
            # queried afterward to turn an unknown write into apparent success.
            assert payload["success"] is False
            assert payload["hint"] == "inspect_state_before_retry"
            assert payload["data"]["reason"] == "outcome_unknown"
            assert [m["name"] for m in command_hub.sent if m["type"] == "execute"] == [
                "refresh_unity"
            ]
            assert all(m["id"] == command_hub.sent[0]["id"] for m in command_hub.sent)
        elif reply == "disconnected":
            assert payload["success"] is True
            assert payload["data"]["recovered_from_disconnect"] is True
            assert "unconfirmed" in payload["message"]
            assert len([m for m in command_hub.sent if m.get("name") == "get_editor_state"]) == 1
        else:
            assert payload["success"] is False
            assert payload["error"] == "native refresh rejected"
            assert len(command_hub.sent) == 1
        assert PluginHub._pending == {}
