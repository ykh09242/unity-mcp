"""Guarded Sprite geometry envelopes and registered SDK behavior."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool

from services.tools.manage_sprite import manage_sprite


@pytest.fixture
def wire(monkeypatch):
    sender = AsyncMock(return_value={"success": True, "revision": "after"})
    monkeypatch.setattr(
        "services.tools.manage_sprite.get_unity_instance_from_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("services.tools.manage_sprite.send_with_unity_instance", sender)
    return sender


def request(**changes):
    return {
        "action": "set_geometry",
        "path": "Assets/Sheet.png",
        "guid": "a" * 32,
        "sprite_id": "b" * 32,
        "file_id": "21300000",
        "expected_revision": "before",
        "geometry": {
            "rect": [0, 0, 16, 16],
            "pivot": [0.25, 0.75],
            "border": [1, 2, 3, 4],
            "outlines": [[[-7, -7], [7, -7], [0, 7]]],
        },
        **changes,
    }


def test_geometry_write_preserves_exact_identity_and_zero_values(wire):
    # Given an exact identity from a previous native read.
    values = request(geometry={"pivot": [0, 1]})
    # When the public wrapper dispatches the guarded edit.
    result = asyncio.run(manage_sprite(SimpleNamespace(), **values))
    # Then no selector/value is coerced or dropped.
    assert result["success"] is True
    assert wire.call_args.args[3] == values


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_revision": None},
        {"guid": None},
        {"sprite_id": ""},
        {"file_id": "0"},
        {"geometry": {}},
        {"geometry": {"name": "Replacement"}},
        {"geometry": {"pivot": [True, 0]}},
        {"geometry": {"pivot": ["0", 0]}},
        {"geometry": {"pivot": [float("nan"), 0]}},
        {"geometry": {"pivot": [0]}},
        {"geometry": {"border": [0, -1, 0, 0]}},
        {"geometry": {"outlines": [[[0, 0], [1, 1]]]}},
    ],
)
def test_malformed_geometry_refuses_before_transport(wire, changes):
    # Given a malformed or incomplete guarded request.
    values = request(**changes)
    # When boundary validation runs.
    result = asyncio.run(manage_sprite(SimpleNamespace(), **values))
    # Then Unity receives no mutation request.
    assert result["success"] is False
    wire.assert_not_called()


@pytest.mark.asyncio
async def test_actual_sdk_accepts_geometry_and_refuses_scalar_coercion(wire):
    # Given the actual registered FastMCP callable.
    server = FastMCP("sprite-geometry")
    wrapped = log_execution("manage_sprite", "Tool")(manage_sprite)
    wrapped = telemetry_tool("manage_sprite")(wrapped)
    server.tool(name="manage_sprite")(wrapped)
    async with Client(server, mode="2026-07-28") as client:
        # When complete geometry and strict identity strings cross the SDK.
        result = await client.call_tool("manage_sprite", request())
        # Then all geometry survives the registered schema.
        assert json.loads(result.content[0].text)["success"] is True
        assert wire.call_args.args[3] == request()
        wire.reset_mock()
        result = await client.call_tool(
            "manage_sprite", request(geometry={"rect": [0, 0, True, 10]})
        )
        assert json.loads(result.content[0].text)["success"] is False
        wire.assert_not_called()
        for invalid in (True, "1", 1.5):
            with pytest.raises(ToolError):
                await client.call_tool(
                    "manage_sprite",
                    {"action": "get_geometry", "path": "Assets/Sheet.png", "page_size": invalid},
                )
            wire.assert_not_called()
        with pytest.raises(ToolError):
            await client.call_tool("manage_sprite", request(file_id=21300000))
        wire.assert_not_called()


def test_geometry_continuation_requires_revision(wire):
    # Given a continuation page without a revision.
    # When the public boundary receives it, then it refuses before transport.
    result = asyncio.run(
        manage_sprite(SimpleNamespace(), action="get_geometry", path="Assets/Sheet.png", cursor=1)
    )
    assert result["success"] is False
    wire.assert_not_called()
