"""Exercise production routing and bounded selection through the real SDK."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, Context, FastMCP
from jsonschema import Draft202012Validator
import pytest

from core.config import config
from services.tools import register_all_tools
from services.tools.manage_tools import manage_tools
from transport.plugin_hub import PluginHub
from transport.session_state_store import SessionStateStore
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def routed_server(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("UNITY_MCP_TOOL_PROFILE", raising=False)
    monkeypatch.setattr("services.tools.discover_modules", lambda *_: [])
    catalog = {"one": SimpleNamespace(project="First", hash="first")}
    monkeypatch.setattr(PluginHub, "is_configured", lambda: True)
    monkeypatch.setattr(
        PluginHub,
        "get_sessions",
        AsyncMock(side_effect=lambda **_: SimpleNamespace(sessions=catalog)),
    )
    monkeypatch.setattr(PluginHub, "_resolve_session_id", AsyncMock(return_value="one"))
    monkeypatch.setattr(PluginHub, "get_catalog_for_project", AsyncMock(return_value=None))

    async def inspect_probe(ctx: Context, count: int = 1):
        return {"selected": await ctx.get_state("unity_instance"), "count": count}

    async def effect_probe():
        return {"ok": True}

    entries = [
        {
            "name": fn.__name__,
            "func": fn,
            "description": fn.__name__,
            "group": group,
            "unity_target": None,
            "kwargs": {"tags": {f"group:{group}"} if group else set()},
        }
        for fn, group in [(inspect_probe, "core"), (effect_probe, "vfx"), (manage_tools, None)]
    ]
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", entries)
    store = SessionStateStore(max_entries=32)

    @asynccontextmanager
    async def lifespan(_):
        async with store.lifespan():
            yield

    server = FastMCP("routing-lifetime", session_state_store=store, lifespan=lifespan)
    server._unity_session_state_store = store
    middleware = UnityInstanceMiddleware()
    server.add_middleware(middleware)
    register_all_tools(server)
    return server, store, catalog, middleware


@pytest.mark.asyncio
async def test_modern_automatic_selection_does_not_exhaust_shared_state(routed_server):
    server, store, _, _ = routed_server
    async with Client(server) as client:
        for _ in range(96):
            result = await client.call_tool("inspect_probe", {})
            assert result.structured_content["selected"] == "First@first"
        assert store.retained_entries == 0
        activated = await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert activated.structured_content["changed"] is True
        assert store.retained_entries == 1
    assert store.retained_entries == 0


@pytest.mark.asyncio
async def test_legacy_repeated_selection_is_idempotent_and_bounded(routed_server):
    server, store, _, _ = routed_server
    async with Client(server, mode="legacy") as client:
        first = await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert first.structured_content["changed"] is True
        baseline = store.retained_bytes
        for _ in range(520):
            result = await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
            assert result.structured_content["changed"] is False
        assert store.retained_bytes <= baseline + 128
        assert "effect_probe" in {tool.name for tool in await client.list_tools()}


@pytest.mark.asyncio
async def test_legacy_selection_precedence_reset_and_connection_isolation(routed_server):
    server, _, _, _ = routed_server
    async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
        await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert "effect_probe" not in {tool.name for tool in await second.list_tools()}
        await first.call_tool("manage_tools", {"action": "deactivate", "tool": "effect_probe"})
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert "effect_probe" in {tool.name for tool in await first.list_tools()}
        result = await first.call_tool("manage_tools", {"action": "reset"})
        assert result.structured_content["visible_tools"] == ["inspect_probe", "manage_tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "legacy"])
async def test_advertised_routing_selector_targets_two_editors_without_handler_arguments(
    routed_server, mode
):
    server, _, catalog, _ = routed_server
    catalog["two"] = SimpleNamespace(project="Second", hash="second")
    async with Client(server, **({"mode": mode} if mode else {})) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
        schema = listed["inspect_probe"].input_schema
        for selector in ("First@first", "Second@second"):
            arguments = {"count": 3, "unity_instance": selector}
            assert list(Draft202012Validator(schema).iter_errors(arguments)) == []
            result = await client.call_tool("inspect_probe", arguments)
            assert result.structured_content == {"selected": selector, "count": 3}
        for malformed in ({"count": 1, "unrelated": 2}, {"count": 1, "unity_instance": True}):
            assert list(Draft202012Validator(schema).iter_errors(malformed))
            result = await client.call_tool("inspect_probe", malformed, raise_on_error=False)
            assert result.is_error


@pytest.mark.parametrize("rich", [False, True])
def test_custom_wire_schema_advertises_reserved_routing_outside_payload(rich):
    from models.models import ToolDefinitionModel, ToolParameterModel
    from services.custom_tool_service import CustomToolService

    service = CustomToolService(FastMCP("custom-routing-schema"))
    definition = ToolDefinitionModel(
        name="custom_probe",
        parameters=[]
        if rich
        else [ToolParameterModel(name="value", type="integer", required=True)],
        schema_version=1 if rich else 0,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        if rich
        else None,
    )
    tool = service._prepare_global_tool(definition)
    validator = Draft202012Validator(tool.parameters)
    assert list(validator.iter_errors({"value": 3, "unity_instance": "First@first"})) == []
    assert list(validator.iter_errors({"value": 3, "unity_instance": False}))
    assert list(validator.iter_errors({"unity_instance": "First@first"}))


@pytest.mark.asyncio
async def test_capacity_refusal_is_not_reported_as_missing_automatic_selection(routed_server):
    server, store, _, _ = routed_server
    store._max_entries = 1
    await store.put("occupied", {"preserve": True})
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool("inspect_probe", {}, raise_on_error=False)
        assert result.is_error
        assert "capacity" in result.content[0].text.lower()
        assert await store.get("occupied") == {"preserve": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("rich", [False, True])
async def test_custom_routing_selector_never_becomes_native_payload(
    routed_server, monkeypatch, rich
):
    from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
    from services.custom_tool_service import CustomToolService

    server, _, catalog, _ = routed_server
    catalog["two"] = SimpleNamespace(project="Second", hash="second")
    service = CustomToolService(server)
    monkeypatch.setattr(CustomToolService, "get_instance", lambda: service)
    monkeypatch.setattr(
        "services.custom_tool_service.resolve_project_id_for_unity_instance", lambda _: "fixture"
    )

    async def execute(project, name, selected, parameters, **kwargs):
        return MCPResponse(success=True, data={"selected": selected, "parameters": parameters})

    monkeypatch.setattr(service, "execute_tool", execute)
    definition = ToolDefinitionModel(
        name="custom_probe",
        parameters=[]
        if rich
        else [ToolParameterModel(name="value", type="integer", required=True)],
        schema_version=1 if rich else 0,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        if rich
        else None,
    )
    server.add_tool(service._prepare_global_tool(definition))
    async with Client(server) as client:
        result = await client.call_tool(
            "custom_probe", {"value": 3, "unity_instance": "Second@second"}
        )
        assert result.structured_content["data"] == {
            "selected": "Second@second",
            "parameters": {"value": 3},
        }


@pytest.mark.asyncio
async def test_active_legacy_selection_renews_without_accumulating_rules(
    routed_server, monkeypatch
):
    import time

    server, store, _, _ = routed_server
    from datetime import datetime, timezone

    baseline = time.time()
    now = {"value": baseline}
    monkeypatch.setattr("services.tool_discovery.time.time", lambda: now["value"])
    monkeypatch.setattr(store, "_clock", lambda: now["value"])
    clock = lambda: datetime.fromtimestamp(now["value"], tz=timezone.utc)
    monkeypatch.setattr("key_value.aio._utils.time_to_live.now", clock)
    monkeypatch.setattr("key_value.aio._utils.managed_entry.now", clock)
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        for hour in range(1, 27):
            now["value"] = baseline + hour * 3601
            assert "effect_probe" in {tool.name for tool in await client.list_tools()}
        assert store.retained_entries <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "legacy"])
@pytest.mark.parametrize(
    "constraints",
    [
        {"minProperties": 1, "maxProperties": 1},
        {"const": {"value": 3}},
        {"enum": [{"value": 3}, {"value": 4}]},
        {"minProperties": 1, "maxProperties": 1, "const": {"value": 3}, "enum": [{"value": 3}]},
    ],
)
async def test_rich_root_constraints_apply_to_native_arguments_not_routing(
    routed_server, monkeypatch, constraints, mode
):
    from copy import deepcopy
    from models.models import MCPResponse, ToolDefinitionModel
    from services.custom_tool_service import CustomToolService

    server, _, catalog, _ = routed_server
    catalog["two"] = SimpleNamespace(project="Second", hash="second")
    service = CustomToolService(server)
    monkeypatch.setattr(CustomToolService, "get_instance", lambda: service)
    monkeypatch.setattr(
        "services.custom_tool_service.resolve_project_id_for_unity_instance", lambda _: "fixture"
    )
    calls = []

    async def execute(project, name, selected, parameters, **kwargs):
        calls.append((selected, parameters))
        return MCPResponse(success=True, data=parameters)

    monkeypatch.setattr(service, "execute_tool", execute)
    native_schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "additionalProperties": False,
        **constraints,
    }
    original = deepcopy(native_schema)
    tool = service._prepare_global_tool(
        ToolDefinitionModel(name="constrained_probe", schema_version=1, input_schema=native_schema)
    )
    server.add_tool(tool)
    async with Client(server, **({"mode": mode} if mode else {})) as client:
        schema = next(t.input_schema for t in await client.list_tools() if t.name == tool.name)
        validator = Draft202012Validator(schema)
        native_validator = Draft202012Validator(native_schema)
        for payload in (
            {},
            {"value": 3},
            {"value": 4},
            {"value": 5},
            {"value": "bad"},
            {"value": 3, "extra": 1},
        ):
            expected = native_validator.is_valid(payload)
            assert validator.is_valid(payload) == expected
            routed = {**payload, "unity_instance": "Second@second"}
            assert validator.is_valid(routed) == expected
            result = await client.call_tool(tool.name, routed, raise_on_error=False)
            assert result.is_error is not expected
        assert not validator.is_valid({"value": 3, "unity_instance": True})
    assert native_schema == original
    assert all(
        selected == "Second@second" and "unity_instance" not in args for selected, args in calls
    )
