"""Recording validation, exact routing, output preservation and CLI contracts."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cli.commands.scenario_recording import scenario_recording
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from models.models import MCPResponse
from models.scenario_recording import ScenarioRecordingCommand
from pydantic import TypeAdapter, ValidationError
from services.registry.tool_registry import get_registered_tools
from services.tools.manage_scenario_recording import manage_scenario_recording


def test_substantial_recording_request_keeps_explicit_capacity():
    result = ScenarioRecordingCommand(
        action="start", name="large", max_events=127, duration_seconds=3600
    ).wire_parameters()
    assert result["max_events"] == 127
    assert result["duration_seconds"] == 3600


RECORDING_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def route(monkeypatch):
    select = AsyncMock(return_value="Selected@recording")
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {"recording_id": RECORDING_ID, "status": "recording", "requires_review": True},
        }
    )
    monkeypatch.setattr(
        "services.tools.manage_scenario_recording.get_unity_instance_from_context", select
    )
    monkeypatch.setattr("services.tools.manage_scenario_recording.send_with_unity_instance", send)
    return SimpleNamespace(select=select, send=send)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,expected",
    [
        (
            {"action": "start", "name": "menu-draft"},
            {
                "action": "start",
                "name": "menu-draft",
                "max_events": 31,
                "duration_seconds": 300,
            },
        ),
        ({"action": "status"}, {"action": "status"}),
        (
            {"action": "stop", "recording_id": RECORDING_ID},
            {"action": "stop", "recording_id": RECORDING_ID},
        ),
        (
            {"action": "cancel", "recording_id": RECORDING_ID},
            {"action": "cancel", "recording_id": RECORDING_ID},
        ),
        (
            {
                "action": "checkpoint",
                "recording_id": RECORDING_ID,
                "checkpoint_name": "after-menu",
            },
            {
                "action": "checkpoint",
                "recording_id": RECORDING_ID,
                "checkpoint_name": "after-menu",
            },
        ),
    ],
)
async def test_dispatches_once_without_reload_replay(route, arguments, expected):
    result = await manage_scenario_recording(SimpleNamespace(), **arguments)
    assert result == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.args[1:] == (
        "Selected@recording",
        "manage_scenario_recording",
        expected,
    )
    assert route.send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "start"},
        {"action": "start", "name": "menu-draft", "max_events": True},
        {"action": "start", "name": "menu-draft", "max_events": 128},
        {"action": "start", "name": "menu-draft", "duration_seconds": 3601},
        {"action": "start", "name": "../menu"},
        {"action": "status", "name": "menu-draft"},
        {"action": "stop"},
        {"action": "cancel", "recording_id": "short"},
        {"action": "checkpoint", "recording_id": RECORDING_ID},
        {"action": "checkpoint", "recording_id": RECORDING_ID, "checkpoint_name": "../shot"},
        {"action": "stop", "recording_id": RECORDING_ID, "max_events": 1},
    ],
)
async def test_invalid_request_has_no_side_effect(route, arguments):
    result = await manage_scenario_recording(SimpleNamespace(), **arguments)
    assert result["success"] is False
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Menu Flow", "메뉴-흐름", "../menu"])
async def test_invalid_recording_slug_has_an_actionable_example(route, name):
    result = await manage_scenario_recording(SimpleNamespace(), action="start", name=name)
    assert result["success"] is False
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in result["hint"]
    assert "menu-flow-01" in result["hint"]
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.asyncio
async def test_preserves_interrupted_draft_diagnostics(route):
    route.send.return_value = {
        "success": True,
        "data": {
            "status": "interrupted",
            "reason": "assembly_reload",
            "requires_review": True,
            "saved": False,
            "diagnostics": ["Duplicate ID omitted"],
            "scenario": {
                "name": "menu-draft",
                "draft": True,
                "steps": [{"name": "Menu", "action": "load_scene", "scene": "Assets/Menu.unity"}],
            },
        },
    }
    assert await manage_scenario_recording(SimpleNamespace(), "status") == route.send.return_value


@pytest.mark.asyncio
async def test_legacy_failure_is_preserved(route):
    route.send.return_value = MCPResponse(success=False, error="Unavailable", hint="retry")
    assert await manage_scenario_recording(SimpleNamespace(), "status") == {
        "success": False,
        "error": "Unavailable",
        "hint": "retry",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_actual_sdk_dispatch_and_schema(route, mode):
    server = FastMCP("scenario-recording")
    metadata = next(
        tool for tool in get_registered_tools() if tool["func"] is manage_scenario_recording
    )
    server.tool(**metadata["kwargs"])(manage_scenario_recording)
    registered = (await server.list_tools(run_middleware=False))[0]
    assert registered.parameters["properties"]["max_events"]["anyOf"][0]["maximum"] == 127
    assert "menu-flow-01" in registered.parameters["properties"]["name"]["description"]
    assert "UnityEngine.Input" in metadata["description"]
    assert "raw observation" in metadata["description"]
    async with Client(server, mode=mode) as client:
        advertised = (await client.list_tools())[0]
        hints = advertised.annotations.model_dump(by_alias=True)
        assert hints["readOnlyHint"] is False
        assert hints["destructiveHint"] is True
        result = await client.call_tool(
            "manage_scenario_recording", {"action": "start", "name": "menu-draft"}
        )
        assert json.loads(result.content[0].text) == route.send.return_value
        route.send.assert_awaited_once()


def test_strict_command_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ScenarioRecordingCommand.model_validate({"action": "status", "auto_run": True})
    schema = TypeAdapter(ScenarioRecordingCommand).json_schema()
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (
            ["start", "menu-draft", "--max-events", "3", "--duration-seconds", "20"],
            {"action": "start", "name": "menu-draft", "max_events": 3, "duration_seconds": 20},
        ),
        (["status"], {"action": "status"}),
        (["stop", RECORDING_ID], {"action": "stop", "recording_id": RECORDING_ID}),
        (["cancel", RECORDING_ID], {"action": "cancel", "recording_id": RECORDING_ID}),
        (
            ["checkpoint", RECORDING_ID, "after-menu"],
            {
                "action": "checkpoint",
                "recording_id": RECORDING_ID,
                "checkpoint_name": "after-menu",
            },
        ),
    ],
)
def test_cli_routes_once(monkeypatch, arguments, expected):
    calls = []
    config = SimpleNamespace(format="json")
    monkeypatch.setattr("cli.commands.scenario_recording.get_config", lambda: config)
    monkeypatch.setattr(
        "cli.commands.scenario_recording.run_command",
        lambda *args: calls.append(args) or {"success": True, "data": {"requires_review": True}},
    )
    result = CliRunner().invoke(scenario_recording, arguments)
    assert result.exit_code == 0, result.output
    assert calls == [("manage_scenario_recording", expected, config)]
    assert json.loads(result.output)["data"]["requires_review"] is True
