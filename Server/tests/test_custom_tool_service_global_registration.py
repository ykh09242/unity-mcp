import pytest
from fastmcp import FastMCP

from models.models import ToolDefinitionModel, ToolParameterModel
from services.custom_tool_service import CustomToolService


@pytest.mark.asyncio
async def test_global_tool_keeps_parameter_annotations_through_wrappers():
    mcp = FastMCP("global-annotations")
    service = CustomToolService(mcp)
    service.register_global_tools(
        [
            ToolDefinitionModel(
                name="my_tool",
                description="My tool",
                parameters=[
                    ToolParameterModel(name="action", type="string"),
                    ToolParameterModel(
                        name="count", type="integer", required=False, default_value="3"
                    ),
                ],
            )
        ]
    )

    tool = next(tool for tool in await mcp.list_tools() if tool.name == "my_tool")
    schema = tool.parameters
    assert set(schema["properties"]) == {"action", "count", "unity_instance"}
    assert schema["properties"]["action"]["type"] == "string"
    assert {item["type"] for item in schema["properties"]["count"]["anyOf"]} == {
        "integer",
        "null",
    }
    assert schema["properties"]["count"]["default"] == 3
    assert schema["required"] == ["action"]
