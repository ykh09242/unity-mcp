"""Optional native per-step observations preserve existing authored contracts."""

import copy

import pytest
from models.play_scenarios import PlayScenario, ScenarioStep, normalize_native_definition
from pydantic import ValidationError

DEFINITION = {
    "name": "observe",
    "steps": [{"name": "Menu", "action": "load_scene", "scene": "Assets/Menu.unity"}],
}


def test_old_steps_do_not_emit_observation_options():
    definition = PlayScenario.model_validate(DEFINITION).model_dump(exclude_none=True)
    assert "performance" not in definition["steps"][0]
    assert "screenshot" not in definition["steps"][0]


@pytest.mark.parametrize(
    "action,selectors",
    [
        ("load_scene", {"scene": "Assets/Menu.unity"}),
        ("wait_scene", {"scene": "Assets/Menu.unity"}),
        ("wait_object", {"target": "Canvas"}),
        ("click_ui", {"target": "Canvas/Start"}),
        ("reset_state", {"reset_ids": ["game"]}),
        ("wait_state", {"state_id": "game", "state_equals": True}),
    ],
)
def test_observations_are_orthogonal_to_all_actions(action, selectors):
    step = ScenarioStep.model_validate(
        {
            "name": "Observe",
            "action": action,
            **selectors,
            "performance": {
                "max_duration_ms": 0,
                "max_frame_time_p95_ms": 16.67,
                "max_gc_allocated_bytes_per_frame": 0,
                "sample_limit": 1,
            },
            "screenshot": {"name": "menu", "width": 1280, "height": 720},
        }
    )
    assert step.performance.max_duration_ms == 0
    assert step.screenshot.width == 1280


@pytest.mark.parametrize(
    "options",
    [
        {"performance": None},
        {"performance": {"max_duration_ms": None}},
        {"performance": {"max_duration_ms": -1}},
        {"performance": {"max_duration_ms": True}},
        {"performance": {"max_gc_allocated_bytes_per_frame": 2147483648}},
        {"performance": {"max_frame_time_p95_ms": float("inf")}},
        {"performance": {"max_frame_time_p95_ms": float("nan")}},
        {"performance": {"sample_limit": 0}},
        {"performance": {"sample_limit": 4097}},
        {"performance": {"unknown": 1}},
        {"screenshot": None},
        {"screenshot": {"name": "../menu", "width": 1, "height": 1}},
        {"screenshot": {"name": "menu", "width": True, "height": 1}},
        {"screenshot": {"name": "menu", "width": 4096, "height": 4096}},
        {"screenshot": {"name": "menu", "width": 1}},
    ],
)
def test_invalid_observation_configuration_is_rejected(options):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({**DEFINITION["steps"][0], **options})


def test_native_null_options_are_normalized_without_mutating_input():
    native = copy.deepcopy(DEFINITION)
    native["steps"][0].update(performance=None, screenshot=None)
    normalized = normalize_native_definition(native)
    assert normalized == DEFINITION
    assert native["steps"][0]["performance"] is None


def test_upper_bounds_are_accepted():
    step = ScenarioStep.model_validate(
        {
            **DEFINITION["steps"][0],
            "performance": {
                "max_duration_ms": 120000,
                "max_frame_time_p95_ms": 120000,
                "max_gc_allocated_bytes_per_frame": 2147483647,
                "sample_limit": 4096,
            },
            "screenshot": {"name": "menu", "width": 4096, "height": 2048},
        }
    )
    assert step.screenshot.width * step.screenshot.height == 8388608
