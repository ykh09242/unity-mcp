"""Persisted device commands retain strict native-compatible bounded payloads."""

import pytest


def test_long_explicit_input_preserves_frame_counts():
    from models.play_scenarios import ScenarioStep

    result = ScenarioStep.model_validate(
        {
            "name": "hold",
            "action": "input",
            "input": {"action": "key", "key": "Space", "frames": 36000},
            "wait_frames": 36000,
            "timeout_seconds": 3600,
        }
    )
    assert result.input.frames == result.wait_frames == 36000


from pydantic import ValidationError

from models.play_scenarios import (
    PlayScenario,
    ScenarioInput,
    ScenarioStep,
    normalize_native_definition,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "key", "key": "Space", "frames": 4},
        {"action": "key", "key": "Space", "state": "release"},
        {"action": "mouse", "position": [50, 60], "frames": 600},
        {"action": "mouse", "state": "move", "position": [70, 80]},
        {"action": "touch", "touch_id": 2, "position": [10, 20], "frames": 12},
        {"action": "touch", "state": "release", "touch_id": 2},
        {"action": "release_all"},
    ],
)
def test_input_command_round_trip_preserves_only_authored_device_fields(payload):
    step = ScenarioStep.model_validate({"name": "Input", "action": "input", "input": payload})
    serialized = step.model_dump(mode="json", exclude_none=True)
    assert serialized["input"] == payload
    assert ScenarioStep.model_validate(serialized) == step


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "key", "key": "Space", "frames": 36001},
        {"action": "key", "key": "Space", "frames": True},
        {"action": "key", "key": "Space", "state": "move"},
        {"action": "key", "key": "Space", "position": [1, 2]},
        {"action": "mouse", "state": "move"},
        {"action": "touch", "state": "press"},
        {"action": "touch", "touch_id": 11, "position": [1, 2]},
        {"action": "mouse", "position": [float("inf"), 2]},
        {"action": "mouse", "position": [1, 2, 3]},
        {"action": "mouse", "state": "release", "frames": 4},
        {"action": "mouse", "position": None},
        {"action": "release_all", "state": "release"},
        {"action": "ui_click", "target": "Canvas/Button"},
    ],
)
def test_invalid_device_commands_cannot_be_saved(payload):
    with pytest.raises(ValidationError):
        ScenarioInput.model_validate(payload)


@pytest.mark.parametrize(
    "fields",
    [
        {"input": {"action": "release_all"}, "wait_frames": 1},
        {"input": {"action": "key", "key": "Space"}, "wait_frames": 0},
        {"input": {"action": "key", "key": "Space"}, "target_id": "button"},
        {"input": {"action": "key", "key": "Space"}, "wait_frames": None},
        {"input": None},
    ],
)
def test_input_steps_reject_unrelated_or_unbounded_waits(fields):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "Input", "action": "input", **fields})


def test_draft_flag_is_optional_strict_and_round_trips():
    definition = {
        "name": "recorded",
        "steps": [{"name": "Load", "action": "load_scene", "scene": "Assets/Test.unity"}],
    }
    old = PlayScenario.model_validate(definition)
    assert "draft" not in old.model_dump(exclude_none=True)
    draft = PlayScenario.model_validate({**definition, "draft": True})
    assert draft.model_dump(exclude_none=True)["draft"] is True
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**definition, "draft": None})


def test_native_null_normalization_preserves_old_reports_without_weakening_authored_input():
    definition = PlayScenario.model_validate(
        {
            "name": "old",
            "steps": [{"name": "Load", "action": "load_scene", "scene": "Assets/Test.unity"}],
        }
    )
    native = definition.model_dump(mode="json")
    assert native["draft"] is None
    assert native["steps"][0]["input"] is None
    assert PlayScenario.model_validate(normalize_native_definition(native)) == definition
    native["steps"].append({"name": "Missing input", "action": "input", "input": None})
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(normalize_native_definition(native))


def test_native_normalization_retains_nonnull_unrelated_and_unknown_fields():
    native = {
        "name": "old",
        "steps": [
            {
                "name": "Load",
                "action": "load_scene",
                "scene": "Assets/Test.unity",
                "input": {"action": "release_all"},
            }
        ],
    }
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(normalize_native_definition(native))
    native["steps"][0].pop("input")
    native["steps"][0]["unknown_input_field"] = None
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(normalize_native_definition(native))
