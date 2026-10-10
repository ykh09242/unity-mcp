"""Validation and single-dispatch contracts for Unity-owned play scenarios."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from models.models import MCPResponse
from models.play_scenarios import PlayScenario, PlayScenarioCommand, ScenarioStep
from pydantic import TypeAdapter, ValidationError
from services.tools.manage_play_scenario import manage_play_scenario

JOB_ID = "0123456789abcdef0123456789abcdef"
DEFINITION = {
    "name": "menu-start",
    "steps": [
        {"name": "Menu", "action": "load_scene", "scene": "Assets/Scenes/Menu.unity"},
        {"name": "Start", "action": "click_ui", "target": "Canvas/Start"},
        {"name": "Game", "action": "wait_scene", "scene": "Assets/Scenes/Game.unity"},
        {"name": "Player", "action": "wait_object", "target": "Player"},
    ],
}


@pytest.fixture
def route(monkeypatch):
    select = AsyncMock(return_value="Selected@owned")
    send = AsyncMock(return_value={"success": True, "data": {"job_id": JOB_ID, "status": "queued"}})
    monkeypatch.setattr(
        "services.tools.manage_play_scenario.get_unity_instance_from_context", select
    )
    monkeypatch.setattr("services.tools.manage_play_scenario.send_with_unity_instance", send)
    return SimpleNamespace(select=select, send=send)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,expected",
    [
        (
            {"action": "save", "scenario": DEFINITION},
            {
                "action": "save",
                "scenario": PlayScenario.model_validate(DEFINITION).model_dump(exclude_none=True),
            },
        ),
        ({"action": "get", "name": "menu-start"}, {"action": "get", "name": "menu-start"}),
        ({"action": "list"}, {"action": "list"}),
        ({"action": "delete", "name": "menu-start"}, {"action": "delete", "name": "menu-start"}),
        (
            {"action": "run", "name": "menu-start"},
            {"action": "run", "name": "menu-start", "repeat_count": 1, "timeout_seconds": 300},
        ),
        (
            {
                "action": "run",
                "name": "menu-start",
                "job_id": JOB_ID,
                "repeat_count": 10,
                "timeout_seconds": 1800,
            },
            {
                "action": "run",
                "name": "menu-start",
                "job_id": JOB_ID,
                "repeat_count": 10,
                "timeout_seconds": 1800,
            },
        ),
        ({"action": "status", "job_id": JOB_ID}, {"action": "status", "job_id": JOB_ID}),
        ({"action": "cancel", "job_id": JOB_ID}, {"action": "cancel", "job_id": JOB_ID}),
    ],
)
async def test_action_dispatches_once_without_reload_replay(route, arguments, expected):
    # Given: one selected editor and a bounded operation.
    # When: the public adapter receives the operation.
    result = await manage_play_scenario(SimpleNamespace(), **arguments)
    # Then: only one command is dispatched; run returns the job without polling.
    assert result == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.args[1:] == ("Selected@owned", "manage_play_scenario", expected)
    assert route.send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "save"},
        {"action": "get"},
        {"action": "delete"},
        {"action": "run"},
        {"action": "status"},
        {"action": "cancel"},
        {"action": "list", "name": "menu-start"},
        {"action": "save", "scenario": DEFINITION, "name": "menu-start"},
        {"action": "get", "name": "menu-start", "job_id": JOB_ID},
        {"action": "status", "job_id": JOB_ID, "scenario": DEFINITION},
        {"action": "get", "name": "menu-start", "repeat_count": 1},
        {"action": "cancel", "job_id": JOB_ID, "timeout_seconds": 300},
        {"action": "run", "name": "Menu"},
        {"action": "run", "name": "menu-start", "repeat_count": True},
        {"action": "run", "name": "menu-start", "repeat_count": "2"},
        {"action": "run", "name": "menu-start", "timeout_seconds": 1.5},
        {"action": "run", "name": "menu-start", "repeat_count": 11},
        {"action": "run", "name": "menu-start", "timeout_seconds": 1801},
        {"action": "status", "job_id": JOB_ID.upper()},
        {"action": "status", "job_id": "short"},
    ],
)
async def test_invalid_request_has_no_selection_or_side_effect(route, arguments):
    # Given: missing, incompatible or coerced arguments.
    # When: the adapter parses the request.
    result = await manage_play_scenario(SimpleNamespace(), **arguments)
    # Then: neither discovery nor Unity execution occurs.
    assert result["success"] is False
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": " "},
        {"name": "bad\\name"},
        {"name": "bad\x7f"},
        {"target": None},
        {"scene": "Assets/Invalid?/Menu.unity"},
        {"scene": "Assets/Folder./Menu.unity"},
        {"scene": "Assets/Folder /Menu.unity"},
        {"timeout_seconds": True},
        {"timeout_seconds": 121},
        {"action": "click_ui", "target": 123, "scene": None},
        {"action": "click_ui", "target": "/Canvas/Start", "scene": None},
        {"action": "click_ui", "target": "Canvas/../Start", "scene": None},
        {"action": "click_ui", "target": "Canvas//Start", "scene": None},
        {"action": "click_ui", "target": "Canvas/Start/", "scene": None},
        {"action": "click_ui", "target": "/".join(["x"] * 129), "scene": None},
        {"scene": "Assets/../Menu.unity"},
        {"scene": "Packages/Menu.unity"},
        {"scene": "Assets/Resources/GameData/Menu.unity"},
        {"scene": "Assets\\Menu.unity"},
        {"target": "Player"},
        {"scene": "Assets/Menu.prefab"},
        {"unexpected": True},
    ],
)
def test_invalid_step_is_rejected(changes):
    # Given: an unsafe selector, incompatible field or invalid bound.
    step = {"name": "Menu", "action": "load_scene", "scene": "Assets/Menu.unity"}
    step.update(changes)
    # When/Then: boundary parsing rejects it rather than normalizing it.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(step)


@pytest.mark.parametrize(
    "change",
    [
        {"steps": []},
        {"steps": [DEFINITION["steps"][0]] * 33},
        {"steps": [DEFINITION["steps"][1]]},
        {"poll_interval_ms": 99},
        {"poll_interval_ms": 2001},
        {"poll_interval_ms": "250"},
        {"extra": True},
    ],
)
def test_invalid_definition_is_rejected(change):
    # Given: a malformed whole definition.
    definition = {**DEFINITION, **change}
    # When/Then: the saved scenario cannot cross the boundary.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition)


@pytest.mark.asyncio
async def test_failure_report_is_preserved(route):
    # Given: Unity returns timestamps, step details and bounded failure logs.
    report = {
        "success": False,
        "error": "scenario_failed",
        "data": {
            "job_id": JOB_ID,
            "status": "failed",
            "steps": [
                {
                    "iteration": 2,
                    "step_index": 3,
                    "status": "failed",
                    "started_at": "2026-10-10T10:00:00Z",
                    "finished_at": "2026-10-10T10:00:30Z",
                    "detail": "Player not found",
                    "poll_count": 120,
                }
            ],
            "logs": [{"message": "Missing player", "stack": "Fixture.Start"}],
        },
    }
    route.send.return_value = report
    # When: the report is requested.
    result = await manage_play_scenario(SimpleNamespace(), "status", job_id=JOB_ID)
    # Then: the facade does not strip job diagnostics or reinterpret failure.
    assert result == report


@pytest.mark.asyncio
async def test_legacy_model_failure_is_returned_as_mapping(route):
    # Given: legacy connection failure is a model.
    route.send.return_value = MCPResponse(success=False, error="Unavailable", hint="retry")
    # When: a saved definition is requested.
    result = await manage_play_scenario(SimpleNamespace(), "get", name="menu-start")
    # Then: public failure metadata is preserved.
    assert result == {"success": False, "error": "Unavailable", "hint": "retry"}


@pytest.mark.asyncio
async def test_request_cancellation_propagates_without_resubmission(route):
    # Given: the single command is cancelled at its transport boundary.
    route.send.side_effect = asyncio.CancelledError
    # When/Then: cancellation escapes and does not start another command/poller.
    with pytest.raises(asyncio.CancelledError):
        await manage_play_scenario(SimpleNamespace(), "run", name="menu-start")
    route.send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_actual_sdk_registration_and_save_contract(route, mode):
    # Given: the real production declaration registered with the installed SDK.
    server = FastMCP("play-scenario-contract")
    server.tool(manage_play_scenario)
    registered = (await server.list_tools(run_middleware=False))[0]
    assert registered.parameters["$defs"]["ScenarioStep"]["additionalProperties"] is False
    assert not registered.output_schema.get("required")
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        schema = next(tool for tool in tools if tool.name == "manage_play_scenario")
        assert schema.name == "manage_play_scenario"

        # When: a whole scenario is saved through actual SDK argument conversion.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": copy.deepcopy(DEFINITION)}
        )
        # Then: nested fields survive validation and only one Unity call is made.
        assert json.loads(result.content[0].text) == route.send.return_value
        route.send.assert_awaited_once()
        assert route.send.call_args.args[3]["scenario"]["steps"][1]["target"] == "Canvas/Start"


def test_schema_advertises_definition_and_run_limits():
    # Given: discoverable nested input schema.
    schema = TypeAdapter(PlayScenarioCommand).json_schema()
    # When/Then: hard bounds and unsupported fields are represented for callers.
    definition = schema["$defs"]["PlayScenario"]
    assert definition["properties"]["steps"]["maxItems"] == 32
    assert definition["properties"]["poll_interval_ms"]["minimum"] == 100
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_run_returns_job_after_one_dispatch(route, mode):
    # Given: Unity owns the queued job and the installed SDK exposes the tool.
    server = FastMCP("play-scenario-run-contract")
    server.tool(manage_play_scenario)
    async with Client(server, mode=mode) as client:
        # When: a saved scenario is started through the public MCP client.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "run", "name": "menu-start"}
        )
        # Then: the initial job report is returned after one dispatch, without a status loop.
        assert json.loads(result.content[0].text) == route.send.return_value
        route.select.assert_awaited_once()
        route.send.assert_awaited_once()
        assert route.send.call_args.args[3] == {
            "action": "run",
            "name": "menu-start",
            "repeat_count": 1,
            "timeout_seconds": 300,
        }
        assert route.send.call_args.kwargs == {"retry_on_reload": False}
