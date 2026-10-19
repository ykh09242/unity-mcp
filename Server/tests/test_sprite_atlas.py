"""Real SDK and transport-envelope coverage for explicit SpriteAtlas authoring."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool


def tool_module():
    # Given a new public domain, absence is a behavioral RED, not collection failure.
    assert importlib.util.find_spec("services.tools.manage_sprite_atlas") is not None, (
        "manage_sprite_atlas public tool is missing"
    )
    return importlib.import_module("services.tools.manage_sprite_atlas")


@pytest.fixture
def wire(monkeypatch):
    def connect():
        module = tool_module()
        sender = AsyncMock(return_value={"success": True, "data": {"revision": "after"}})
        monkeypatch.setattr(
            module, "get_unity_instance_from_context", AsyncMock(return_value="fixture")
        )
        monkeypatch.setattr(module, "send_with_unity_instance", sender)
        return module, sender

    return connect


def edit(**changes):
    return {
        "action": "configure",
        "path": "Assets/UI.spriteatlasv2",
        "guid": "a" * 32,
        "file_id": "6870788951953259653",
        "expected_revision": "before",
        "properties": {
            "include_in_build": False,
            "packing": {"enableRotation": False},
            "platforms": {
                "Standalone": {"overridden": True, "maxTextureSize": 128, "format": "RGBA32"}
            },
        },
        **changes,
    }


def test_guarded_atlas_settings_forward_exact_false_and_identity(wire):
    # Given an exact atlas identity and explicit false/zero values.
    module, sender = wire()
    values = edit()
    # When the public tool dispatches the guarded request.
    response = asyncio.run(module.manage_sprite_atlas(SimpleNamespace(), **values))
    # Then transport preserves values and the new tool route.
    assert response["success"] is True
    assert sender.call_args.args[2] == "manage_sprite_atlas"
    assert sender.call_args.args[3] == values


@pytest.mark.parametrize(
    "values",
    [
        edit(expected_revision=None),
        edit(file_id="0"),
        edit(guid="name"),
        edit(properties={"packing": {"padding": True}}),
        edit(properties={"packing": {"padding": 3}}),
        edit(properties={"texture": {"anisoLevel": "1"}}),
        edit(properties={"variant_scale": float("inf")}),
        edit(properties={"variant_scale": 10**400}),
        edit(
            action="pack", build_target="StandaloneWindows64", properties={"include_in_build": True}
        ),
        {"action": "create", "path": "Assets/New.spriteatlasv2", "properties": {}},
        {
            "action": "pack",
            "path": "Assets/UI.spriteatlasv2",
            "guid": "a" * 32,
            "file_id": "1",
            "expected_revision": "old",
        },
        edit(action="add_packables", properties={"packables": [{"path": "Assets/A.png"}]}),
    ],
)
def test_bad_atlas_envelope_refuses_before_transport(wire, values):
    # Given malformed supplied values or incomplete write preconditions.
    module, sender = wire()
    # When the public boundary preflights them.
    response = asyncio.run(module.manage_sprite_atlas(SimpleNamespace(), **values))
    # Then Unity receives no partial authoring request.
    assert response["success"] is False
    sender.assert_not_called()


@pytest.mark.asyncio
async def test_registered_atlas_sdk_preserves_settings_and_rejects_coercion(wire):
    # Given the production decorators and actual FastMCP registration.
    module, sender = wire()
    server = FastMCP("sprite-atlas")
    wrapped = telemetry_tool("manage_sprite_atlas")(
        log_execution("manage_sprite_atlas", "Tool")(module.manage_sprite_atlas)
    )
    server.tool(name="manage_sprite_atlas")(wrapped)
    async with Client(server, mode="2026-07-28") as client:
        # When all public domain actions cross the registered schema.
        for values in [
            edit(),
            {"action": "capabilities"},
            {
                "action": "read",
                "path": "Assets/UI.spriteatlasv2",
                "platforms": ["Standalone"],
                "page_size": 1,
            },
            edit(
                action="add_packables",
                properties={"packables": [{"guid": "b" * 32, "file_id": "21300000"}]},
            ),
            edit(
                action="remove_packables",
                properties={"packables": [{"guid": "b" * 32, "file_id": "21300000"}]},
            ),
            edit(action="pack", properties=None, build_target="StandaloneWindows64"),
            {
                "action": "create",
                "path": "Assets/UI.spriteatlasv2",
                "properties": {"include_in_build": False},
            },
        ]:
            result = await client.call_tool("manage_sprite_atlas", values)
            # Then exact property shapes/identities reach the transport.
            assert json.loads(result.content[0].text)["success"] is True
            assert sender.call_args.args[3] == {
                key: value for key, value in values.items() if value is not None
            }
        sender.reset_mock()
        for values in (
            edit(file_id=1),
            {"action": "read", "path": "Assets/UI.spriteatlasv2", "page_size": True},
        ):
            with pytest.raises(ToolError):
                await client.call_tool("manage_sprite_atlas", values)
        result = await client.call_tool(
            "manage_sprite_atlas", edit(properties={"texture": {"readable": "false"}})
        )
        assert json.loads(result.content[0].text)["success"] is False
        sender.assert_not_called()
