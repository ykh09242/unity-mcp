"""Composed discovery retains explicit activation and actual inventory semantics."""

from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock
import asyncio

import pytest

import services.tools.manage_tools as module
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


def test_internal_catalog_requires_the_scoped_method_and_server():
    from fastmcp import FastMCP
    from transport.protocol import (
        internal_catalog,
        is_internal_catalog_list,
        is_internal_resource_list,
    )

    server = FastMCP("catalog-owner")
    owner = SimpleNamespace(fastmcp=server)
    resources = SimpleNamespace(method="resources/list", fastmcp_context=owner)
    tools = SimpleNamespace(method="tools/list", fastmcp_context=owner)

    with internal_catalog(server, "resources/list", "tools/list"):
        assert is_internal_resource_list(resources)
        assert is_internal_catalog_list(tools) and not is_internal_resource_list(tools)
        for context in (
            SimpleNamespace(fastmcp_context=owner),
            SimpleNamespace(method="resources/list"),
            SimpleNamespace(
                method="resources/list", fastmcp_context=SimpleNamespace(fastmcp=FastMCP("other"))
            ),
            SimpleNamespace(method="resources/templates/list", fastmcp_context=owner),
        ):
            assert not is_internal_catalog_list(context)
            assert not is_internal_resource_list(context)
    assert not is_internal_resource_list(resources)


@pytest.mark.asyncio
async def test_real_sdk_lists_external_catalog_with_unity_routing(monkeypatch):
    from core.config import config
    from fastmcp import Client, FastMCP
    from tests.plugin_hub_lifespan import isolated_plugin_hub
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    server = FastMCP("external-catalog")
    server.add_middleware(UnityInstanceMiddleware())

    @server.tool
    def sample_tool() -> str:
        pytest.fail("Listing invoked a tool")

    @server.resource("mcpforunity://sample")
    def sample_resource() -> str:
        pytest.fail("Listing read a resource")

    @server.resource("mcpforunity://sample/{name}")
    def sample_template(name: str) -> str:
        pytest.fail(f"Listing read the resource for {name}")

    async with (
        isolated_plugin_hub(monkeypatch),
        Client(server, timeout=10, init_timeout=10) as client,
    ):
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()

    assert {tool.name for tool in tools} == {"sample_tool"}
    assert {str(resource.uri) for resource in resources} == {"mcpforunity://sample"}
    assert {str(template.uri_template) for template in templates} == {"mcpforunity://sample/{name}"}


@pytest.mark.asyncio
async def test_transport_tool_is_discoverable_from_its_actual_wire_schema():
    from fastmcp import Client, FastMCP
    from jsonschema import Draft202012Validator
    from transport.execution_receipts import install_execution_receipts

    server = FastMCP("transport-discovery")
    server.tool(module.manage_tools)
    install_execution_receipts(server, None)
    async with Client(server) as client:
        tools = {item.name: item for item in await client.list_tools()}
        name = "get_execution_receipt"
        for action in ("catalog", "describe"):
            result = await client.call_tool("manage_tools", {"action": action, "tool": name})
            assert result.structured_content["tool"]["name"] == name
        description = result.structured_content
        assert description["tool"]["input_schema"] == tools[name].input_schema
        assert description["catalog"]["advertised"] is True
        assert description["catalog"]["readiness"] == "unobserved"
        bound = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": name, "operation": "get"}
        )
        schema = bound.structured_content["tool"]["input_schema"]
        assert schema["properties"]["action"]["const"] == "get"
        assert set(bound.structured_content["operation"]["required_parameters"]) == {
            "store_id",
            "project_hash",
        }
        validator = Draft202012Validator(schema)
        scope = {"action": "get", "store_id": "store", "project_hash": "project"}
        assert not validator.is_valid({"action": "get"})
        assert not validator.is_valid(scope)
        assert not validator.is_valid({**scope, "receipt_id": "id", "idempotency_key": "key"})
        for key in ("receipt_id", "idempotency_key"):
            assert validator.is_valid({**scope, key: "value"})
        default = Draft202012Validator(tools[name].input_schema)
        assert default.is_valid({}) and default.is_valid(
            {"action": "describe", "include_result": False}
        )
        assert not default.is_valid({"include_result": True})


@pytest.mark.asyncio
async def test_search_and_normalized_resource_branch_stay_within_the_original_request():
    from fastmcp import Client, FastMCP
    from fastmcp.server.middleware import Middleware
    from transport.protocol import is_internal_resource_list

    class CountResourceLists(Middleware):
        def __init__(self):
            self.external = []
            self.internal = []
            self.tool_requests = []

        async def on_call_tool(self, context, call_next):
            self.tool_requests.append(context.fastmcp_context.request_context.request_id)
            return await call_next(context)

        async def on_list_resources(self, context, call_next):
            requests = self.internal if is_internal_resource_list(context) else self.external
            requests.append(context.fastmcp_context.request_context.request_id)
            return await call_next(context)

    server = FastMCP("readable-state")
    server.tool(module.manage_tools)
    counter = CountResourceLists()
    server.add_middleware(counter)

    @server.resource("mcpforunity://editor/state")
    def editor_state() -> str:
        pytest.fail("Discovery read the resource contents")

    async with Client(server) as client:
        await client.list_resources()
        external_calls = list(counter.external)
        assert external_calls
        for group in ("resources", " resources "):
            page = await client.call_tool("manage_tools", {"action": "catalog", "group": group})
            assert [row["uri"] for row in page.structured_content["resources"]] == [
                "mcpforunity://editor/state"
            ]
        search = await client.call_tool("manage_tools", {"action": "search", "query": "هل يعمل؟"})
        assert search.structured_content["navigation"]["resources"]["total"] == 1
        assert counter.external == external_calls
        assert len(counter.tool_requests) == 3
        assert counter.internal == counter.tool_requests


@pytest.mark.asyncio
async def test_resource_catalog_keeps_per_call_project_selection(monkeypatch):
    from core.config import config
    from fastmcp import Client, Context, FastMCP
    from tests.plugin_hub_lifespan import isolated_plugin_hub
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "require_explicit_target", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    server = FastMCP("per-call resource catalog")
    routing = UnityInstanceMiddleware()
    server.add_middleware(routing)

    @server.tool
    async def inspect_catalog_selection(ctx: Context) -> dict:
        await routing.set_active_instance(ctx, "Saved@aaaaaaaa")
        await ctx.set_state("unity_instance", "PerCall@bbbbbbbb", serializable=False)
        await module._catalog_resources(ctx)
        return {
            "request": await ctx.get_state("unity_instance"),
            "session": await routing.get_active_instance(ctx),
        }

    async with (
        isolated_plugin_hub(monkeypatch),
        Client(server, mode="legacy", timeout=10, init_timeout=10) as client,
    ):
        result = await client.call_tool("inspect_catalog_selection", {})
        assert result.structured_content == {
            "request": "PerCall@bbbbbbbb",
            "session": "Saved@aaaaaaaa",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("advertised", [False, True])
async def test_activation_can_return_schema_and_declared_readiness(monkeypatch, advertised):
    from fastmcp.tools import Tool

    def feature(action: Literal["capabilities", "edit"]):
        pytest.fail(f"Inspecting the schema invoked {action}")

    definition = Tool.from_function(feature)
    discovery = SimpleNamespace(
        definitions={"feature": definition}, update=AsyncMock(return_value=True)
    )
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(
            _tool_discovery=discovery,
            list_tools=AsyncMock(return_value=[definition] if advertised else []),
        ),
        enable_components=AsyncMock(),
        get_state=AsyncMock(return_value="Second@second"),
    )
    monkeypatch.setattr(module, "is_sessionless", lambda _ctx: False)
    monkeypatch.setattr(
        module,
        "get_registered_tools",
        lambda: [{"name": "feature", "group": "test", "func": feature}],
    )
    result = await module.manage_tools(ctx, "activate", tool="feature", include_schema=True)
    descriptor = result["description"]["tool"]
    assert descriptor == definition.to_mcp_tool().model_dump(mode="json", exclude_none=True)
    assert descriptor["input_schema"]["properties"]["action"]["enum"] == ["capabilities", "edit"]
    assert result["server_advertised"] is advertised
    assert result["client_inventory_status"] == "unconfirmed"
    assert result["readiness"] == {
        "status": "unobserved",
        "next_action": {
            "tool": "feature",
            "action": "capabilities",
            "unity_instance": "Second@second",
        }
        if advertised
        else {
            "resource": "mcpforunity://tool-diagnostics/feature",
            "_meta": {"unity_instance": "Second@second"},
        },
    }
    discovery.update.assert_awaited_once_with(ctx, ["feature"], enabled=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"action": "activate", "tool": "feature", "include_schema": True},
        {"action": "activate", "group": "ui"},
        {"action": "reset"},
    ],
)
@pytest.mark.parametrize("can_read", [False, True])
async def test_hosted_selection_observes_inventory_only_with_read_permission(
    monkeypatch, selection, can_read
):
    from core.config import config
    from fastmcp.tools import Tool
    from services.operation_permissions import OperationGrants, Permission, authorize_operation

    def feature():
        pytest.fail("Selection invoked the tool")

    definition = Tool.from_function(feature)
    grants = OperationGrants(
        frozenset({Permission.EDIT, Permission.READ} if can_read else {Permission.EDIT}), True
    )
    authorize_operation(grants, "manage_tools", selection)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(
        "services.credential_lease.require_credential_lease",
        AsyncMock(return_value=SimpleNamespace(permissions=grants)),
    )
    monkeypatch.setattr(module, "is_sessionless", lambda _ctx: False)
    monkeypatch.setattr(
        module,
        "get_registered_tools",
        lambda: [{"name": "feature", "group": "ui", "func": feature}],
    )
    monkeypatch.setattr(module, "get_group_tool_names", lambda: {"ui": ["feature"]})
    discovery = SimpleNamespace(
        definitions={"feature": definition},
        default_groups={"ui"},
        update=AsyncMock(return_value=True),
    )
    listing = AsyncMock(return_value=[definition])
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(_tool_discovery=discovery, list_tools=listing),
        enable_components=AsyncMock(),
        get_state=AsyncMock(return_value=None),
    )
    result = await module.manage_tools(ctx, **selection)
    discovery.update.assert_awaited_once()
    assert ("refresh_request" in result) == can_read
    if selection["action"] == "reset":
        assert result["reset"] is True
        assert result["visible_tools"] == (["feature"] if can_read else None)
        assert result["default_groups"] == (["ui"] if can_read else None)
    elif "tool" in selection:
        assert result["changed"] is True
        assert result["server_advertised"] is (True if can_read else None)
        assert ("description" in result) == can_read
    else:
        assert result["changed"] is True
        assert result["server_advertised_tools"] == (["feature"] if can_read else None)
        assert result["tools"] == (["feature"] if can_read else None)
    assert listing.await_count == int(can_read)


@pytest.mark.asyncio
async def test_schema_option_rejected_before_group_activation(monkeypatch):
    discovery = SimpleNamespace(update=AsyncMock())
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(_tool_discovery=discovery))
    monkeypatch.setattr(module, "is_sessionless", lambda _ctx: False)
    result = await module.manage_tools(ctx, "activate", group="ui", include_schema=True)
    assert result["success"] is False
    discovery.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_context_is_bounded_to_captured_editor(monkeypatch):
    import services.operation_permissions as permissions
    import services.tools.wait_for_operation as waiting
    from services.tools.set_active_instance import selected_context

    target = waiting.ObservationTarget("Project@hash", None, "generation", None)
    monkeypatch.setattr(permissions, "authorize_remote_operation", AsyncMock())
    fetch = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "unity": {"unity_version": "6000.0", "unexpected": "large" * 1000},
                "compilation": {"is_compiling": True},
                "assets": {"is_updating": False},
            },
        }
    )
    monkeypatch.setattr(waiting, "fetch_observation", fetch)
    result, _ = await selected_context(target, asyncio.get_running_loop().time() + 3)
    assert result["generation"] == "generation"
    assert result["editor_state"]["compilation"] == {"is_compiling": True}
    assert "unexpected" not in result["editor_state"]["unity"]
    fetch.assert_awaited_once_with(target, "get_editor_state", {})
    fetch.reset_mock()
    result, observation = await selected_context(None, asyncio.get_running_loop().time() + 3)
    assert result["status"] == "unavailable" and observation is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect", [False, True])
async def test_switch_snapshot_uses_new_editor_generation(command_hub, monkeypatch, reconnect):
    import services.tools.set_active_instance as selection
    from tests.integration.test_helpers import DummyContext
    from transport.models import CommandResultMessage
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(selection, "get_unity_instance_middleware", lambda: middleware)
    ctx = DummyContext()
    await command_hub.registry.register("old-session", "Old", "old-hash", "6000.3")
    await middleware.set_active_instance(ctx, "Old@old-hash")
    await ctx.set_state("unity_instance", "Old@old-hash", serializable=False)
    await ctx.set_state("unity_session_id", "old-session", serializable=False)
    original = middleware.set_active_instance

    async def persist(context, instance):
        await original(context, instance)
        if reconnect:
            command_hub.websocket.state.plugin_generation = "replacement"

    async def answer(message):
        command_hub.sent.append(message["name"])
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"], result={"success": True, "data": {"activity": {"phase": "idle"}}}
            ),
        )

    monkeypatch.setattr(middleware, "set_active_instance", persist)
    command_hub.websocket.send_json = answer
    result = await selection.set_active_instance(ctx, "Fixture@hash", include_context=True)
    assert result["success"] and await middleware.get_active_instance(ctx) == "Fixture@hash"
    context = result["data"]["context"]
    if reconnect:
        assert context == {"status": "unavailable", "reason": "observation_generation_changed"}
        assert command_hub.sent == []
    else:
        assert context["instance"] == "Fixture@hash" and context["generation"] == "generation"
        assert context["editor_state"]["activity"] == {"phase": "idle"}
        assert command_hub.sent == ["get_editor_state"]


@pytest.mark.asyncio
async def test_remote_switch_checks_read_permission_on_new_socket(command_hub, monkeypatch):
    import services.tools.set_active_instance as selection
    import services.tools.wait_for_operation as waiting
    from tests.integration.test_helpers import DummyContext
    from tests.remote_credentials import remote_credentials
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(selection.config, "http_remote_hosted", True)
    monkeypatch.setattr(selection, "get_unity_instance_middleware", UnityInstanceMiddleware)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    ctx = DummyContext()
    await ctx.set_state("user_id", "alice", serializable=False)
    await ctx.set_state("unity_session_id", "old-session", serializable=False)
    fetch = AsyncMock()
    monkeypatch.setattr(waiting, "fetch_observation", fetch)
    async with remote_credentials({"request": "alice", "plugin": "alice"}) as credentials:
        await credentials.attach_socket(command_hub.websocket, "plugin")
        await credentials.service.invalidate_cache("plugin")
        async with credentials.requester("request"):
            result = await selection.set_active_instance(ctx, "Fixture@hash", include_context=True)
    assert result["success"]
    assert result["data"]["context"] == {"status": "unavailable", "reason": "CredentialLeaseError"}
    handoff = result["data"]["handoff"]
    assert handoff["instance"] == "Fixture@hash"
    assert handoff["mode"] == "unknown" and handoff["generation"] is None
    assert handoff["source"] == "unavailable" and handoff["observed_at_unix_ms"] is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stdio_switch_captures_and_reads_only_selected_connection(monkeypatch):
    import services.tools.set_active_instance as selection
    import services.tools.wait_for_operation as waiting
    from tests.integration.test_helpers import DummyContext
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(selection.config, "transport_mode", "stdio")
    monkeypatch.setattr(selection.config, "http_remote_hosted", False)
    monkeypatch.setattr(selection, "get_unity_instance_middleware", UnityInstanceMiddleware)
    target = SimpleNamespace(id="New@bbbbbbbb", hash="bbbbbbbb")
    monkeypatch.setattr(
        selection,
        "get_unity_connection_pool",
        lambda: SimpleNamespace(discover_all_instances=lambda **_: [target]),
    )
    generation = AsyncMock(return_value="new-generation")
    send = AsyncMock(return_value={"success": True, "data": {"assets": {"is_updating": False}}})
    monkeypatch.setattr(waiting, "get_authenticated_stdio_generation", generation)
    monkeypatch.setattr(waiting, "async_send_command_with_retry", send)
    ctx = DummyContext()
    await ctx.set_state("unity_instance", "Old@aaaaaaaa", serializable=False)
    result = await selection.set_active_instance(ctx, target.id, include_context=True)
    assert result["data"]["context"]["status"] == "observed"
    generation.assert_awaited_once_with(target.id)
    send.assert_awaited_once_with(
        "get_editor_state",
        {},
        instance_id=target.id,
        retry_on_reload=False,
        _expected_generation="new-generation",
    )


@pytest.mark.asyncio
async def test_handoff_is_recaptured_across_sessions_and_scene_or_mode_changes(command_hub):
    from services.tools.set_active_instance import set_active_instance
    from tests.integration.test_helpers import DummyContext
    from transport.models import CommandResultMessage

    scene = {"path": "Assets/Menu.unity", "guid": "menu-guid", "name": "Menu"}
    play_mode = {"is_playing": True, "is_changing": False}
    snapshot = {
        "observed_at_unix_ms": 12345,
        "unity": {"instance_id": "Fixture@hash"},
        "editor": {"active_scene": scene, "play_mode": play_mode},
    }

    async def answer(message):
        assert message["name"] == "get_editor_state"
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": snapshot}),
        )

    command_hub.websocket.send_json = answer
    checks = ["Capture the closed menu in Game View"]
    result = await set_active_instance(DummyContext(), "hash", evidence_needed=checks)
    previous = result["data"]["handoff"]
    assert previous == {
        "instance": "Fixture@hash",
        "generation": "generation",
        "status": "observed",
        "source": "live_editor",
        "saved_state": "unobserved",
        "mode": "play",
        "active_scene": dict(scene),
        "observed_at_unix_ms": 12345,
        "freshness": "point_in_time",
        "evidence_needed": checks,
        "evidence_needed_source": "caller",
    }

    play_mode["is_playing"] = False
    scene.update(path="Assets/Boot.unity", guid="boot-guid", name="Boot")
    snapshot["observed_at_unix_ms"] += 100
    receiver = DummyContext()
    current = (await set_active_instance(receiver, previous["instance"], include_context=True))[
        "data"
    ]["handoff"]
    assert current["mode"] == "edit" and current["active_scene"] == scene
    assert current["observed_at_unix_ms"] == 12445 and current["evidence_needed"] == []
    assert previous["mode"] == "play" and previous["active_scene"]["name"] == "Menu"

    snapshot["unity"]["instance_id"] = "Another@hash"
    result = await set_active_instance(receiver, previous["instance"], evidence_needed=checks)
    assert result["data"]["context"]["reason"] == "editor_instance_mismatch"
    unavailable = result["data"]["handoff"]
    assert unavailable["mode"] == "unknown" and unavailable["freshness"] == "unknown"
    assert set(unavailable["active_scene"].values()) == {None}
    assert unavailable["status"] == "unavailable" and unavailable["evidence_needed"] == checks


@pytest.mark.parametrize(
    ("play", "mode"),
    [
        ({"is_changing": True, "is_playing": True}, "transitioning"),
        ({"is_playing": True}, "unknown"),
        ({"is_changing": False, "is_playing": "false"}, "unknown"),
    ],
)
def test_handoff_preserves_incomplete_observations(play, mode):
    from services.tools.set_active_instance import handoff_observation

    observation = handoff_observation(
        {
            "observed_at_unix_ms": True,
            "editor": {
                "play_mode": play,
                "active_scene": {"name": "Untitled", "path": "", "guid": None},
            },
        }
    )
    assert observation["mode"] == mode
    assert observation["active_scene"] == {"name": "Untitled", "path": None, "guid": None}
    assert observation["observed_at_unix_ms"] is None and observation["freshness"] == "unknown"


@pytest.mark.asyncio
async def test_sessionless_wire_handoff_targets_one_generation_without_persisting(
    command_hub, monkeypatch
):
    from fastmcp import Client, FastMCP
    import services.operation_permissions as permissions
    import services.tools.set_active_instance as selection
    from transport.models import CommandResultMessage

    monkeypatch.setattr(
        selection,
        "get_unity_instance_middleware",
        lambda: pytest.fail("Persisted sessionless selection"),
    )
    commands = []

    async def answer(message):
        commands.append(message["name"])
        data = {
            "observed_at_unix_ms": 2500,
            "editor": {
                "play_mode": {"is_playing": False, "is_changing": False},
                "active_scene": {"name": "Fixture", "path": "Assets/Fixture.unity", "guid": "guid"},
            },
        }
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
        )

    command_hub.websocket.send_json = answer
    server = FastMCP("sessionless-handoff")
    server.tool(selection.set_active_instance)
    params = {"instance": "Fixture@hash", "evidence_needed": ["Inspect the saved prefab"]}
    async with Client(server, mode="2026-07-28") as client:
        result = (await client.call_tool("set_active_instance", params)).structured_content
        assert result["success"]
        data = result["data"]
        assert data["selection_persisted"] is False and "session_id" not in data
        assert data["handoff"]["instance"] == "Fixture@hash"
        assert data["handoff"]["mode"] == "edit" and data["handoff"]["generation"] == "generation"
        assert data["handoff"]["evidence_needed"] == params["evidence_needed"]
        wrong = (
            await client.call_tool("set_active_instance", {**params, "instance": "Other@hash"})
        ).structured_content
        assert wrong["success"] is False and "data" not in wrong
        selection_only = (
            await client.call_tool("set_active_instance", {"instance": "Fixture@hash"})
        ).structured_content
        assert selection_only["success"] is False

        async def reconnect(*args, **kwargs):
            command_hub.websocket.state.plugin_generation = "replacement"

        monkeypatch.setattr(permissions, "authorize_remote_operation", reconnect)
        stale = (await client.call_tool("set_active_instance", params)).structured_content
        assert not stale["success"] and stale["error"] == "observation_generation_changed"
        assert stale["data"]["handoff"]["status"] == "unavailable"
        assert stale["data"]["handoff"]["observed_at_unix_ms"] is None
        assert stale["data"]["handoff"]["evidence_needed"] == params["evidence_needed"]
        assert commands == ["get_editor_state"]
