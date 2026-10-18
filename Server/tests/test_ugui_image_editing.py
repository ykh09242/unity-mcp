"""Image request admission, real SDK schema, CLI boundary and scoped policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner
from fastmcp import Client, FastMCP

from cli.commands.ui import ui
from services.operation_permissions import (
    OperationPermissionError,
    authorize_operation,
    parse_permissions,
)
from services.tools.manage_ugui import manage_ugui
from services.registry import get_registered_tools


@pytest.fixture
def boundary(monkeypatch):
    calls = {
        "preflight": AsyncMock(return_value=None),
        "get_unity_instance_from_context": AsyncMock(return_value="image@fixture"),
        "send_mutation": AsyncMock(
            return_value={"success": True, "data": {"image": {"fillAmount": 0.25}}}
        ),
        "send_with_unity_instance": AsyncMock(
            side_effect=AssertionError("Image edit used read dispatch")
        ),
    }
    for name, value in calls.items():
        monkeypatch.setattr(f"services.tools.manage_ugui.{name}", value)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties",
    [
        {},
        {"sprite": {"path": "../outside.png"}},
        {"sprite": []},
        {"sprite": {"path": "Assets/a.png", "name": ""}},
        {"sprite": {"path": "Assets/a.png", "unknown": 1}},
        {"fillAmount": True},
        {"fillAmount": 1.01},
        {"fillAmount": float("inf")},
        {"fillOrigin": 4},
        {"fillOrigin": False},
        {"fillMethod": "unknown"},
        {"type": "Simple", "fillAmount": 0.5},
        {"type": "Filled", "fillMethod": "Vertical", "fillOrigin": 2},
        {"type": "Tiled", "preserveAspect": True},
        {"fillCenter": "true"},
        {"type": "Sliced", "sprite": None},
    ],
)
async def test_invalid_image_request_stops_before_preflight_or_transport(boundary, properties):
    # Given: even harmless probes are forbidden for a malformed request.
    # When
    result = await manage_ugui(
        SimpleNamespace(), action="set_image", target=123, properties=properties
    )
    # Then
    assert result["success"] is False
    for call in boundary.values():
        call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties",
    [
        {"sprite": None},
        {"sprite": "Assets/Images/icon.asset"},
        {"sprite": {"path": "Assets/Images/icons.asset", "name": "상태"}},
        {"type": "Filled", "fillMethod": "Horizontal", "fillOrigin": 1, "fillAmount": 0.25},
    ],
)
async def test_image_request_uses_one_existing_mutation_path_and_returns_readback(
    boundary, properties
):
    # Given / When
    result = await manage_ugui(
        SimpleNamespace(), action="set_image", target=-123, properties=properties
    )
    # Then: the native readback is preserved rather than replaced by the request.
    assert result["success"] is True, result
    assert result["data"]["image"]["fillAmount"] == 0.25
    boundary["preflight"].assert_awaited_once()
    assert boundary["preflight"].await_args.kwargs["requires_no_tests"] is True
    boundary["send_mutation"].assert_awaited_once()
    assert boundary["send_mutation"].await_args.args[1:3] == ("image@fixture", "manage_ugui")
    assert boundary["send_mutation"].await_args.args[3]["properties"] == properties
    boundary["send_with_unity_instance"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_real_sdk_publishes_and_dispatches_set_image(boundary, mode):
    # Given: a real isolated SDK registration and both supported initialization modes.
    server = FastMCP("ui15")
    entry = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server.tool(name=entry["name"], **entry["kwargs"])(manage_ugui)
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        tool = next(tool for tool in tools if tool.name == "manage_ugui")
        schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
        assert "set_image" in schema["properties"]["action"]["enum"]
        # When
        response = await client.call_tool(
            "manage_ugui", {"action": "set_image", "target": 123, "properties": {"sprite": None}}
        )
    # Then
    assert not response.is_error
    boundary["send_mutation"].assert_awaited_once()


@pytest.mark.parametrize("native", [False, True])
def test_scoped_image_edit_requires_edit_independently_of_other_grants(native):
    # Given / When / Then: both public and final canonical native admission share this policy.
    for grant in ("read", "test", "run"):
        authority = parse_permissions({"unity_mcp_permissions": [grant]}, strict=True)
        with pytest.raises(OperationPermissionError):
            authorize_operation(authority, "manage_ugui", {"action": "set_image"}, native=native)
    edit = parse_permissions({"unity_mcp_permissions": ["edit"]}, strict=True)
    authorize_operation(edit, "manage_ugui", {"action": "set_image"}, native=native)


@pytest.mark.parametrize(
    "payload", ["null", "[]", "{bad", '{"fillAmount":NaN}', '{"fillAmount":1e309}']
)
def test_cli_invalid_properties_does_not_read_config_or_send(monkeypatch, payload):
    # Given
    def forbidden(*args, **kwargs):
        raise AssertionError("Malformed CLI request reached config or transport")

    monkeypatch.setattr("cli.commands.ui.get_config", forbidden)
    monkeypatch.setattr("cli.commands.ui.run_command", forbidden)
    # When / Then
    result = CliRunner().invoke(ui, ["set-image", "Canvas/Icon", "--properties", payload])
    assert result.exit_code == 2


def test_cli_valid_properties_dispatches_one_canonical_image_request(monkeypatch):
    # Given
    calls = []
    monkeypatch.setattr("cli.commands.ui.get_config", lambda: SimpleNamespace(format="json"))

    def send(tool, params, config):
        calls.append((tool, params))
        return {"success": True, "data": {"image": {"preserveAspect": True}}}

    monkeypatch.setattr("cli.commands.ui.run_command", send)
    # When
    result = CliRunner().invoke(
        ui, ["set-image", "Canvas/Icon", "--properties", '{"preserveAspect":true}']
    )
    # Then
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "manage_ugui",
            {
                "action": "set_image",
                "target": "Canvas/Icon",
                "properties": {"preserveAspect": True},
            },
        )
    ]
