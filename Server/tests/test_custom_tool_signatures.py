import inspect
from unittest.mock import AsyncMock, Mock

import pytest

import services.custom_tool_service as module
from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
from services.custom_tool_service import CustomToolService


def test_custom_tool_registration_accepts_mixed_required_parameter_order(monkeypatch):
    service = object.__new__(CustomToolService)
    service._global_tools = {}
    service._mcp = Mock()
    registered = []

    def tool(**kwargs):
        def register(handler):
            inspect.signature(handler).bind(Mock(), required="target")
            registered.append(kwargs["name"])
            return handler

        return register

    service._mcp.tool = tool
    monkeypatch.setattr(service, "_get_builtin_tool_names", lambda: set())
    definition = ToolDefinitionModel(
        name="build",
        parameters=[
            ToolParameterModel(name="optional", type="integer", required=False),
            ToolParameterModel(name="required", type="string", required=True),
        ],
    )

    service.register_global_tools([definition])

    assert registered == ["build"]
    assert service._global_tools["build"] is definition


@pytest.mark.asyncio
async def test_custom_tool_accepts_required_parameters_after_optional_parameters(monkeypatch):
    service = object.__new__(CustomToolService)
    definition = ToolDefinitionModel(
        name="build",
        parameters=[
            ToolParameterModel(name="optional", type="integer", required=False, default_value="3"),
            ToolParameterModel(name="required", type="string", required=True),
        ],
    )

    handler = service._build_global_tool_handler(definition)

    signature = inspect.signature(handler)
    bound = signature.bind(Mock(), required="target")
    bound.apply_defaults()
    assert bound.arguments["optional"] == 3
    assert list(signature.parameters) == ["ctx", "optional", "required"]
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Project@hash")
    )
    monkeypatch.setattr(module, "resolve_project_id_for_unity_instance", lambda instance: "project")
    monkeypatch.setattr(module, "get_user_id_from_context", AsyncMock(return_value="user-a"))
    monkeypatch.setattr(CustomToolService, "get_instance", lambda: service)
    service.execute_tool = AsyncMock(return_value=MCPResponse(success=True))

    response = await handler(Mock(), required="target", optional=3)

    assert response.success is True
    service.execute_tool.assert_awaited_once_with(
        "project",
        "build",
        "Project@hash",
        {"required": "target", "optional": 3},
        user_id="user-a",
        omit_nulls=True,
    )
