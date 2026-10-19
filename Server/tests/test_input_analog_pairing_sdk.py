"""Real SDK contracts for bounded gamepad, one-shot wheel and explicit PlayerInput routing."""

from importlib import import_module

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
import pytest

from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_analog_pairing_values_and_read_diagnostics_preserve_no_replay_contract(monkeypatch):
    # Given the real production wrapper with the selected-instance transport captured.
    module = import_module("services.tools.manage_input")
    sent = []
    reply = {"success": True, "data": {"delivery_eligible": False, "paired_device_ids": [0, 12]}}

    async def selected(_ctx):
        return "InputProject@owned"

    async def send(_send, instance, command, params, **kwargs):
        sent.append((instance, command, params, kwargs))
        return reply

    monkeypatch.setattr(module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("analog-input")
    wrapped = log_execution("manage_input", "Tool")(module.manage_input)
    server.tool(name="manage_input")(telemetry_tool("manage_input")(wrapped))
    requests = [
        {"action": "gamepad", "control": "leftStick", "value": [-0.25, 0.75], "frames": 3},
        {"action": "gamepad", "control": "rightTrigger", "value": 0.0, "frames": 2},
        {"action": "gamepad", "control": "buttonSouth", "state": "release"},
        {"action": "wheel", "delta": [0, -120]},
        {"action": "player_status", "target": -71},
        {"action": "pair_player", "target": "/Player/Selected", "device": "gamepad"},
        {"action": "release_player", "target": -71},
    ]
    # When modern and legacy clients send every new action.
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            for arguments in requests:
                result = await client.call_tool("manage_input", arguments)
                expected = dict(arguments)
                if arguments["action"] == "gamepad":
                    expected.setdefault("state", "press")
                    expected.setdefault("frames", 1)
                # Then exact values and target identity survive, with reload replay disabled.
                assert sent[-1] == (
                    "InputProject@owned",
                    "manage_input",
                    expected,
                    {"retry_on_reload": False},
                )
                assert result.structured_content == reply


@pytest.mark.anyio
async def test_invalid_analog_requests_never_reach_transport(monkeypatch):
    # Given real SDK validation and an unused native transport.
    module = import_module("services.tools.manage_input")
    sent = []

    async def send(*args, **kwargs):
        sent.append((args, kwargs))
        return {"success": True}

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("invalid-analog")
    server.tool(name="manage_input")(module.manage_input)
    invalid = [
        {"action": "gamepad", "control": "leftStick", "value": [2, 0]},
        {"action": "gamepad", "control": "leftStick", "value": 0.2},
        {"action": "gamepad", "control": "leftStick", "value": [True, 0]},
        {"action": "gamepad", "control": "leftTrigger", "value": -0.1},
        {"action": "gamepad", "control": "leftTrigger", "value": [0, 1]},
        {"action": "gamepad", "control": "leftTrigger", "value": True},
        {"action": "gamepad", "control": "leftTrigger", "state": "release", "value": 0},
        {"action": "gamepad", "control": "buttonSouth", "value": 1},
        {"action": "gamepad", "control": "arbitraryControl", "value": 0},
        {"action": "gamepad", "control": "leftStick", "state": "move", "value": [0, 1]},
        {"action": "wheel", "delta": [0, 10001]},
        {"action": "wheel", "delta": [0, True]},
        {"action": "pair_player"},
        {"action": "pair_player", "target": -1, "device": "physical"},
        {"action": "player_status", "target": True},
        {"action": "release_player"},
    ]
    # When malformed input crosses the schema and action-specific parser.
    async with Client(server) as client:
        for arguments in invalid:
            try:
                result = await client.call_tool("manage_input", arguments)
            except ToolError:
                continue
            assert result.structured_content["success"] is False
    # Then no rejected request can mutate native device state.
    assert sent == []
