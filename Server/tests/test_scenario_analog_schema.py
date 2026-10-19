"""Public saved scenarios match native analog command bounds without coercing controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from models.play_scenarios import PlayScenario, ScenarioInput
from pydantic import ValidationError
import pytest
from services.tools.manage_play_scenario import manage_play_scenario


def definition(command):
    return {
        "name": "analog-case",
        "steps": [
            {"name": "Anchor", "action": "load_scene", "scene": "Assets/Owned.unity"},
            {"name": "Input", "action": "input", "input": command},
        ],
    }


VALID = [
    {"action": "gamepad", "control": "leftStick", "value": [-1, 1]},
    {"action": "gamepad", "control": "rightStick", "value": [0.25, -0.75], "frames": 600},
    {"action": "gamepad", "control": "leftTrigger", "value": 0},
    {"action": "gamepad", "control": "rightTrigger", "value": 1},
    {"action": "gamepad", "control": "buttonSouth"},
    {"action": "gamepad", "control": "leftStick", "state": "release"},
    {"action": "wheel", "delta": [-10000, 10000]},
]


@pytest.mark.parametrize("command", VALID)
def test_scenario_analog_command_preserves_authored_fields(command):
    scenario = PlayScenario.model_validate(definition(command))
    assert scenario.steps[1].input.model_dump(mode="json") == command


@pytest.mark.parametrize(
    "command",
    [
        {"action": "gamepad", "control": "leftStick"},
        {"action": "gamepad", "control": "leftStick", "value": [0, 1.1]},
        {"action": "gamepad", "control": "leftStick", "value": [True, 0]},
        {"action": "gamepad", "control": "leftStick", "value": [0]},
        {"action": "gamepad", "control": "leftTrigger", "value": -0.1},
        {"action": "gamepad", "control": "rightTrigger", "value": [0, 1]},
        {"action": "gamepad", "control": "buttonSouth", "value": 1},
        {"action": "gamepad", "control": "buttonSouth", "state": "move"},
        {"action": "gamepad", "control": "buttonSouth", "state": "release", "frames": 1},
        {"action": "gamepad", "control": "leftStick", "state": "release", "value": [0, 0]},
        {"action": "gamepad", "control": "unknown"},
        {"action": "gamepad", "control": "buttonSouth", "value": None},
        {"action": "wheel", "delta": [0, float("inf")]},
        {"action": "wheel", "delta": [0, 10001]},
        {"action": "wheel", "delta": [0, True]},
        {"action": "wheel", "delta": [0, 1], "frames": 1},
        {"action": "wheel", "delta": [0, 1], "state": "press"},
        {"action": "wheel", "delta": None},
        {"action": "pair_player", "target": "Player"},
    ],
)
def test_invalid_scenario_analog_command_is_rejected_before_save(command):
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition(command))


@pytest.mark.parametrize(
    "control",
    [
        "buttonSouth",
        "buttonNorth",
        "buttonEast",
        "buttonWest",
        "leftShoulder",
        "rightShoulder",
        "leftStickPress",
        "rightStickPress",
        "start",
        "select",
        "dpadUp",
        "dpadDown",
        "dpadLeft",
        "dpadRight",
    ],
)
def test_every_native_gamepad_button_is_supported(control):
    command = {"action": "gamepad", "control": control, "state": "press"}
    assert ScenarioInput.model_validate(command).model_dump(mode="json") == command


@pytest.mark.asyncio
async def test_real_sdk_saves_analog_scenario_and_invalid_payload_has_no_dispatch(monkeypatch):
    select = AsyncMock(return_value="owned")
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        "services.tools.manage_play_scenario.get_unity_instance_from_context", select
    )
    monkeypatch.setattr("services.tools.manage_play_scenario.send_with_unity_instance", send)
    server = FastMCP("scenario-analog")
    server.tool(manage_play_scenario)
    async with Client(server) as client:
        schema = (await client.list_tools())[0].input_schema
        input_schema = schema["properties"]["scenario"]["anyOf"][0]["properties"]["steps"]["items"][
            "properties"
        ]["input"]["anyOf"][0]
        assert {"control", "value", "delta"} <= input_schema["properties"].keys()
        for command in VALID:
            await client.call_tool(
                "manage_play_scenario", {"action": "save", "scenario": definition(command)}
            )
    assert [call.args[3]["scenario"]["steps"][1]["input"] for call in send.call_args_list] == VALID
    select.reset_mock()
    send.reset_mock()
    invalid = definition({"action": "wheel", "delta": [False, 1]})
    result = await manage_play_scenario(SimpleNamespace(), action="save", scenario=invalid)
    assert result["success"] is False
    select.assert_not_called()
    send.assert_not_called()
