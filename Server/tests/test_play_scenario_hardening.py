"""Strict expanded scenario contracts across model, SDK and CLI boundaries."""

import copy
import json
from types import SimpleNamespace
from typing import Literal, assert_never

import pytest
from fastmcp import Client, FastMCP
from models.play_scenarios import PlayScenario, PlayScenarioCommand, ScenarioStep
from pydantic import ValidationError
from services.tools.manage_play_scenario import manage_play_scenario

from .test_manage_play_scenario import DEFINITION, JOB_ID, route

HARDENED = {
    "name": "repeat-probe",
    "setup_steps": [{"name": "Reset", "action": "load_scene", "scene": "Assets/Scenes/Menu.unity"}],
    "steps": [
        {
            "name": "Inactive player",
            "action": "wait_object",
            "target": "Player",
            "count": 1,
            "active": False,
            "component": "Fixture.PlayerState",
            "property": {"path": "health", "equals": 0},
            "stable_for_ms": 500,
        }
    ],
    "cleanup_steps": [{"name": "Return", "action": "click_ui", "target": "Canvas/Return"}],
    "cleanup_timeout_seconds": 60,
    "completion_stable_ms": 750,
    "log_policy": {"mode": "strict", "allowed_messages": ["Expected fixture error"]},
    "metrics": {
        "enabled": True,
        "warmup_iterations": 0,
        "consecutive_increases": 3,
        "managed_growth_bytes": 100,
        "allocated_growth_bytes": 200,
        "object_growth_count": 1,
    },
    "diagnostics": {"screenshot_on_failure": True, "record_timeline": False},
}


def test_legacy_definition_gains_safe_hardening_defaults():
    # Given: an existing saved definition with none of the new options.
    # When: the same boundary parses it.
    scenario = PlayScenario.model_validate(copy.deepcopy(DEFINITION))
    # Then: old steps remain usable with strict logs and bounded observation/cleanup.
    assert scenario.setup_steps == []
    assert scenario.cleanup_steps == []
    assert scenario.cleanup_timeout_seconds == 30
    assert scenario.completion_stable_ms == 250
    assert scenario.log_policy.model_dump() == {"mode": "strict", "allowed_messages": []}
    assert scenario.metrics.model_dump() == {
        "enabled": False,
        "warmup_iterations": 1,
        "consecutive_increases": 2,
        "managed_growth_bytes": 1048576,
        "allocated_growth_bytes": 1048576,
        "object_growth_count": 0,
    }
    assert scenario.diagnostics.model_dump() == {
        "screenshot_on_failure": False,
        "record_timeline": False,
    }
    assert "stable_for_ms" not in scenario.steps[0].model_dump(exclude_none=True)


def test_setup_reset_and_all_expanded_options_survive_wire_serialization():
    # Given: setup provides the first scene load and the main step has false/zero values.
    # When: save builds the exact command sent to Unity.
    wire = PlayScenarioCommand.model_validate(
        {"action": "save", "scenario": HARDENED}
    ).wire_parameters()
    # Then: every explicitly authored field survives, including nested false and integer zero.
    scenario = wire["scenario"]
    for field, value in HARDENED.items():
        if field.endswith("steps"):
            for expected, actual in zip(value, scenario[field], strict=True):
                assert actual == {"timeout_seconds": 30, **expected}
        else:
            assert scenario[field] == value


@pytest.mark.parametrize("equals", [False, True, 0, -(2**63), 2**63 - 1, 1.25, "", "ready"])
def test_property_scalar_preserves_its_json_type(equals):
    # Given: a supported scalar comparison against one component.
    definition = copy.deepcopy(HARDENED)
    definition["steps"][0]["property"]["equals"] = equals
    # When: the validated definition is serialized for native comparison.
    result = PlayScenario.model_validate(definition).model_dump(mode="json", exclude_none=True)
    # Then: booleans and signed integers are not coerced into another JSON scalar type.
    actual = result["steps"][0]["property"]["equals"]
    assert actual == equals
    assert type(actual) is type(equals)


@pytest.mark.parametrize(
    "fields",
    [
        {"count": 0},
        {"count": 10000, "active": True},
        {"active": False},
        {"component": "UnityEngine.Transform"},
        {"stable_for_ms": 0},
        {"stable_for_ms": 29999},
    ],
)
def test_valid_wait_object_conditions(fields):
    # Given: bounded count, active, component or stability conditions.
    # When: the public step boundary parses them.
    step = ScenarioStep.model_validate(
        {"name": "Player", "action": "wait_object", "target": "Player", **fields}
    )
    # Then: the authored condition is retained without introducing other wait defaults.
    for field, value in fields.items():
        assert step.model_dump(exclude_none=True)[field] == value


@pytest.mark.parametrize(
    "fields",
    [
        {"count": True},
        {"count": "1"},
        {"count": -1},
        {"count": 10001},
        {"active": 0},
        {"active": "false"},
        {"count": 0, "active": False},
        {"count": 0, "component": "UnityEngine.Transform"},
        {"count": 2, "component": "UnityEngine.Transform"},
        {"component": ""},
        {"component": "x" * 257},
        {"property": {"path": "health", "equals": 0}},
        {"component": "Fixture.State", "property": {"path": "", "equals": 0}},
        {"component": "Fixture.State", "property": {"path": "x" * 257, "equals": 0}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": None}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": []}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": {}}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": 2**63}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": -(2**63) - 1}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": float("inf")}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": float("nan")}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": "x" * 1025}},
        {"component": "Fixture.State", "property": {"path": "health", "equals": 0, "extra": 1}},
        {"stable_for_ms": True},
        {"stable_for_ms": "1"},
        {"stable_for_ms": -1},
        {"stable_for_ms": 30000},
        {"stable_for_ms": 60001, "timeout_seconds": 120},
    ],
)
def test_invalid_wait_object_conditions_are_rejected(fields):
    # Given: an unsafe/coerced/out-of-bounds or incompatible condition.
    # When/Then: validation rejects it before native object inspection.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"name": "Player", "action": "wait_object", "target": "Player", **fields}
        )


@pytest.mark.parametrize("field", ["count", "active", "component", "property", "stable_for_ms"])
def test_new_optional_step_fields_reject_explicit_null(field):
    # Given: omission is meaningful but an explicit null is not an authored condition.
    # When/Then: null does not silently become a default.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"name": "Player", "action": "wait_object", "target": "Player", field: None}
        )


@pytest.mark.parametrize("action", ["load_scene", "wait_scene", "click_ui"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("count", 1),
        ("active", False),
        ("component", "Fixture.State"),
        ("property", {"path": "health", "equals": 0}),
    ],
)
def test_object_conditions_are_rejected_on_other_actions(action, field, value):
    # Given: an object condition attached to a different action.
    selector = (
        {"target": "Canvas/Start"} if action == "click_ui" else {"scene": "Assets/Menu.unity"}
    )
    # When/Then: unused fields cannot masquerade as enforced conditions.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "Step", "action": action, **selector, field: value})


@pytest.mark.parametrize("action", ["load_scene", "click_ui"])
def test_stability_is_rejected_on_effect_actions(action):
    # Given: effects must never be replayed to establish stability.
    selector = (
        {"target": "Canvas/Start"} if action == "click_ui" else {"scene": "Assets/Menu.unity"}
    )
    # When/Then: only conditions may request stability.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"name": "Step", "action": action, **selector, "stable_for_ms": 0}
        )


def test_wait_scene_accepts_stability_without_object_fields():
    # Given: a scene condition with a bounded quiet duration.
    # When: it is parsed separately from object conditions.
    step = ScenarioStep.model_validate(
        {
            "name": "Menu",
            "action": "wait_scene",
            "scene": "Assets/Menu.unity",
            "stable_for_ms": 1000,
        }
    )
    # Then: the duration survives for native uninterrupted readiness checks.
    assert step.stable_for_ms == 1000


@pytest.mark.parametrize(
    "change",
    [
        {"setup_steps": None},
        {"cleanup_steps": None},
        {"setup_steps": [DEFINITION["steps"][0]] * 17},
        {"cleanup_steps": [DEFINITION["steps"][0]] * 17},
        {"setup_steps": [DEFINITION["steps"][1]]},
        {"cleanup_timeout_seconds": 0},
        {"cleanup_timeout_seconds": 301},
        {"cleanup_timeout_seconds": True},
        {"cleanup_timeout_seconds": "30"},
        {"completion_stable_ms": -1},
        {"completion_stable_ms": 10001},
        {"completion_stable_ms": True},
        {"completion_stable_ms": "250"},
        {"log_policy": None},
        {"log_policy": {"mode": "ignore"}},
        {"log_policy": {"allowed_messages": [""]}},
        {"log_policy": {"allowed_messages": ["x"] * 2}},
        {"log_policy": {"allowed_messages": [str(i) for i in range(33)]}},
        {"log_policy": {"allowed_messages": ["x" * 1025]}},
        {"log_policy": {"allowed_messages": [False]}},
        {"log_policy": {"unexpected": True}},
        {"metrics": None},
        {"metrics": {"enabled": 1}},
        {"metrics": {"warmup_iterations": -1}},
        {"metrics": {"warmup_iterations": 10}},
        {"metrics": {"consecutive_increases": 1}},
        {"metrics": {"consecutive_increases": 10}},
        {"metrics": {"managed_growth_bytes": -1}},
        {"metrics": {"managed_growth_bytes": 2147483648}},
        {"metrics": {"allocated_growth_bytes": -1}},
        {"metrics": {"allocated_growth_bytes": 2147483648}},
        {"metrics": {"object_growth_count": -1}},
        {"metrics": {"object_growth_count": 10001}},
        {"metrics": {"object_growth_count": True}},
        {"metrics": {"unexpected": True}},
        {"diagnostics": None},
        {"diagnostics": {"screenshot_on_failure": "true"}},
        {"diagnostics": {"unexpected": True}},
    ],
)
def test_invalid_hardening_options_are_rejected(change):
    # Given: malformed lifecycle/policy/diagnostics/metrics configuration.
    # When/Then: existing strict definition parsing rejects the whole save.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, **change})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_expanded_save_dispatches_once_without_losing_conditions(route, mode):
    # Given: the installed SDK advertises the actual expanded declaration.
    server = FastMCP("play-scenario-expanded")
    server.tool(manage_play_scenario)
    async with Client(server, mode=mode) as client:
        # When: the public SDK saves a scenario including every new optional field.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": copy.deepcopy(HARDENED)}
        )
    # Then: SDK conversion keeps all authored fields and exactly one native dispatch.
    assert json.loads(result.content[0].text) == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.kwargs == {"retry_on_reload": False}
    assert (
        route.send.call_args.args[3]
        == PlayScenarioCommand(action="save", scenario=HARDENED).wire_parameters()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, "menu-start"])
async def test_reports_query_has_optional_name_and_one_dispatch(route, name):
    # Given: retained terminal jobs may include unsuccessful scenarios.
    route.send.return_value = {
        "success": True,
        "data": {"reports": [{"job_id": JOB_ID, "status": "failed"}]},
    }
    # When: the history query requests all scenarios or one exact saved name.
    result = await manage_play_scenario(SimpleNamespace(), "reports", name=name)
    # Then: reports are returned unchanged, with no background scan or status request.
    assert result == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.args[3] == {"action": "reports", **({"name": name} if name else {})}
    assert route.send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"job_id": JOB_ID},
        {"scenario": DEFINITION},
        {"repeat_count": 1},
        {"timeout_seconds": 30},
        {"name": "../unsafe"},
    ],
)
async def test_invalid_reports_query_never_selects_or_dispatches(route, arguments):
    # Given: reports permits only an optional canonical name.
    # When: an incompatible selector or option enters the public tool.
    result = await manage_play_scenario(SimpleNamespace(), "reports", **arguments)
    # Then: history validation fails before any Unity interaction.
    assert result["success"] is False
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.parametrize("mode", ["strict", "log_only"])
def test_literal_log_allowlist_preserves_case_and_message_whitespace(mode):
    # Given: literal case-sensitive exceptions include distinct whitespace/case variants.
    messages = ["Expected", "expected", " Expected "]
    # When: policy validation prepares the definition for native full-message matching.
    scenario = PlayScenario.model_validate(
        {**DEFINITION, "log_policy": {"mode": mode, "allowed_messages": messages}}
    )
    # Then: no regex, case folding or trimming changes the allowed messages.
    assert scenario.log_policy.mode == mode
    assert scenario.log_policy.allowed_messages == messages


def test_maximum_lifecycle_counts_and_condition_stability_are_accepted():
    # Given: each stage and wait condition reaches its documented upper bound.
    definition = copy.deepcopy(DEFINITION)
    definition["setup_steps"] = [definition["steps"][0]] * 16
    definition["steps"] = [
        {
            "name": "Player",
            "action": "wait_object",
            "target": "Player",
            "timeout_seconds": 120,
            "stable_for_ms": 60000,
        }
    ] * 32
    definition["cleanup_steps"] = [definition["steps"][0]] * 16
    definition["cleanup_timeout_seconds"] = 300
    definition["completion_stable_ms"] = 10000
    # When: the definition crosses the shared save boundary.
    scenario = PlayScenario.model_validate(definition)
    # Then: 64 total steps and the upper stability/cleanup limits stay supported.
    assert len(scenario.setup_steps) + len(scenario.steps) + len(scenario.cleanup_steps) == 64
    assert scenario.steps[0].stable_for_ms == 60000
    assert scenario.cleanup_timeout_seconds == 300
    assert scenario.completion_stable_ms == 10000


@pytest.mark.asyncio
@pytest.mark.parametrize("character", ["x", "\ud55c"])
async def test_definition_byte_limit_rejects_before_selection_or_dispatch(route, character):
    # Given: individually valid targets exceed the shared serialized UTF-8 definition budget.
    definition = copy.deepcopy(DEFINITION)
    definition["steps"] = [definition["steps"][0]] + [
        {"name": "Player", "action": "wait_object", "target": character * 4096}
    ] * 31
    # When: save parses the whole definition at the public boundary.
    result = await manage_play_scenario(SimpleNamespace(), "save", scenario=definition)
    # Then: the oversized definition never reaches Unity or instance selection.
    assert result["success"] is False
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "setup_steps",
        "cleanup_steps",
        "log_policy",
        "metrics",
        "diagnostics",
        "cleanup_timeout_seconds",
        "completion_stable_ms",
    ],
)
def test_top_optional_fields_reject_explicit_null(field):
    # Given: new definition options use absence for defaults.
    # When/Then: explicit null cannot pass as a defaulted object, list or integer.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, field: None})


@pytest.mark.parametrize(
    "field",
    [
        "warmup_iterations",
        "consecutive_increases",
        "managed_growth_bytes",
        "allocated_growth_bytes",
        "object_growth_count",
    ],
)
@pytest.mark.parametrize("value", [True, "2", 2.5])
def test_metrics_integer_fields_never_coerce(field, value):
    # Given: a metrics option uses a boolean, string or fractional value as an integer.
    # When/Then: strict validation rejects it before sampling can be configured.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, "metrics": {field: value}})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_reports_preserves_native_diagnostics_and_metric_warnings(route, mode):
    # Given: a retained failed report contains the newly bounded native diagnostics.
    report = {
        "job_id": JOB_ID,
        "status": "failed",
        "failure_diagnostics": {"target": "Player", "screenshot_error": "headless"},
        "runner_resources_released": True,
        "cleanup_error": "Return target unavailable",
        "metrics": [{"iteration": 1, "managed_bytes": 42, "error": None}],
        "metric_warnings": ["insufficient_samples"],
    }
    route.send.return_value = {"success": True, "data": {"reports": [report]}}
    server = FastMCP("play-scenario-reports")
    server.tool(manage_play_scenario)
    async with Client(server, mode=mode) as client:
        # When: the real MCP client requests retained history.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "reports", "name": "menu-start"}
        )
    # Then: the facade keeps native output intact and does not fetch each job again.
    assert json.loads(result.content[0].text) == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.args[3] == {"action": "reports", "name": "menu-start"}
    assert route.send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.parametrize("equals", [b"ready", (1, 2)])
def test_property_non_json_values_are_validation_errors(equals):
    # Given: direct Python callers cannot use non-JSON values for native comparisons.
    definition = copy.deepcopy(HARDENED)
    definition["steps"][0]["property"]["equals"] = equals
    # When/Then: malformed values produce a normal boundary validation error.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition)


@pytest.mark.parametrize("field", ["component", "property_path", "equals", "allowed_messages"])
def test_new_text_limits_reject_non_bmp_utf16_overflow(
    field: Literal["component", "property_path", "equals", "allowed_messages"],
):
    # Given: code point counts fit but native UTF-16 text units exceed the field limit.
    definition = copy.deepcopy(HARDENED)
    text = "\U0001f987" * (129 if field in ("component", "property_path") else 513)
    match field:
        case "component":
            definition["steps"][0]["component"] = text
        case "property_path":
            definition["steps"][0]["property"]["path"] = text
        case "equals":
            definition["steps"][0]["property"]["equals"] = text
        case "allowed_messages":
            definition["log_policy"]["allowed_messages"] = [text]
        case unreachable:
            assert_never(unreachable)
    # When/Then: saves rejected by the native bounds are also rejected in Python.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition)


@pytest.mark.parametrize("field", ["component", "property_path", "equals", "allowed_messages"])
def test_new_text_limits_accept_exact_non_bmp_utf16_boundary(
    field: Literal["component", "property_path", "equals", "allowed_messages"],
):
    # Given: an authored string fills the native UTF-16 limit exactly.
    definition = copy.deepcopy(HARDENED)
    text = "\U0001f987" * (128 if field in ("component", "property_path") else 512)
    match field:
        case "component":
            definition["steps"][0]["component"] = text
        case "property_path":
            definition["steps"][0]["property"]["path"] = text
        case "equals":
            definition["steps"][0]["property"]["equals"] = text
        case "allowed_messages":
            definition["log_policy"]["allowed_messages"] = [text]
        case unreachable:
            assert_never(unreachable)
    # When: the shared save boundary parses the definition.
    result = PlayScenario.model_validate(definition)
    # Then: matching native bounds do not reject valid supplementary characters.
    assert result.name == definition["name"]


@pytest.mark.parametrize("field", ["component", "property_path"])
@pytest.mark.parametrize("value", [" ", "\t", "Fixture\\State", "bad\x7f"])
def test_condition_names_reject_native_invalid_text(field, value):
    # Given: new condition selectors violate native Text validation.
    definition = copy.deepcopy(HARDENED)
    if field == "component":
        definition["steps"][0]["component"] = value
    else:
        definition["steps"][0]["property"]["path"] = value
    # When/Then: whitespace-only, controls and backslashes fail before Unity dispatch.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition)


@pytest.mark.parametrize("value", [" ", "\t", "\\"])
def test_literal_messages_and_property_values_preserve_controls_and_backslashes(value):
    # Given: literal log exceptions and scalar values may intentionally contain such text.
    definition = copy.deepcopy(HARDENED)
    definition["steps"][0]["property"]["equals"] = value
    definition["log_policy"]["allowed_messages"] = [value]
    # When: the definition is parsed without applying selector rules to literal values.
    scenario = PlayScenario.model_validate(definition)
    # Then: exact full-message/scalar comparison semantics survive unchanged.
    assert scenario.log_policy.allowed_messages == [value]
    assert scenario.steps[0].property.equals == value
