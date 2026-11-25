"""Verify required fields through the registered MCP discovery surfaces."""

from fastmcp import Client, FastMCP
from jsonschema import Draft202012Validator
import pytest

from core.config import config
from services.tools import register_all_tools


@pytest.mark.asyncio
async def test_action_requirements_match_public_schemas(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("action-inputs")
    register_all_tools(server, project_scoped_tools=False)
    path = {"path": "Assets/Controls.inputactions"}
    existing = {**path, "expected_revision": "a" * 64}
    guid = "12345678-1234-1234-1234-123456789abc"
    binding = {**existing, "map_id": guid, "binding_path": "<Keyboard>/space"}
    terrain = {"target": "Ground"}
    region = {**terrain, "channel": "heights", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}
    guards = {"expected_data_guid": "0" * 32, "expected_revision": "terrain1:" + "a" * 64}
    cases = {
        "manage_input_actions": {
            "capabilities": {},
            "read": path,
            "create_asset": path,
            "edit": {**path, "commands": [{"action": "create_asset"}]},
            "add_map": {**existing, "name": "Gameplay"},
            "add_action": {**existing, "map_id": guid, "name": "Jump", "action_type": "Button"},
            "add_binding": {**binding, "action_id": guid},
            "set_binding": {**binding, "binding_id": guid},
            "remove_binding": {**existing, "map_id": guid, "binding_id": guid},
        },
        "manage_terrain": {
            "get_info": terrain,
            "get_region": region,
            "set_region": {**region, **guards, "values": [[0.1]]},
            "get_trees": {**terrain, "indices": [0]},
            "set_trees": {**terrain, **guards, "indices": [0], "values": [{"width_scale": 1}]},
            "get_layer": {**terrain, "index": 0},
            "set_layer": {
                **terrain,
                **guards,
                "index": 0,
                "layer_path": "Assets/Layer.terrainlayer",
                "layer_guid": "1" * 32,
            },
        },
        "unity_reflect": {
            "get_type": {"class_name": "Transform"},
            "get_member": {"class_name": "Transform", "member_name": "position"},
            "search": {"query": "Transform"},
        },
    }
    async with Client(server, mode="legacy") as client:
        for tool, actions in cases.items():
            response = await client.call_tool("manage_tools", {"action": "describe", "tool": tool})
            public = Draft202012Validator(response.structured_content["tool"]["input_schema"])
            for action, inputs in actions.items():
                description = await client.call_tool(
                    "manage_tools", {"action": "describe", "tool": tool, "operation": action}
                )
                result = description.structured_content
                assert set(result["operation"]["required_parameters"]) == inputs.keys(), (
                    tool,
                    action,
                )
                bound = Draft202012Validator(result["tool"]["input_schema"])
                bound.validate({"action": action, **inputs})
                wire_action = action if tool == "manage_input_actions" else action.upper()
                args = {"action": wire_action, **inputs}
                public.validate(args)
                for field in inputs:
                    assert not public.is_valid({k: v for k, v in args.items() if k != field}), (
                        tool,
                        action,
                        field,
                    )
                    assert not public.is_valid({**args, field: None}), (tool, action, field)
