"""Custom-tool registration reads stable built-in declarations once per batch."""

from unittest.mock import Mock

from fastmcp import FastMCP

from core.config import config
from models.models import ToolDefinitionModel
import services.custom_tool_service as custom_tools


def test_project_registration_reads_builtin_inventory_once_per_batch(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    service = custom_tools.CustomToolService(
        FastMCP("batch-registration"), project_scoped_tools=False
    )
    old = ToolDefinitionModel(name="fixture_one", description="old")
    service._register_tool("project", old)
    tools = [
        ToolDefinitionModel(name="fixture_one", description="replacement"),
        ToolDefinitionModel(name="fixture_two"),
        ToolDefinitionModel(name="builtin"),
    ]
    inventory = Mock(return_value=[{"name": "builtin"}])
    register = Mock()
    monkeypatch.setattr(custom_tools, "get_registered_tools", inventory)
    monkeypatch.setattr(service, "_register_global_tool", register)

    registered, replaced = service._register_project_tools("project", tools, project_hash="HASH")

    assert registered == [tool.name for tool in tools]
    assert replaced == ["fixture_one"]
    assert service._project_tools["project"] == {tool.name: tool for tool in tools}
    assert service.get_project_id_for_hash("hash") == "project"
    assert [call.args[0] for call in register.call_args_list] == tools[:2]
    inventory.assert_called_once()

    # The next registration observes newly added built-ins instead of a stale cache.
    register.reset_mock()
    inventory.return_value = [{"name": "fixture_two"}]
    service._register_project_tools("project", tools)
    assert [call.args[0] for call in register.call_args_list] == [tools[0], tools[2]]
    assert inventory.call_count == 2
    service._register_project_tools("project", [])
    assert inventory.call_count == 2
