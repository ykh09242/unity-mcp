"""Typed datasets validate complete effective cases before any editor selection or save."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from models.play_scenarios import PlayScenario, PlayScenarioCommand, ScenarioStep
from pydantic import ValidationError
import pytest
from services.tools.manage_play_scenario import manage_play_scenario


def template():
    return {
        "name": "difficulty",
        "steps": [
            {"name": "Anchor", "action": "load_scene", "scene": "Assets/Scenes/Owned.unity"},
            {"name": "Score", "action": "wait_state", "state_id": "score", "state_equals": 0},
        ],
        "parameters": {"score": {"type": "integer", "default": 0}},
        "parameter_bindings": [{"path": "/steps/1/state_equals", "parameter": "score"}],
        "datasets": [
            {"name": name, "values": {"score": value}}
            for name, value in (("easy", 1), ("normal", 2), ("hard", 3))
        ],
    }


def test_three_effective_cases_have_distinct_scalar_values_names_and_hashes():
    saved = PlayScenario.model_validate(template())
    cases = [saved.materialize(name) for name in ("easy", "normal", "hard")]
    assert [case.steps[1].state_equals for case in cases] == [1, 2, 3]
    assert len({case.name for case in cases}) == 3
    assert (
        len(
            {
                hashlib.sha256(
                    json.dumps(
                        case.model_dump(mode="json", exclude_none=True), sort_keys=True
                    ).encode()
                ).hexdigest()
                for case in cases
            }
        )
        == 3
    )
    assert saved.steps[1].state_equals == 0
    assert saved.materialize().name == "difficulty"
    assert all(
        not {"parameters", "parameter_bindings", "datasets"}
        & case.model_dump(exclude_none=True).keys()
        for case in cases
    )


def test_legacy_serialization_omits_new_schema_and_materialization_is_identical():
    old = template()
    for key in ("parameters", "parameter_bindings", "datasets"):
        old.pop(key)
    parsed = PlayScenario.model_validate(old)
    assert not {"parameters", "parameter_bindings", "datasets"} & parsed.model_dump().keys()
    assert parsed.model_dump() == parsed.materialize().model_dump()
    # Frozen from the actual pre-PLAY13 PRIMARY model, preserving concrete wire defaults.
    assert (
        hashlib.sha256(
            json.dumps(
                parsed.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        == "6da45e9b178641018556ee38c8e4175f12fbc0ee6b8665a856b1bbf03ce38869"
    )
    assert PlayScenarioCommand(action="run", name="difficulty").wire_parameters() == {
        "action": "run",
        "name": "difficulty",
        "repeat_count": 1,
        "timeout_seconds": 300,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/steps/0/scene",
        "/steps/1/state_id",
        "/name",
        "/steps/1/action",
        "/steps/1/target",
        "/steps/1/property/path",
        "/steps/1/screenshot/name",
        "/../scene",
        "/steps/01/state_equals",
        "/steps/999/state_equals",
    ],
)
def test_selector_path_or_noncanonical_binding_rejected(path):
    value = template()
    value["parameter_bindings"][0]["path"] = path
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.parametrize("value", [True, 1.5, "2", None, {}, [], 9223372036854775808])
def test_wrong_dataset_scalar_type_is_rejected(value):
    definition = template()
    definition["datasets"][0]["values"]["score"] = value
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(definition)


@pytest.mark.parametrize(
    "mutation",
    [
        "overflow",
        "unknown",
        "duplicate",
        "bindings",
        "null",
        "string",
        "size",
        "identity",
        "case_limit",
    ],
)
def test_all_effective_cases_and_bounds_are_admitted_before_save(mutation):
    value = template()
    if mutation == "overflow":
        value["parameter_bindings"][0]["path"] = "/steps/1/timeout_seconds"
        value["datasets"][0]["values"]["score"] = 3601
    elif mutation == "unknown":
        value["datasets"][0]["values"]["undeclared"] = 1
    elif mutation == "duplicate":
        value["datasets"][1]["name"] = "easy"
    elif mutation == "bindings":
        value["parameter_bindings"] *= 2
    elif mutation == "null":
        value["datasets"] = None
    elif mutation == "string":
        value["parameters"]["score"] = {"type": "string", "default": "😀" * 513}
    elif mutation == "size":
        value["steps"] *= 33
    elif mutation == "identity":
        value["name"] = "a" * 64
    elif mutation == "case_limit":
        value["datasets"] = [{"name": f"case-{i}", "values": {}} for i in range(17)]
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


def test_defaults_and_partial_dataset_values_are_applied_without_text_interpolation():
    value = template()
    value["parameters"]["score"]["default"] = 7
    value["datasets"][0]["values"] = {}
    saved = PlayScenario.model_validate(value)
    assert saved.materialize().steps[1].state_equals == 7
    assert saved.materialize("easy").steps[1].state_equals == 7
    with pytest.raises(ValueError, match="Unknown dataset"):
        saved.materialize("absent")


@pytest.mark.parametrize("action", ["save", "get", "suite_run", "status"])
def test_dataset_selector_is_run_only(action):
    with pytest.raises(ValidationError):
        PlayScenarioCommand(action=action, name="difficulty", dataset="easy")


@pytest.mark.asyncio
async def test_real_sdk_schema_and_three_selected_case_dispatches(monkeypatch):
    select = AsyncMock(return_value="owned")
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        "services.tools.manage_play_scenario.get_unity_instance_from_context", select
    )
    monkeypatch.setattr("services.tools.manage_play_scenario.send_with_unity_instance", send)
    server = FastMCP("parameter-cases")
    server.tool(manage_play_scenario)
    async with Client(server) as client:
        tools = await client.list_tools()
        schema = tools[0].input_schema
        assert "dataset" in schema["properties"]
        assert "parameters" in schema["properties"]["scenario"]["anyOf"][0]["properties"]
        await client.call_tool("manage_play_scenario", {"action": "save", "scenario": template()})
        for case in ("easy", "normal", "hard"):
            await client.call_tool(
                "manage_play_scenario", {"action": "run", "name": "difficulty", "dataset": case}
            )
    assert select.await_count == 4
    assert [call.args[3].get("dataset") for call in send.call_args_list] == [
        None,
        "easy",
        "normal",
        "hard",
    ]
    assert all(call.kwargs == {"retry_on_reload": False} for call in send.call_args_list)


@pytest.mark.asyncio
async def test_invalid_dataset_never_reaches_selection_or_save(monkeypatch):
    select = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(
        "services.tools.manage_play_scenario.get_unity_instance_from_context", select
    )
    monkeypatch.setattr("services.tools.manage_play_scenario.send_with_unity_instance", send)
    definition = template()
    definition["datasets"][2]["values"]["score"] = True
    result = await manage_play_scenario(SimpleNamespace(), action="save", scenario=definition)
    assert result["success"] is False
    select.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    "selector",
    [
        {"element_name": "Start"},
        {"element_path": ""},
        {"element_path": "/0/12", "element_name": "Start"},
    ],
)
def test_uitoolkit_schema_accepts_typed_stable_selectors(selector):
    step = ScenarioStep.model_validate(
        {
            "name": "Click",
            "action": "click_ui",
            "target_id": "doc",
            "ui_backend": "uitoolkit",
            **selector,
        }
    )
    assert step.ui_backend == "uitoolkit"


@pytest.mark.parametrize(
    "changes",
    [
        {"element_path": "/01"},
        {"element_path": "/0/../1"},
        {"element_path": "/2147483648"},
        {"element_name": "x", "click_mode": "raycast"},
        {"element_id": "volatile"},
        {},
        {"element_name": None},
    ],
)
def test_uitoolkit_schema_rejects_ephemeral_and_invalid_selectors(changes):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {
                "name": "Click",
                "action": "click_ui",
                "target_id": "doc",
                "ui_backend": "uitoolkit",
                **changes,
            }
        )


def test_combined_dataset_metadata_byte_limit_is_checked_before_save():
    value = template()
    value["parameters"] = {f"p{i}": {"type": "string", "default": "x" * 1024} for i in range(4)}
    value["steps"] = value["steps"][:1] + [
        {"name": f"Probe{i}", "action": "wait_state", "state_id": f"p{i}", "state_equals": ""}
        for i in range(4)
    ]
    value["parameter_bindings"] = [
        {"path": f"/steps/{i + 1}/state_equals", "parameter": f"p{i}"} for i in range(4)
    ]
    value["datasets"] = [
        {"name": f"case-{i}", "values": {f"p{j}": "x" * 1024 for j in range(4)}} for i in range(16)
    ]
    with pytest.raises(ValidationError, match="64 KiB"):
        PlayScenario.model_validate(value)
