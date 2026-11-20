"""Align two saved observations of a pointer click and locate their first difference."""

from itertools import zip_longest
import json
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cli.utils.play_scenario_analysis_io import AnalysisError, read_json
from cli.utils.play_scenario_report_json import JsonValue

_NORMALIZED_TOLERANCE: Final = 0.0001

Label = Annotated[str, Field(max_length=128, pattern=r"^[^\x00-\x1f\x7f-\x9f]*$")]
Key = Annotated[Label, Field(min_length=1)]
Number = Annotated[float, Field(allow_inf_nan=False)]
Extent = Annotated[int, Field(gt=0, le=100_000)]
Pixel = tuple[Number, Number]
Viewport = tuple[Extent, Extent]
StateValue = bool | int | Number | Label | None
StateValues = Annotated[dict[Key, StateValue], Field(min_length=1, max_length=32)]


class _TraceModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)


class _RawEdge(_TraceModel):
    control: Key
    pressed: bool
    position: Pixel


class _Callback(_TraceModel):
    backend: Literal["ugui", "uitoolkit"]
    event: Literal["Down", "Up", "Click"]
    phase: Literal["handler", "target", "trickle_down", "bubble_up"]
    target: Label | None
    receiver: Label
    button: int

    def recipient(self) -> tuple[str, str, str, str]:
        return (self.event, self.backend, self.receiver, self.phase)


class _State(_TraceModel):
    before: StateValues
    after: StateValues

    @model_validator(mode="after")
    def same_probes(self) -> "_State":
        if self.before.keys() != self.after.keys():
            raise ValueError("Game state probes must be present in both checkpoints")
        return self


class _Trace(_TraceModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    input_action: Key
    viewport: Viewport
    screen_point: Pixel
    trace_truncated: bool
    input_action_edges: Annotated[list[_RawEdge], Field(max_length=128)]
    events: Annotated[list[_Callback], Field(max_length=128)]
    gameplay_state: _State

    @model_validator(mode="after")
    def on_screen(self) -> "_Trace":
        if not all(0 <= pixel < extent for pixel, extent in zip(self.screen_point, self.viewport)):
            raise ValueError("Input point must be within the observed viewport")
        return self

    def terminal_handler(self) -> _Callback | None:
        return next(
            (
                callback
                for callback in reversed(self.events)
                if callback.event == "Click" and callback.phase in ("target", "handler")
            ),
            None,
        )


def _load_trace(path: Path, label: str) -> _Trace:
    """Read bounded JSON and validate the evidence fields independently of fixture metadata."""
    try:
        # JSON mode accepts coordinate arrays for fixed-size tuples without coercing strings.
        return _Trace.model_validate_json(json.dumps(read_json(path)))
    except (OSError, ValueError):
        raise AnalysisError(f"Invalid {label} pointer trace") from None


def _normalized(point: Pixel, viewport: Viewport) -> Pixel:
    return (point[0] / viewport[0], point[1] / viewport[1])


def _same_point(left: Pixel, right: Pixel) -> bool:
    return all(abs(a - b) <= _NORMALIZED_TOLERANCE for a, b in zip(left, right))


def _point_evidence(trace: _Trace) -> dict[str, JsonValue]:
    return {
        "action": trace.input_action,
        "viewport": list(trace.viewport),
        "screen_point": list(trace.screen_point),
        "normalized_point": list(_normalized(trace.screen_point, trace.viewport)),
    }


def _pair(baseline: BaseModel | None, candidate: BaseModel | None) -> dict[str, JsonValue]:
    return {
        "baseline": baseline.model_dump(mode="json") if baseline is not None else None,
        "candidate": candidate.model_dump(mode="json") if candidate is not None else None,
    }


def _raw_matches(left: _RawEdge, right: _RawEdge, before: Viewport, after: Viewport) -> bool:
    return (left.control, left.pressed) == (right.control, right.pressed) and _same_point(
        _normalized(left.position, before), _normalized(right.position, after)
    )


def _raw_difference(before: _Trace, after: _Trace) -> dict[str, JsonValue] | None:
    for index, (left, right) in enumerate(
        zip_longest(before.input_action_edges, after.input_action_edges)
    ):
        if (
            left is None
            or right is None
            or not _raw_matches(left, right, before.viewport, after.viewport)
        ):
            return {"stage": "raw_event", "index": index, **_pair(left, right)}
    return None


def _click_problem(trace: _Trace) -> str | None:
    edges = trace.input_action_edges
    if len(edges) != 2:
        return "expected_press_and_release"
    press, release = edges
    if not press.pressed or release.pressed or press.control != release.control:
        return "expected_press_and_release"
    point = _normalized(trace.screen_point, trace.viewport)
    if any(not _same_point(point, _normalized(edge.position, trace.viewport)) for edge in edges):
        return "off_target"
    return None


def _downstream_difference(before: _Trace, after: _Trace) -> dict[str, JsonValue] | None:
    left = next(iter(before.events), None)
    right = next(iter(after.events), None)
    if (left.recipient() if left else None) != (right.recipient() if right else None):
        return {"stage": "first_recipient", **_pair(left, right)}

    left, right = before.terminal_handler(), after.terminal_handler()
    if left != right:
        return {"stage": "terminal_handler", **_pair(left, right)}

    for index, (left, right) in enumerate(zip_longest(before.events, after.events)):
        if left != right:
            return {"stage": "callback_route", "index": index, **_pair(left, right)}

    for checkpoint, left_state, right_state in (
        ("before", before.gameplay_state.before, after.gameplay_state.before),
        ("after", before.gameplay_state.after, after.gameplay_state.after),
    ):
        for probe in sorted(left_state.keys() | right_state.keys()):
            left_value, right_value = left_state.get(probe), right_state.get(probe)
            if (
                probe not in left_state
                or probe not in right_state
                or isinstance(left_value, bool) != isinstance(right_value, bool)
                or left_value != right_value
            ):
                return {
                    "stage": "gameplay_state",
                    "checkpoint": checkpoint,
                    "probe": probe,
                    "baseline": left_value,
                    "candidate": right_value,
                    "baseline_observed": probe in left_state,
                    "candidate_observed": probe in right_state,
                }
    return None


def compare_pointer_traces(baseline: Path, candidate: Path) -> dict[str, JsonValue]:
    """Return the earliest different stage of a common click or an incomplete outcome."""
    before, after = _load_trace(baseline, "baseline"), _load_trace(candidate, "candidate")
    evidence: dict[str, JsonValue] = {
        "schema_version": 1,
        "input_coordinate": {
            "baseline": _point_evidence(before),
            "candidate": _point_evidence(after),
        },
    }
    if before.trace_truncated or after.trace_truncated:
        return {
            **evidence,
            "status": "incomplete",
            "reason": "trace_truncated",
            "first_divergence": None,
            "retained_callbacks": {
                side: len(trace.events)
                for side, trace in (("baseline", before), ("candidate", after))
                if trace.trace_truncated
            },
        }
    if (
        before.input_action != after.input_action
        or not _same_point(
            _normalized(before.screen_point, before.viewport),
            _normalized(after.screen_point, after.viewport),
        )
        or not (before.input_action_edges or after.input_action_edges)
    ):
        return {
            **evidence,
            "status": "incomplete",
            "reason": "no_common_input",
            "first_divergence": None,
        }

    evidence["raw_edge_counts"] = {
        "baseline": len(before.input_action_edges),
        "candidate": len(after.input_action_edges),
    }
    raw_difference = _raw_difference(before, after)
    input_problems = {
        side: problem
        for side, trace in (("baseline", before), ("candidate", after))
        if (problem := _click_problem(trace)) is not None
    }
    if input_problems:
        return {
            **evidence,
            "status": "incomplete",
            "reason": "incomplete_click",
            "first_divergence": None,
            "input_problems": input_problems,
            "observed_raw_difference": raw_difference,
        }

    difference = raw_difference or _downstream_difference(before, after)
    return {
        **evidence,
        "status": "diverged" if difference else "same",
        "first_divergence": difference,
        "first_recipient": _pair(next(iter(before.events), None), next(iter(after.events), None)),
        "terminal_handler": _pair(before.terminal_handler(), after.terminal_handler()),
        "gameplay_state": _pair(before.gameplay_state, after.gameplay_state),
    }
