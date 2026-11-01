"""Custom dispatch cannot turn native catalog rows into alternate built-in APIs."""

from copy import deepcopy
import importlib
from unittest.mock import AsyncMock, Mock

from fastmcp import Client, FastMCP
import pytest

from core.config import config
from models.models import ToolDefinitionModel
import services.custom_tool_service as custom
from services.registry import TOOL_GROUPS
from services.tool_discovery import ToolDiscovery
from services.tools import register_all_tools

endpoint = importlib.import_module("services.tools.execute_custom_tool")


@pytest.fixture(scope="module")
def boundary_catalog() -> FastMCP:
    # Compile the real complete SDK catalog once; individual cases own all mutable state.
    app = FastMCP("custom-availability-catalog")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "http_remote_hosted", False)
        patch.setattr(config, "transport_mode", "stdio")
        register_all_tools(app)
    return app


def _boundary_app(catalog: FastMCP) -> FastMCP:
    app = FastMCP("custom-availability-boundary")
    source = catalog._tool_discovery
    definitions = deepcopy(source.definitions)
    for tool in definitions.values():
        app.add_tool(tool)
    discovery = ToolDiscovery(app, definitions, source.groups.copy(), hosted=False)
    app._tool_discovery = discovery
    for group in sorted(set(TOOL_GROUPS) - discovery.default_groups):
        app.disable(tags={f"group:{group}"}, components={"tool"})
    app.add_transform(discovery)
    return app


@pytest.fixture
def boundary(monkeypatch, boundary_catalog):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(custom.CustomToolService, "_instance", None)
    app = _boundary_app(boundary_catalog)
    service = custom.CustomToolService(app)
    send = AsyncMock(return_value={"success": True, "data": {"effect": "executed"}})
    monkeypatch.setattr(custom, "send_with_unity_instance", send)
    for module in (custom, endpoint):
        monkeypatch.setattr(
            module, "get_unity_instance_from_context", AsyncMock(return_value="Project@hash")
        )
        monkeypatch.setattr(module, "resolve_project_id_for_unity_instance", lambda _: "hash")
    return app, service, send


@pytest.mark.asyncio
async def test_boundary_catalog_keeps_mutable_case_state_independent(boundary, boundary_catalog):
    app, service, _ = boundary
    original = boundary_catalog._tool_discovery.definitions["manage_scene"]
    tool = app._tool_discovery.definitions["manage_scene"]
    tool.parameters["properties"]["action"]["description"] = "case-local mutation"
    tool.tags.add("case-local mutation")
    assert await app.get_tool("manage_tools") is not None
    app.disable(names={"manage_tools"}, components={"tool"})
    app.disable(names={"manage_scene"}, components={"tool"})
    service.register_global_tools([ToolDefinitionModel(name="case_local_custom")])
    assert await app.get_tool("manage_scene") is None
    assert await app.get_tool("manage_tools") is None
    assert await app.get_tool("case_local_custom") is not None

    fresh = _boundary_app(boundary_catalog)
    fresh_service = custom.CustomToolService(fresh)
    restored = fresh._tool_discovery.definitions["manage_scene"]
    assert restored.parameters == original.parameters
    assert restored.tags == original.tags
    assert "case-local mutation" not in restored.tags
    assert restored.parameters["properties"]["action"].get("description") != "case-local mutation"
    assert (await fresh.get_tool("manage_scene") is None) == (
        await boundary_catalog.get_tool("manage_scene") is None
    )
    assert await fresh.get_tool("case_local_custom") is None
    assert await fresh.get_tool("manage_tools") is not None
    assert fresh_service._global_tools == {} and fresh_service._project_tools == {}
    assert set(fresh._tool_discovery.definitions) == set(
        boundary_catalog._tool_discovery.definitions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["endpoint", "service"])
@pytest.mark.parametrize(
    "name",
    [
        "manage_scene",  # Public built-in, including disabled descriptors retained in catalogs.
        "MANAGE_SCENE",  # Native command identity is case insensitive.
        " manage_scene ",
        "unity_docs",  # Public alias.
        "unity_reflect",  # Native alias target.
        "manage_object_handles_v2",  # Current native implementation name.
        "tail_console",  # Hidden native backend; not a separate custom API.
        "execute_menu_item_observed",
        "batch_execute",  # Nested batch cannot bypass canonical validation.
        "batch_validate",  # Reserved native aliases have no standalone Python wrapper.
        "batch_apply_preview",
        "get_tool_states",  # Native resources must retain their canonical API.
        "get_compile_job",
        "execute_custom_tool",  # Nested custom dispatch is not a native custom tool.
    ],
)
async def test_builtin_catalog_descriptor_cannot_execute_via_custom_route(boundary, route, name):
    app, service, send = boundary
    app.disable(names={"manage_scene"}, components={"tool"})
    assert await app.get_tool("manage_scene") is None
    service._register_project_tools("hash", [ToolDefinitionModel(name=name)])

    if route == "endpoint":
        result = await endpoint.execute_custom_tool(Mock(), name, {})
    else:
        result = await service.execute_tool("hash", name, "Project@hash", {})

    assert result.success is False
    assert result.code == "builtin_tool_requires_canonical_route"
    assert result.data == {"reason": "builtin_tool_requires_canonical_route", "dispatched": False}
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["manage_scene", "MANAGE_SCENE", "unity_reflect", "batch_validate", "tail_console"]
)
async def test_retained_sdk_custom_handler_cannot_dispatch_a_builtin_descriptor(boundary, name):
    _, service, send = boundary
    definition = ToolDefinitionModel(name=name)
    service._register_project_tools("hash", [definition])
    # Exercise an SDK handler retained from an earlier custom publication. Normal
    # publication already avoids canonical name collisions; invocation must too.
    retained_app = FastMCP("retained-custom-handler")
    retained_app.add_tool(service._prepare_global_tool(definition))
    async with Client(retained_app) as client:
        blocked = await client.call_tool(name, {}, raise_on_error=False)
    assert blocked.structured_content["success"] is False
    assert blocked.structured_content["code"] == "builtin_tool_requires_canonical_route"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_custom_handler_cannot_keep_a_name_that_becomes_builtin(boundary, monkeypatch):
    app, service, send = boundary
    definition = ToolDefinitionModel(name="future_builtin")
    service.register_global_tools([definition])
    actual_inventory = custom.get_registered_tools()
    async with Client(app) as client:
        first = await client.call_tool("future_builtin", {})
        assert first.structured_content["success"] is True
        send.reset_mock()
        # A retained SDK handler/catalog row is not authority after a built-in is registered.
        monkeypatch.setattr(
            custom,
            "get_registered_tools",
            lambda: [
                *actual_inventory,
                {"name": "future_builtin", "unity_target": "future_native"},
            ],
        )
        blocked = await client.call_tool("future_builtin", {}, raise_on_error=False)

    assert blocked.structured_content["success"] is False
    assert blocked.structured_content["code"] == "builtin_tool_requires_canonical_route"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_builtin_identity_is_rechecked_after_metadata_lookup(boundary, monkeypatch):
    _, service, send = boundary
    definition = ToolDefinitionModel(name="late_native_alias")
    actual_inventory = custom.get_registered_tools()

    async def lookup(*args, **kwargs):
        monkeypatch.setattr(
            custom,
            "get_registered_tools",
            lambda: [*actual_inventory, {"name": "late_public", "unity_target": definition.name}],
        )
        return definition

    monkeypatch.setattr(service, "get_tool_definition", lookup)
    result = await service.execute_tool("hash", definition.name, "Project@hash", {})
    assert result.success is False
    assert result.code == "builtin_tool_requires_canonical_route"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_withdrawn_catalog_cannot_authorize_a_retained_lookup_result(boundary, monkeypatch):
    _, service, send = boundary
    definition = ToolDefinitionModel(name="withdrawn_custom")
    service._register_project_tools("hash", [definition])
    original_lookup = service.get_tool_definition

    async def lookup(*args, **kwargs):
        captured = await original_lookup(*args, **kwargs)
        service._register_project_tools("hash", [], replace=True)
        return captured

    monkeypatch.setattr(service, "get_tool_definition", lookup)
    result = await service.execute_tool("hash", definition.name, "Project@hash", {})
    assert result.success is False
    assert result.data["reason"] == "custom_tool_catalog_changed"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["endpoint", "sdk"])
async def test_declared_project_custom_tool_keeps_its_schema_and_selected_target(boundary, route):
    app, service, send = boundary
    definition = ToolDefinitionModel(
        name="project_manage_scene",
        schema_version=1,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    if route == "endpoint":
        service._register_project_tools("hash", [definition])
        result = await endpoint.execute_custom_tool(Mock(), definition.name, {"value": 7})
        assert result.success is True
        invalid = await endpoint.execute_custom_tool(Mock(), definition.name, {"value": "bad"})
        assert invalid.success is False
    else:
        service.register_global_tools([definition])
        async with Client(app) as client:
            result = await client.call_tool(definition.name, {"value": 7})
            assert result.structured_content["success"] is True
            invalid = await client.call_tool(
                definition.name, {"value": "bad"}, raise_on_error=False
            )
            assert invalid.is_error
    send.assert_awaited_once()
    assert send.call_args.args[1:4] == ("Project@hash", definition.name, {"value": 7})
