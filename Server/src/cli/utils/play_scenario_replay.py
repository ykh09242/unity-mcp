"""Reduce a main-stage state replay by running its broken and repaired frozen Players."""

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path, write_new
from cli.utils.play_scenario_html import export_html
from cli.utils.play_scenario_player import (
    MANIFEST_LIMIT,
    REPORT_LIMIT,
    PlayerBundle,
    PlayerRunOptions,
    _read_json,
    run_player,
)
from cli.utils.play_scenario_reports import write_player_artifacts
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS
from models.play_scenarios import PlayScenario
from pydantic import ValidationError


@dataclass(frozen=True)
class Build:
    path: Path
    manifest: PlayerBundle

    @classmethod
    def read(cls, path):
        path = safe_local_path(path)
        manifest = PlayerBundle.model_validate(
            _read_json(path / "scenario-bundle.json", MANIFEST_LIMIT)
        )
        if manifest.payload is None or not manifest.build_source_revision:
            raise ValueError(
                "Reduction requires verified Players with a known build source revision"
            )
        return cls(path, manifest)

    def settings(self):
        m = self.manifest
        return (m.build_target, m.scripting_backend, m.unity_version, m.package_version)


def _definition_key(definition):
    return json.dumps(
        definition.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":")
    )


def _read_pair(paths, full=None):
    broken, repaired = (Build.read(p) for p in paths)
    if _definition_key(broken.manifest.definition) != _definition_key(repaired.manifest.definition):
        raise ValueError("Both controls must execute the same definition")
    if (
        broken.settings() != repaired.settings()
        or broken.manifest.build_source_revision == repaired.manifest.build_source_revision
    ):
        raise ValueError("Controls require matching build settings and distinct source revisions")
    pair = (broken, repaired)
    if full and any(
        build.settings() != base.settings()
        or build.manifest.build_source_revision != base.manifest.build_source_revision
        for build, base in zip(pair, full, strict=True)
    ):
        raise ValueError("A candidate build differs from its original control source or settings")
    return pair


def _launch(build, output, graphical, timeout, seed=None, start_delay_frames=0):
    manifest = build.manifest
    result = run_player(
        PlayerRunOptions(
            build.path,
            output,
            timeout_seconds=timeout,
            cleanup_wait_seconds=60,
            graphical=graphical,
            require_verified_payload=True,
            expected_build_revision=manifest.build_source_revision,
            expected_build_id=manifest.build_id,
            expected_payload_hash=manifest.payload_hash,
            replay_seed=seed,
            start_delay_frames=start_delay_frames,
        )
    )
    report = result.report
    if result.client_error or report.get("report_error") or report.get("cleanup_failures"):
        raise ValueError(
            "Player infrastructure or cleanup failed; inspect " + str(result.directory)
        )
    if report["reproduction"]["definition_hash"] != manifest.definition_hash:
        raise ValueError("Player executed a different frozen definition")
    write_player_artifacts(report, result.directory)
    export_html(result.directory / "run.json", result.directory / "report.html")
    return result


def _receipt(outcome, output):
    report = outcome.report
    reproduction = report["reproduction"]
    encoded = read_bytes(outcome.directory / "run.json", REPORT_LIMIT)
    if json.loads(encoded) != report:
        raise ValueError("Saved report differs from the launched replay")
    return {
        "status": report["status"],
        "job_id": report["job_id"],
        "exit_code": report["exit_code"],
        "definition_hash": reproduction["definition_hash"],
        "build_id": reproduction["build_id"],
        "source_revision": reproduction["build_source_revision"],
        "payload_hash": reproduction["payload_hash"],
        "report": (outcome.directory.relative_to(output) / "run.json").as_posix(),
        "report_sha256": sha256(encoded).hexdigest(),
    }


def _environment(outcome):
    environment = outcome.report["reproduction"].get("environment") or {}
    values = tuple(environment.get(key) for key in ENVIRONMENT_FIELDS)
    if any(
        value is None or str(value).strip().lower() in ("", "unknown", "unavailable")
        for value in values
    ):
        raise ValueError("Reduction requires complete observed runtime environments")
    return values


def state_oracle(definition, broken, repaired):
    """Admit the first observed unmet value and its independently observed successful control."""
    if broken.get("status") not in ("failed", "timed_out") or repaired.get("status") != "succeeded":
        return None
    failure = broken.get("failure") or {}
    index = failure.get("step_index")
    if (
        (failure.get("stage"), failure.get("iteration"), failure.get("code"))
        != ("main", 1, "condition_unmet")
        or type(index) is not int
        or not 0 <= index < len(definition.steps)
    ):
        return None
    step = definition.steps[index]
    if (
        step.action != "wait_state"
        or step.state_equals is None
        or step.state_id != failure.get("target")
    ):
        return None
    expected, actual = failure.get("expected"), failure.get("actual")
    if not isinstance(expected, str) or not isinstance(actual, str) or actual == expected:
        return None
    try:
        observed = json.loads(actual)
        wanted = json.loads(expected)
    except ValueError:
        return None
    if (
        type(wanted) is not type(step.state_equals)
        or wanted != step.state_equals
        or not isinstance(observed, (str, bool, int, float))
        or isinstance(observed, float)
        and not math.isfinite(observed)
        or type(observed) is type(wanted)
        and observed == wanted
    ):
        return None
    failures = [
        row
        for row in broken.get("steps", [])
        if row.get("status") in ("failed", "timed_out", "cancelled")
    ]
    if not failures:
        return None
    first = failures[0]

    def matches(row):
        return (
            row.get("stage"),
            row.get("iteration"),
            row.get("step_index"),
            row.get("action"),
            row.get("name"),
        ) == ("main", 1, index, step.action, step.name)

    controls = [row for row in repaired.get("steps", []) if matches(row)]
    if not matches(first) or len(controls) != 1 or controls[0].get("status") != "passed":
        return None
    # Native wait_state emits both JSON scalar values in a successful observation.
    detail = controls[0].get("detail")
    if detail != f"State {step.state_id}: {expected}; expected: {expected}.":
        return None
    return index, {
        "name": step.name,
        "action": step.action,
        "target": step.state_id,
        "code": failure["code"],
        "expected": expected,
        "actual": actual,
        "repair_observation": detail,
    }


def deletion_candidates(definition, kept, pinned, groups):
    """Try declared dependencies, mark/read groups and successively finer contiguous chunks."""
    removable = [index for index in kept if index not in pinned]
    marks = {}
    for index in kept:
        step = definition.steps[index]
        if step.event_mark:
            marks[step.event_mark] = [index]
        elif step.event_since in marks:
            marks[step.event_since].append(index)
    available = set(removable)
    for group in (*groups, *marks.values()):
        if len(group) > 1 and available.issuperset(group):
            yield tuple(i for i in kept if i not in group)
    # Partition only deletable steps so pinned checkpoints can divide a dependency group.
    size = len(removable)
    while size:
        for start in range(0, len(removable), size):
            chunk = set(removable[start : start + size])
            yield tuple(i for i in kept if i not in chunk)
        size = (size + 1) // 2 if size > 1 else 0


def reduce_replay(
    broken,
    repaired,
    candidate_pairs,
    output,
    *,
    builder=None,
    graphical=False,
    timeout=60,
    max_trials=24,
    groups=(),
    grouped=False,
    seed=None,
    start_delay_frames=0,
    attempts=1,
):
    """Delete groups and single steps while preserving the original state oracle."""
    if len(candidate_pairs) > 128 or not 1 <= max_trials <= 128:
        raise ValueError("Reduction admits at most 128 prepared pairs and trials")
    if not 1 <= attempts <= max_trials:
        raise ValueError("Attempts per candidate must be within the total paired trial budget")
    if start_delay_frames and seed is None:
        raise ValueError("A replay start delay requires an explicit seed")
    full = _read_pair((broken, repaired))
    definition = full[0].manifest.definition
    if len(groups) > 128 or any(
        not 2 <= len(group) <= len(definition.steps)
        or len(set(group)) != len(group)
        or any(type(i) is not int or not 0 <= i < len(definition.steps) for i in group)
        for group in groups
    ):
        raise ValueError("Supply up to 128 groups of unique, in-range original step indices")
    sources = {}
    for paths in candidate_pairs:
        pair = _read_pair(paths, full)
        key = _definition_key(pair[0].manifest.definition)
        if key in sources:
            raise ValueError("Duplicate replay candidate definition")
        sources[key] = pair
    output = safe_local_path(output)
    for pair in [full, *sources.values()]:
        if any(output.is_relative_to(build.path) for build in pair):
            raise ValueError("Replay outputs must remain outside immutable builds")
    if builder:
        builder = safe_local_path(builder).resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    trials = []
    environment = None

    def trial(pair, kept):
        nonlocal environment
        outcomes = [
            _launch(build, output / "runs", graphical, timeout, seed, start_delay_frames)
            for build in pair
        ]
        environments = [_environment(run) for run in outcomes]
        if environments[0] != environments[1] or environment not in (None, environments[0]):
            raise ValueError("Observed runtime environment changed between replay controls")
        environment = environments[0]
        result = state_oracle(pair[0].manifest.definition, *(run.report for run in outcomes))
        record = {
            "kept_indices": list(kept),
            "broken": _receipt(outcomes[0], output),
            "repaired": _receipt(outcomes[1], output),
            "checkpoint": None,
        }
        if result:
            index, identity = result
            record["checkpoint"] = {**identity, "step_index": index, "original_index": kept[index]}
        trials.append(record)
        return result

    kept = list(range(len(definition.steps)))
    original = None
    for _ in range(attempts):
        original = trial(full, kept)
        if original is not None:
            break
    if original is None:
        raise ValueError(
            "Original pair did not reproduce an observed state mismatch and its passing repair"
        )
    failing_index, identity = original
    pinned = {failing_index}
    if not definition.setup_steps:
        pinned.add(0)
    pinned.update(i for i, step in enumerate(definition.steps) if step.screenshot)
    tested = set()
    unavailable = []
    before = chosen = trials[-1]
    stop = "group_deletion_fixed_point" if grouped or groups else "single_deletion_fixed_point"
    missing = set()
    while True:
        changed = False
        incomplete = False
        candidates = (
            deletion_candidates(definition, kept, pinned, groups)
            if grouped or groups
            else (tuple(i for i in kept if i != index) for index in kept if index not in pinned)
        )
        for indices in candidates:
            if indices in missing:
                incomplete = True
            if indices in tested:
                continue
            tested.add(indices)
            raw = definition.model_dump(mode="json", exclude_none=True)
            raw["steps"] = [raw["steps"][i] for i in indices]
            try:
                candidate = PlayScenario.model_validate(raw)
            except ValidationError:
                continue
            if len(trials) >= max_trials:
                stop = "trial_limit"
                break
            key = _definition_key(candidate)
            pair = sources.get(key)
            if pair is None and builder:
                request = output / f"candidate-{len(trials)}"
                path = write_new(request / "definition.json", key.encode("utf-8"))
                paths = (request / "broken", request / "repaired")
                subprocess.run(
                    [str(builder), str(path), *map(str, paths)],
                    check=True,
                    timeout=1800,
                    shell=False,
                )
                pair = _read_pair(paths, full)
                if _definition_key(pair[0].manifest.definition) != key:
                    raise ValueError("Builder returned a different replay definition")
            if pair is None:
                incomplete = True
                missing.add(indices)
                unavailable.append(list(indices))
                continue
            for _ in range(attempts):
                if len(trials) >= max_trials:
                    stop = "trial_limit"
                    break
                result = trial(pair, indices)
                if result and indices[result[0]] == failing_index and result[1] == identity:
                    kept, chosen = list(indices), trials[-1]
                    changed = True
                    break
            if changed or stop == "trial_limit":
                break
        if not changed:
            break
    if incomplete and stop != "trial_limit":
        stop = "candidate_builds_unavailable"
    report = {
        "schema_version": 1,
        "status": "reduced" if len(kept) < len(definition.steps) else "unchanged",
        "stop": stop,
        "pinned_indices": sorted(pinned),
        "deletion_groups": [list(group) for group in groups],
        "unavailable_candidates": unavailable,
        "environment": dict(zip(ENVIRONMENT_FIELDS, environment, strict=True)),
        "graphical": graphical,
        "timeout_seconds": timeout,
        "max_trials": max_trials,
        "attempts_per_candidate": attempts,
        "replay_seed": seed,
        "start_delay_frames": start_delay_frames,
        "before": before,
        "after": chosen,
        "trials": trials,
    }
    write_new(output / "reduction.json", json.dumps(report, indent=2).encode("utf-8"))
    source = definition.model_dump(mode="json", exclude_none=True)
    source["steps"] = [source["steps"][i] for i in kept]
    write_new(output / "reduced.json", json.dumps(source, indent=2).encode("utf-8"))
    return report
