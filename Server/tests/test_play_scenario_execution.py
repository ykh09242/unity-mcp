"""Reset, query-budget and bounded execution-diagnostic boundary regressions."""

import copy
import json
from xml.etree import ElementTree

from cli.main import cli
from cli.utils.play_scenario_reports import junit_xml, write_suite_artifacts
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from services.tools.manage_play_scenario import manage_play_scenario
from models.play_scenarios import PlayScenario, PlayScenarioCommand, ScenarioStep
from pydantic import ValidationError
import pytest

from .test_cli_play_scenario import wire
from .test_manage_play_scenario import DEFINITION, route


def test_old_definition_retains_disabled_execution_diagnostics():
    scenario = PlayScenario.model_validate(DEFINITION)
    assert scenario.query_budget.model_dump() == {
        "enabled": False,
        "max_target_searches": 4096,
        "max_hierarchy_visits": 1000000,
    }
    assert scenario.diagnostics.record_timeline is False
    assert scenario.steps[0].action == "load_scene"


def test_reset_state_and_zero_query_budgets_survive_cli_and_tool_wire(wire, tmp_path):
    definition = copy.deepcopy(DEFINITION)
    definition["setup_steps"] = [
        definition["steps"].pop(0),
        {
            "name": "Reset registered state",
            "action": "reset_state",
            "reset_ids": ["State.One", "state:two"],
        },
    ]
    definition["query_budget"] = {
        "enabled": True,
        "max_target_searches": 0,
        "max_hierarchy_visits": 0,
    }
    definition["diagnostics"] = {"record_timeline": True}
    expected = PlayScenarioCommand(
        action="save", scenario=PlayScenario.model_validate(definition)
    ).wire_parameters()
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    result = CliRunner().invoke(cli, ["play-scenario", "save", str(path)])
    assert result.exit_code == 0, result.output
    calls, _reply = wire
    assert calls[0]["params"] == expected
    assert expected["scenario"]["setup_steps"][1]["reset_ids"] == ["State.One", "state:two"]


@pytest.mark.parametrize(
    "ids",
    [None, [], ["same", "same"], ["-bad"], ["contains space"], ["a" * 129], ["x"] * 17, [1], "one"],
)
def test_reset_state_rejects_missing_duplicate_or_malformed_ids(ids):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "Reset", "action": "reset_state", "reset_ids": ids})


@pytest.mark.parametrize(
    "field,value",
    [
        ("scene", None),
        ("target", "Player"),
        ("target_id", "Player"),
        ("count", 1),
        ("active", False),
        ("component", "State"),
        ("property", {"path": "x", "equals": 1}),
        ("stable_for_ms", 0),
        ("click_mode", "direct"),
        ("unknown", 1),
    ],
)
def test_reset_state_rejects_every_unused_field_even_null(field, value):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"name": "Reset", "action": "reset_state", "reset_ids": ["State"], field: value}
        )


@pytest.mark.parametrize(
    "action,fields",
    [
        ("load_scene", {"scene": "Assets/Menu.unity"}),
        ("wait_scene", {"scene": "Assets/Menu.unity"}),
        ("click_ui", {"target": "Canvas/Start"}),
        ("wait_object", {"target_id": "Player"}),
    ],
)
def test_other_actions_reject_reset_ids(action, fields):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"name": "Step", "action": action, "reset_ids": ["State"], **fields}
        )


@pytest.mark.parametrize(
    "budget",
    [
        {"enabled": 1},
        {"max_target_searches": True},
        {"max_hierarchy_visits": False},
        {"max_target_searches": "1"},
        {"max_hierarchy_visits": 1.0},
        {"max_target_searches": -1},
        {"max_target_searches": 1000001},
        {"max_hierarchy_visits": 10000001},
        {"unknown": 1},
    ],
)
def test_query_budget_rejects_coercions_unknown_fields_and_out_of_bounds(budget):
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, "query_budget": budget})


@pytest.mark.parametrize("value", [0, "true", None])
def test_timeline_rejects_non_boolean_values(value):
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, "diagnostics": {"record_timeline": value}})


def test_reset_cannot_replace_mandatory_first_scene_load():
    with pytest.raises(ValidationError, match="first executed step"):
        PlayScenario.model_validate(
            {
                **DEFINITION,
                "setup_steps": [{"name": "Reset", "action": "reset_state", "reset_ids": ["State"]}],
            }
        )


def test_junit_retains_bounded_safe_diagnostics_and_original_json(tmp_path):
    native = {
        "error": "query budget exceeded",
        "query_counts": {"target_searches": 2, "hierarchy_visits": 9},
        "timeline": [
            {
                "sequence": 1,
                "event": "budget",
                "detail": "password=fixture C:/private/secret.cs " + "x" * 900,
            }
        ],
        "dropped_timeline_count": 3,
        "resource_checks": [
            {
                "iteration": 1,
                "omitted_resource_count": 4,
                "retained_resources": [
                    {
                        "kind": "subscription",
                        "owner": "Inventory",
                        "source_file": "C:/private/Owner.cs",
                        "source_member": "Enable",
                    }
                ],
            }
        ],
        "steps": [{"name": "Find", "query_counts": {"target_searches": 2, "hierarchy_visits": 9}}],
    }
    report = {
        "suite": {"name": "diagnostics"},
        "scenarios": [{"name": "bounded", "status": "failed", "report": native}],
    }
    xml = ElementTree.fromstring(junit_xml(report))
    detail = xml.find("testcase/system-out").text
    assert len(detail) <= 16384
    assert "private" not in detail and "fixture" not in detail
    assert "Inventory" in detail and '"omitted_resource_count":4' in detail
    assert '"target_searches":2' in detail and '"dropped_timeline_count":3' in detail
    path, _xml_path = write_suite_artifacts(report, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_query_budget_upper_bounds_and_case_sensitive_reset_ids_are_supported():
    scenario = PlayScenario.model_validate(
        {
            **DEFINITION,
            "query_budget": {
                "enabled": True,
                "max_target_searches": 1000000,
                "max_hierarchy_visits": 10000000,
            },
        }
    )
    assert scenario.query_budget.max_hierarchy_visits == 10000000
    step = ScenarioStep(name="Reset", action="reset_state", reset_ids=["State", "state"])
    assert step.reset_ids == ["State", "state"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_execution_options_preserve_strict_wire_and_single_dispatch(route, mode):
    definition = copy.deepcopy(DEFINITION)
    definition["setup_steps"] = [
        definition["steps"].pop(0),
        {
            "name": "Reset",
            "action": "reset_state",
            "reset_ids": ["State", "state"],
        },
    ]
    definition["query_budget"] = {
        "enabled": True,
        "max_target_searches": 0,
        "max_hierarchy_visits": 0,
    }
    definition["diagnostics"] = {"record_timeline": True}
    server = FastMCP("execution-wire-parity")
    server.tool(manage_play_scenario)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": definition}
        )
    assert json.loads(result.content[0].text) == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert (
        route.send.call_args.args[3]
        == PlayScenarioCommand(action="save", scenario=definition).wire_parameters()
    )
    assert route.send.call_args.kwargs == {"retry_on_reload": False}
