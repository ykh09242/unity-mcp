"""Saved pointer trace comparison through the public scenario CLI."""

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from cli.main import cli
from cli.utils.play_scenario_report_json import JsonValue


def _trace(scale: float = 1) -> dict[str, JsonValue]:
    point = [100 * scale, 200 * scale]
    callback = {
        "backend": "uitoolkit",
        "receiver": "Overlay",
        "target": "Overlay",
        "phase": "target",
        "button": 0,
    }
    return {
        "schema_version": 1,
        "input_action": "mouse_left_click",
        "viewport": [int(400 * scale), int(300 * scale)],
        "screen_point": point,
        "input_action_edges": [
            {
                "control": "leftButton",
                "pressed": pressed,
                "position": point,
                "device_id": int(scale * 10),
            }
            for pressed in (True, False)
        ],
        "events": [{**callback, "event": event} for event in ("Down", "Up", "Click")],
        "trace_truncated": False,
        "gameplay_state": {"before": {"opens": 0}, "after": {"opens": 0}},
    }


def _compare(
    root: Path, baseline: dict[str, JsonValue], candidate: dict[str, JsonValue]
) -> tuple[int, dict[str, JsonValue]]:
    paths = [root / name for name in ("baseline.json", "candidate.json", "comparison.json")]
    paths[0].write_text(json.dumps(baseline), encoding="utf-8")
    paths[1].write_text(json.dumps(candidate), encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "play-scenario",
            "compare-pointer-traces",
            str(paths[0]),
            str(paths[1]),
            "--output",
            str(paths[2]),
        ],
    )
    payload = json.loads(result.output)
    assert json.loads(paths[2].read_text(encoding="utf-8")) == payload
    return result.exit_code, payload


def test_same_click_aligns_using_each_observed_viewport(tmp_path: Path) -> None:
    # Given / When
    code, report = _compare(tmp_path, _trace(), _trace(1.5))
    # Then
    assert (code, report["status"], report["first_divergence"]) == (0, "same", None)
    assert report["input_coordinate"]["baseline"]["screen_point"] == [100, 200]
    assert report["input_coordinate"]["candidate"]["screen_point"] == [150, 300]


@pytest.mark.parametrize(
    "raw,callback_index,stage",
    [
        (True, 0, "raw_event"),
        (False, 0, "first_recipient"),
        (False, 2, "terminal_handler"),
        (False, 1, "callback_route"),
        (False, None, "gameplay_state"),
    ],
)
def test_reports_earliest_different_observation(
    tmp_path: Path, raw: bool, callback_index: int | None, stage: str
) -> None:
    # Given
    baseline, candidate = _trace(), _trace(1.5)
    candidate["gameplay_state"]["after"]["opens"] = 1
    if raw:
        for edge in candidate["input_action_edges"]:
            edge["control"] = "rightButton"
    if callback_index is not None:
        candidate["events"][callback_index]["receiver"] = "NewReceiver"
    # When
    code, report = _compare(tmp_path, baseline, candidate)
    # Then
    assert (code, report["status"], report["first_divergence"]["stage"]) == (1, "diverged", stage)
    assert report["gameplay_state"]["baseline"]["after"]["opens"] == 0
    assert report["gameplay_state"]["candidate"]["after"]["opens"] == 1


def test_preserves_observed_raw_difference_when_one_click_is_off_target(tmp_path: Path) -> None:
    # Given
    baseline, candidate = _trace(), _trace(1.5)
    candidate["input_action_edges"][1]["position"] = [155, 305]
    # When
    code, report = _compare(tmp_path, baseline, candidate)
    # Then
    assert (code, report["status"], report["first_divergence"]) == (2, "incomplete", None)
    assert report["input_problems"] == {"candidate": "off_target"}
    difference = report["observed_raw_difference"]
    assert difference["stage"] == "raw_event"
    assert difference["index"] == 1
    assert difference["candidate"]["position"] == [155, 305]


@pytest.mark.parametrize(
    "press_only,problem",
    [
        (True, "expected_press_and_release"),
        (False, "off_target"),
    ],
)
def test_matching_invalid_clicks_do_not_pass(
    tmp_path: Path, press_only: bool, problem: str
) -> None:
    # Given
    baseline, candidate = _trace(), _trace(1.5)
    for trace in (baseline, candidate):
        trace["events"] = []
        if press_only:
            trace["input_action_edges"] = trace["input_action_edges"][:1]
        else:
            for edge in trace["input_action_edges"]:
                edge["position"] = [0, 0]
    # When
    code, report = _compare(tmp_path, baseline, candidate)
    # Then
    assert (code, report["status"], report["reason"]) == (2, "incomplete", "incomplete_click")
    assert report["first_divergence"] is None
    assert report["observed_raw_difference"] is None
    assert report["input_problems"] == {"baseline": problem, "candidate": problem}


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"trace_truncated": True}, "trace_truncated"),
        ({"screen_point": [160, 300]}, "no_common_input"),
        ({"input_action": "different_action"}, "no_common_input"),
    ],
)
def test_incomplete_evidence_has_no_divergence_verdict(
    tmp_path: Path, overrides: dict[str, JsonValue], reason: str
) -> None:
    # Given
    baseline, candidate = _trace(), _trace(1.5)
    candidate["events"][0]["receiver"] = "NewReceiver"
    candidate.update(overrides)
    if reason == "trace_truncated":
        candidate["events"] = (candidate["events"] * 43)[:128]
    # When
    code, report = _compare(tmp_path, baseline, candidate)
    # Then
    assert (code, report["status"], report["reason"], report["first_divergence"]) == (
        2,
        "incomplete",
        reason,
        None,
    )
    if reason == "trace_truncated":
        assert report["retained_callbacks"] == {"candidate": 128}


def test_missing_raw_input_on_both_sides_is_incomplete(tmp_path: Path) -> None:
    # Given
    baseline, candidate = _trace(), _trace()
    baseline["input_action_edges"] = candidate["input_action_edges"] = []
    # When
    code, report = _compare(tmp_path, baseline, candidate)
    # Then
    assert (code, report["status"], report["reason"], report["first_divergence"]) == (
        2,
        "incomplete",
        "no_common_input",
        None,
    )
