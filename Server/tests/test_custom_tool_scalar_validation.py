import importlib
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from pydantic import ValidationError

from core.config import config
from models.models import ToolDefinitionModel, ToolParameterModel
import services.custom_tool_service as module
from services.custom_tool_service import CustomToolService


@pytest.mark.parametrize("payload", [
    {"required": 1}, {"required": "false"}, {"required": None},
    {"type": "boolean", "default_value": "flase"},
    {"type": "boolean", "default_value": "1"},
    {"type": "boolean", "default_value": "yes"},
    {"type": "integer", "default_value": "1.5"},
    {"type": "number", "default_value": "NaN"},
    {"type": "number", "default_value": "Infinity"},
    {"type": "number", "default_value": "-inf"},
    {"type": "array", "default_value": "{}"},
    {"type": "object", "default_value": "[]"},
])
def test_malformed_parameter_descriptors_are_rejected(payload):
    with pytest.raises(ValidationError):
        ToolParameterModel(name="value", **payload)


@pytest.mark.parametrize("payload", [
    {"requires_polling": "false"}, {"requires_polling": 0},
    {"structured_output": 1}, {"structured_output": "true"},
    {"max_poll_seconds": True}, {"max_poll_seconds": "3"},
    {"max_poll_seconds": 3.0},
])
def test_malformed_tool_metadata_is_rejected(payload):
    with pytest.raises(ValidationError):
        ToolDefinitionModel(name="custom_fixture", **payload)


@pytest.mark.parametrize("ptype,value,expected", [
    ("integer", "3", 3), ("number", "2.5", 2.5),
    ("boolean", "true", True), ("boolean", "False", False),
    ("array", "[1,2]", [1, 2]), ("object", '{"flag":false}', {"flag": False}),
])
def test_valid_legacy_defaults_remain_supported(ptype, value, expected):
    parameter = ToolParameterModel(name="value", type=ptype, required=False, default_value=value)
    service = object.__new__(CustomToolService)
    parsed = service._coerce_default(parameter.default_value, parameter.type)
    assert parsed == expected and type(parsed) is type(expected)


@pytest.fixture
def custom_server(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("custom-scalars")
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    definition = ToolDefinitionModel(name="custom_fixture", parameters=[
        ToolParameterModel(name="flag", type="boolean", description="Boolean input"),
        ToolParameterModel(name="count", type="integer", description="Integer input"),
        ToolParameterModel(name="ratio", type="number", description="Numeric input"),
        ToolParameterModel(name="optional", type="integer", required=False, default_value="3"),
        ToolParameterModel(name="enabled", type="boolean", required=False, default_value="false"),
    ])
    service._register_tool("project", definition)
    service.register_global_tools([definition])
    scoped = importlib.import_module("services.tools.execute_custom_tool")
    server.tool(name="execute_custom_tool")(scoped.execute_custom_tool)
    for target in (module, scoped):
        monkeypatch.setattr(target, "get_unity_instance_from_context", AsyncMock(return_value="Project@hash"))
        monkeypatch.setattr(target, "get_user_id_from_context", AsyncMock(return_value=None))
        monkeypatch.setattr(target, "resolve_project_id_for_unity_instance", lambda _: "project")
    send = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    yield server, service, send


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("route", ["global", "scoped"])
async def test_actual_custom_tool_routes_validate_before_transport(custom_server, mode, route):
    server, service, send = custom_server
    valid = {"flag": False, "count": 0, "ratio": 1.5}
    invalid = [
        {"flag": 0}, {"flag": "false"}, {"flag": None},
        {"count": True}, {"count": 3.0}, {"count": "3"}, {"count": None},
        {"ratio": True}, {"ratio": "1.5"}, {"optional": True}, {"enabled": 1},
        {"ratio": float("nan")}, {"ratio": float("inf")}, {"ratio": float("-inf")},
    ]
    async with Client(server, mode=mode) as client:
        async def call(arguments):
            name = "custom_fixture" if route == "global" else "execute_custom_tool"
            payload = arguments if route == "global" else {"tool_name": "custom_fixture", "parameters": arguments}
            return await client.call_tool(name, payload, raise_on_error=False)

        tools = await client.list_tools()
        schema = next(tool for tool in tools if tool.name == "custom_fixture").input_schema
        assert schema["properties"]["flag"]["description"] == "Boolean input"
        assert set(schema["required"]) == {"flag", "count", "ratio"}
        for wrong in invalid:
            result = await call(valid | wrong)
            assert result.is_error or result.data.success is False
            send.assert_not_awaited()
        for missing in ("flag", "count", "ratio"):
            result = await call({key: value for key, value in valid.items() if key != missing})
            assert result.is_error or result.data.success is False
            send.assert_not_awaited()
        result = await call(valid)
        assert not result.is_error
        payload = send.call_args.args[3]
        assert payload == valid | {"optional": 3, "enabled": False}
        assert type(payload["flag"]) is bool and type(payload["count"]) is int
        assert type(payload["ratio"]) is float
        await call(valid | {"optional": None, "enabled": None})
        expected = valid if route == "global" else valid | {"optional": None, "enabled": None}
        assert send.call_args.args[3] == expected
        await call(valid | {"ratio": 2})
        assert send.call_args.args[3]["ratio"] == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), float("-inf")])
async def test_scoped_nonfinite_values_fail_before_transport(custom_server, ratio):
    _, service, send = custom_server
    result = await service.execute_tool("project", "custom_fixture", "Project@hash",
                                        {"flag": True, "count": 3, "ratio": ratio})
    assert result.success is False
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptorless_commands_preserve_extra_values(custom_server):
    _, service, send = custom_server
    service._register_tool("project", ToolDefinitionModel(name="legacy_custom"))
    result = await service.execute_tool("project", "legacy_custom", "Project@hash",
                                        {"action": "start", "value": True, "explicit_null": None})
    assert result.success is True
    assert send.call_args.args[3] == {"action": "start", "value": True, "explicit_null": None}


@pytest.mark.asyncio
async def test_scoped_omits_absent_optional_null_defaults(custom_server):
    _, service, send = custom_server
    service._register_tool("project", ToolDefinitionModel(name="optional_fixture", parameters=[
        ToolParameterModel(name="nullable", type="integer", required=False),
        ToolParameterModel(name="defaulted", type="integer", required=False, default_value="3"),
    ]))
    result = await service.execute_tool("project", "optional_fixture", "Project@hash", {})
    assert result.success is True
    assert send.call_args.args[3] == {"defaulted": 3}
    await service.execute_tool("project", "optional_fixture", "Project@hash", {"nullable": None, "defaulted": None})
    assert send.call_args.args[3] == {"nullable": None, "defaulted": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["_count", "model_dump"])
async def test_valid_python_parameter_names_keep_strict_scoped_contract(custom_server, name):
    _, service, send = custom_server
    service._register_tool("project", ToolDefinitionModel(name="named_fixture", parameters=[
        ToolParameterModel(name=name, type="integer"),
    ]))
    invalid = await service.execute_tool("project", "named_fixture", "Project@hash", {name: True})
    assert invalid.success is False
    send.assert_not_awaited()
    valid = await service.execute_tool("project", "named_fixture", "Project@hash", {name: 3})
    assert valid.success is True
    assert send.call_args.args[3] == {name: 3}


@pytest.mark.asyncio
async def test_input_model_cache_tracks_schema_and_bounds_retained_models(custom_server):
    _, service, send = custom_server
    definition = ToolDefinitionModel(name="cached_fixture", parameters=[
        ToolParameterModel(name="value", type="integer", required=False, default_value="3"),
    ])
    service._register_tool("project", definition)
    await service.execute_tool("project", "cached_fixture", "Project@hash", {})
    cached = service._get_input_model(definition)
    assert service._get_input_model(definition) is cached
    assert send.call_args.args[3] == {"value": 3}
    definition.parameters[0].default_value = "5"
    await service.execute_tool("project", "cached_fixture", "Project@hash", {})
    assert service._get_input_model(definition) is not cached
    assert send.call_args.args[3] == {"value": 5}
    definition.parameters[0].type = "boolean"
    definition.parameters[0].default_value = "false"
    rejected = await service.execute_tool("project", "cached_fixture", "Project@hash", {"value": 5})
    assert rejected.success is False
    for index in range(140):
        service._get_input_model(ToolDefinitionModel(name="bounded_fixture", parameters=[
            ToolParameterModel(name="value", type="integer", required=False, default_value=str(index)),
        ]))
    assert len(service._input_models) == 128
    assert cached not in service._input_models.values()
    invalid = ToolDefinitionModel(name="invalid_fixture", parameters=[ToolParameterModel(name="ctx")])
    with pytest.raises(ValueError):
        service._get_input_model(invalid)
    assert len(service._input_models) == 128


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_sync_preserves_good_descriptors_and_reports_invalid_fields(custom_server, monkeypatch, caplog, transport):
    from services.tools import sync_tool_visibility_from_unity
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry

    _, service, _ = custom_server
    monkeypatch.setattr(config, "transport_mode", transport)
    registry = PluginRegistry()
    await registry.register("session", "project", "hash", "6000.0")
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_resolve_session_id", AsyncMock(return_value="session"))
    monkeypatch.setattr(PluginHub, "_refresh_server_tool_visibility", AsyncMock())
    monkeypatch.setattr(PluginHub, "_sync_server_tool_visibility", lambda _: None)
    bad_default = "private-malformed-default"
    descriptors = [
        {"name": "bad_custom", "is_built_in": False, "enabled": True, "parameters": [
            {"name": "flag", "type": "boolean", "default_value": bad_default},
        ]},
        {"name": "good_custom", "is_built_in": False, "enabled": True, "parameters": [
            {"name": "count", "type": "integer", "required": False, "default_value": "3"},
        ]},
    ]
    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance",
                        AsyncMock(return_value={"success": True, "data": {"tools": descriptors}}))

    result = await sync_tool_visibility_from_unity("Project@hash", notify=False)

    assert result["synced"] is True and result["custom_tool_count"] == 1
    assert "good_custom" in service._global_tools and "bad_custom" not in service._global_tools
    if transport == "http":
        session = await registry.get_session("session")
        assert set(session.tools) == {"good_custom"}
    assert "bad_custom" in caplog.text and "default_value" in caplog.text and "value_error" in caplog.text
    assert bad_default not in caplog.text
