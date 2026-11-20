"""Run one-factor replay trials against verified original and reduced Player controls."""

from collections import Counter
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path, write_new
from cli.utils.play_scenario_replay import (
    _definition_key,
    _environment,
    _launch,
    _read_pair,
    _receipt,
    state_oracle,
)
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS
from models.play_scenarios import PlayScenario


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Seed(Settings):
    axis: Literal["seed"]
    value: int = Field(ge=-(2**31), lt=2**31)


class FrameDelay(Settings):
    axis: Literal["start_delay_frames"]
    value: int = Field(ge=0, le=120)


class InputChange(Settings):
    axis: Literal["input"]
    step_index: int = Field(ge=0, lt=128)
    field: Literal["key", "button", "position", "touch_id", "control", "value", "delta", "state"]
    value: JsonValue


class TrialPlan(Settings):
    checkpoint_index: int = Field(ge=0, lt=128)
    seed: int = Field(ge=-(2**31), lt=2**31)
    start_delay_frames: int = Field(default=0, ge=0, le=120)
    attempts: int = Field(ge=2, le=32)
    variants: list[Annotated[Seed | FrameDelay | InputChange, Field(discriminator="axis")]] = Field(
        max_length=15, default_factory=list
    )
    kept_indices: list[Annotated[int, Field(ge=0, lt=128)]] | None = Field(
        default=None, min_length=1, max_length=128
    )


def _subsequence(definition, indices):
    raw = definition.model_dump(mode="json", exclude_none=True)
    raw["steps"] = [raw["steps"][index] for index in indices]
    return PlayScenario.model_validate(raw)


def _vary_input(definition, index, change):
    step = definition.steps[index]
    if step.action != "input" or step.input is None:
        raise ValueError("Input variants require an original main-stage input step")
    raw = definition.model_dump(mode="json", exclude_none=True)
    raw["steps"][index]["input"][change.field] = change.value
    variant = PlayScenario.model_validate(raw)
    if getattr(variant.steps[index].input, change.field) == getattr(step.input, change.field):
        raise ValueError("An input variant must change the selected input field")
    return variant


def _passed_detail(report, index, step):
    if report["status"] != "succeeded":
        return None
    matches = [
        row.get("detail")
        for row in report.get("steps", [])
        if (
            row.get("stage"),
            row.get("iteration"),
            row.get("step_index"),
            row.get("name"),
            row.get("action"),
            row.get("status"),
        )
        == ("main", 1, index, step.name, step.action, "passed")
    ]
    return matches[0] if len(matches) == 1 else None


def _trial_phases(plan, full, reduced_pair):
    original = full[0].manifest.definition
    indices = list(range(len(original.steps)))
    checkpoint = plan.checkpoint_index
    if (
        checkpoint not in indices
        or original.steps[checkpoint].action != "wait_state"
        or original.steps[checkpoint].state_equals is None
    ):
        raise ValueError(
            "checkpoint_index must select a main-stage wait_state equality observation"
        )
    if bool(reduced_pair) != bool(plan.kept_indices):
        raise ValueError(
            "Supply --reduced-pair together with its original kept_indices in the plan"
        )
    inputs = {v.step_index for v in plan.variants if isinstance(v, InputChange)}
    if any(index >= checkpoint for index in inputs):
        raise ValueError("A varied input must execute before the original failure checkpoint")
    phases = {"before": (full, indices)}
    if reduced_pair:
        kept = plan.kept_indices
        anchors = {
            checkpoint,
            *inputs,
            *(i for i, step in enumerate(original.steps) if step.screenshot),
        }
        if not original.setup_steps:
            anchors.add(0)
        if kept != sorted(set(kept)) or not set(kept) < set(indices) or not anchors.issubset(kept):
            raise ValueError(
                "Reduced indices must preserve order, initial scene, checkpoints and varied inputs"
            )
        reduced = _read_pair(reduced_pair, full)
        if _definition_key(_subsequence(original, kept)) != _definition_key(
            reduced[0].manifest.definition
        ):
            raise ValueError("Reduced pair must execute exactly the selected original subsequence")
        phases["after"] = (reduced, kept)
    return phases


def _trial_conditions(plan, base, kept, variants):
    """Resolve each requested change to concrete seed, frame delay and immutable builds."""
    definition = base[0].manifest.definition
    seen = set()
    for variant in (None, *plan.variants):
        seed = variant.value if isinstance(variant, Seed) else plan.seed
        delay = variant.value if isinstance(variant, FrameDelay) else plan.start_delay_frames
        pair = base
        candidate = definition
        if isinstance(variant, InputChange):
            candidate = _vary_input(definition, kept.index(variant.step_index), variant)
            pair = variants.get(_definition_key(candidate))
        key = (seed, delay, _definition_key(candidate))
        if key in seen:
            raise ValueError("Duplicate or unchanged trial condition")
        seen.add(key)
        yield (
            {
                "condition": variant.model_dump() if variant else {"axis": "baseline"},
                "seed": seed,
                "start_delay_frames": delay,
                "kept_indices": kept,
                "status": "observed" if pair else "candidate_builds_unavailable",
                "runs": [],
            },
            pair,
        )


def _measure_pair(pair, cell, output, index, graphical, timeout):
    outcomes = [
        _launch(
            build, output / "runs", graphical, timeout, cell["seed"], cell["start_delay_frames"]
        )
        for build in pair
    ]
    environments = [_environment(outcome) for outcome in outcomes]
    if environments[0] != environments[1]:
        raise ValueError("Observed runtime environment changed between replay controls")
    definition = pair[0].manifest.definition
    reports = [outcome.report for outcome in outcomes]
    observed = state_oracle(definition, *reports)
    return environments[0], {
        "broken": _receipt(outcomes[0], output),
        "repaired": _receipt(outcomes[1], output),
        "checkpoint": observed[1] if observed and observed[0] == index else None,
        "broken_observation": _passed_detail(reports[0], index, definition.steps[index]),
        "repair_observation": _passed_detail(reports[1], index, definition.steps[index]),
    }


def _summarize_cell(cell, signature):
    counts = Counter()
    for trial in cell["runs"]:
        if signature is None:
            outcome = "no_reference_failure"
        elif trial["checkpoint"] == signature:
            outcome = "matching_failure"
        elif trial["repair_observation"] != signature["repair_observation"]:
            outcome = "repair_control_failed"
        elif trial["broken_observation"] == signature["repair_observation"]:
            outcome = "passed"
        else:
            outcome = "other_outcome"
        trial["outcome"] = outcome
        counts[outcome] += 1
    denominator = len(cell["runs"])
    measured = denominator and signature
    cell.update(
        counts=dict(counts),
        denominator=denominator,
        matching_failures=counts["matching_failure"],
        broken_statuses=dict(Counter(run["broken"]["status"] for run in cell["runs"])),
        repaired_statuses=dict(Counter(run["repaired"]["status"] for run in cell["runs"])),
        matching_rate=counts["matching_failure"] / denominator if measured else None,
        intermittent=bool(counts["matching_failure"] and counts["passed"]) if measured else None,
    )


def run_replay_trials(
    broken,
    repaired,
    candidate_pairs,
    reduced_pair,
    plan_path,
    output,
    *,
    graphical=False,
    timeout=60,
    max_trials=128,
):
    """Execute admitted original/reduced trials and compare the first observed original signature."""
    plan = TrialPlan.model_validate_json(read_bytes(plan_path, 32768))
    if not 1 <= max_trials <= 128 or len(candidate_pairs) > 128:
        raise ValueError("Replay trials admit at most 128 paired trials and prepared variants")
    if (1 + len(plan.variants)) * plan.attempts * (2 if reduced_pair else 1) > max_trials:
        raise ValueError("Planned paired trials exceed --max-trials")
    full = _read_pair((broken, repaired))
    phases = _trial_phases(plan, full, reduced_pair)

    variants = {}
    for paths in candidate_pairs:
        pair = _read_pair(paths, full)
        key = _definition_key(pair[0].manifest.definition)
        if key in variants:
            raise ValueError("Duplicate prepared input variant")
        variants[key] = pair
    output = safe_local_path(output)
    if any(
        output.is_relative_to(b.path)
        for pair in [*(p for p, _ in phases.values()), *variants.values()]
        for b in pair
    ):
        raise ValueError("Replay trial outputs must remain outside immutable builds")
    conditions = {
        phase: list(_trial_conditions(plan, base, kept, variants))
        for phase, (base, kept) in phases.items()
    }
    output.mkdir(parents=True, exist_ok=False)
    environment = None
    signature = None
    measurements = {}
    for phase, cells in conditions.items():
        measurements[phase] = [cell for cell, _ in cells]
        for cell, pair in cells:
            if not pair:
                continue
            index = cell["kept_indices"].index(plan.checkpoint_index)
            for _ in range(plan.attempts):
                observed_env, trial = _measure_pair(pair, cell, output, index, graphical, timeout)
                if environment not in (None, observed_env):
                    raise ValueError("Observed runtime environment changed between replay trials")
                environment = observed_env
                cell["runs"].append(trial)
                if phase == "before" and signature is None:
                    signature = trial["checkpoint"]
    for cells in measurements.values():
        for cell in cells:
            _summarize_cell(cell, signature)
    after = measurements.get("after", [])
    preserved = [
        i
        for i, (before, shorter) in enumerate(zip(measurements["before"], after))
        if before["matching_failures"] and shorter["matching_failures"]
    ]
    complete = all(cell["runs"] for cells in measurements.values() for cell in cells)
    if not after:
        after_status = "unmeasured"
    elif preserved:
        after_status = "reproduced"
    elif not complete or not signature:
        after_status = "inconclusive"
    else:
        after_status = "not_observed"
    report = {
        "schema_version": 1,
        "checkpoint_original_index": plan.checkpoint_index,
        "signature": signature,
        "plan": plan.model_dump(exclude_none=True),
        "max_trials": max_trials,
        "graphical": graphical,
        "timeout_seconds": timeout,
        "environment": dict(zip(ENVIRONMENT_FIELDS, environment, strict=True)),
        "measurements": measurements,
        "post_reduction": after_status,
        "reproduced_condition_indices": preserved,
        "status": "observed"
        if complete and signature and (not after or preserved)
        else "inconclusive",
    }
    write_new(output / "replay-trials.json", json.dumps(report, indent=2).encode("utf-8"))
    return report
