"""The real SDK tool path shares selection and transport execution deadlines."""

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

    server.tool(blender_bridge)
    server.tool(manage_play_scenario)
    server.tool(manage_build)

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
                client.call_tool("manage_scene", raise_on_error=False), 0.3
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
    async with Client(request_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.06)
        entered, release = asyncio.Event(), asyncio.Event()
        old = command_hub.websocket
        old.client_state = WebSocketState.DISCONNECTED

        async def blocked_close(**_kwargs):
            entered.set()
            await release.wait()

        old.close = blocked_close
        task = asyncio.create_task(client.call_tool("manage_scene", raise_on_error=False))
        try:
            await entered.wait()
            failed = await asyncio.wait_for(task, 0.3)
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
            await asyncio.gather(*list(PluginHub._eviction_tasks.values()))
            result = await client.call_tool("manage_scene")
            assert result.structured_content["success"] is True
            assert PluginHub._connections["replacement"] is replacement
            assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


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
            await asyncio.wait_for(entered.wait(), 10)
            assert observed["request_timeout"] == pytest.approx(request_timeout)
            assert observed["scope_deadline"] == pytest.approx(observed["request_deadline"])
            assert observed["command_deadline"] == observed["request_deadline"]
            assert not task.done()
            assert len(PluginHub._pending) == 1
            assert command_hub.sent[0]["id"] in PluginHub._pending
            release.set()
            result = await asyncio.wait_for(task, 10)
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
        assert short_result.structured_content["data"]["reason"] == "outcome_unknown"
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
    service.register_global_tools(
        [ToolDefinitionModel(name="fixture_job", requires_polling=True, max_poll_seconds=1)]
    )
    calls = []

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
            monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.4 if not finish else 0.05)
            release = None
            if not finish:
                await command_hub.registry._lock.acquire()

                async def release_selection():
                    await asyncio.sleep(0.3)
                    command_hub.registry._lock.release()

                release = asyncio.create_task(release_selection())
            started = asyncio.get_running_loop().time()
            try:
                result = await client.call_tool(
                    "execute_custom_tool", {"tool_name": "fixture_job"}, raise_on_error=False
                )
            finally:
                if release is not None:
                    await release
            elapsed = asyncio.get_running_loop().time() - started
            if finish:
                assert result.structured_content["success"] is True
                assert len(calls) == 2
                assert elapsed > 0.09
            else:
                assert result.structured_content["success"] is False
                assert result.structured_content["data"]["reason"] == "outcome_unknown"
                assert 0.9 < elapsed < 1.2
                assert len(calls) == 1
            assert service._active_polls == 0
            assert PluginHub._pending == {}
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
    from main import create_mcp_server

    monkeypatch.setattr(config, "local_auth_token", "deadline-fixture")
    app = create_mcp_server(False).http_app()
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
    monkeypatch.setattr(
        PluginHub,
        "COMMAND_TIMEOUT",
        0.05 if case == "selection_lock" else 0.1 if case == "cumulative" else 0.02,
    )

    async def delayed_send(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            await asyncio.sleep(0.06)
            await answer(command_hub, command_hub.websocket, message)

    command_hub.websocket.send_json = delayed_send
    release = None
    await command_hub.registry._lock.acquire()
    if case != "selection_lock":

        async def release_selection():
            await asyncio.sleep(0.06)
            command_hub.registry._lock.release()

        release = asyncio.create_task(release_selection())
    started = asyncio.get_running_loop().time()
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
                # This watchdog catches hangs, not the command's production deadline.
                # Keep scheduling slack for oversubscribed CI workers; successful requests
                # still finish immediately and the short-deadline assertions stay below.
                2.0,
            )
    finally:
        if release is not None:
            await release
        elif command_hub.registry._lock.locked():
            command_hub.registry._lock.release()
    result = response.json()
    assert response.status_code == 200
    if case in {"long", "custom_long"}:
        assert result["success"] is True
        assert command_hub.sent[0]["timeout"] == (930 if case == "long" else 900)
    else:
        assert result["data"]["reason"] == (
            "command_deadline_exceeded" if case == "selection_lock" else "outcome_unknown"
        )
        assert asyncio.get_running_loop().time() - started < 0.2
        if case == "selection_lock":
            assert command_hub.sent == []
        else:
            assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
    assert PluginHub._pending == {}
    assert http_request_budget.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [True, False])
async def test_sdk_refresh_deadline_recovery_uses_structured_outcome_without_replay(
    command_hub, monkeypatch, lost_reply
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
            if lost_reply:
                return
            result = {"success": False, "error": "native refresh rejected"}
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
            "refresh_unity", {"compile": "request"}, raise_on_error=False
        )
        payload = result.structured_content
        payload = payload.get("result", payload)
        assert len([m for m in command_hub.sent if m.get("name") == "refresh_unity"]) == 1
        if lost_reply:
            assert payload["success"] is True
            assert payload["data"]["recovered_from_disconnect"] is True
            assert "unconfirmed" in payload["message"]
            assert len([m for m in command_hub.sent if m.get("name") == "get_editor_state"]) == 1
        else:
            assert payload["success"] is False
            assert payload["error"] == "native refresh rejected"
            assert len(command_hub.sent) == 1
        assert PluginHub._pending == {}
