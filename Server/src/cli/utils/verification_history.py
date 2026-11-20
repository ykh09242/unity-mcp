"""Locate a regression boundary in an explicitly ordered sequence of archived builds."""

from pathlib import Path

from .play_scenario_analysis_io import AnalysisError, read_json, safe_local_path
from .verification_change import read_run, same_json


def inspect_history(manifest: Path, kind: str, name: str):
    manifest = safe_local_path(manifest)
    history = read_json(manifest)
    builds = history.get("builds")
    if (
        history.get("schema") != "verification_build_history@1"
        or not isinstance(builds, list)
        or not 2 <= len(builds) <= 128
    ):
        raise ValueError("Expected an oldest-first history of 2 to 128 named builds")
    total_bytes = 0
    evidence_count = 0

    def local_artifact(relative):
        nonlocal total_bytes
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValueError("Expected a relative artifact path")
        parts = relative.split("/")
        if any(not part or part.rstrip(" .") != part or ":" in part for part in parts):
            raise ValueError("Artifact path must remain inside the selected history")
        path = safe_local_path(manifest.parent.joinpath(*parts))
        try:
            total_bytes += path.stat().st_size
        except FileNotFoundError:
            return None
        if total_bytes > 128 * 1024 * 1024:
            raise ValueError("History input exceeds 128 MiB")
        return path

    ids, jobs, verification_ids = set(), set(), set()
    timeline = []
    reference_conditions = None
    for item in builds:
        if not isinstance(item, dict):
            raise AnalysisError("Expected a build history record")
        build_id, source, runs = item.get("build_id"), item.get("source_revision"), item.get("runs")
        if (
            not isinstance(build_id, str)
            or not build_id
            or len(build_id) > 256
            or build_id in ids
            or not isinstance(source, str)
            or len(source) != 64
            or any(c not in "0123456789abcdef" for c in source)
        ):
            raise ValueError("Expected distinct build IDs with recorded source digests")
        if not isinstance(runs, list) or len(runs) > 16:
            raise ValueError("A build may supply at most 16 explicit trial pairs")
        ids.add(build_id)
        observations = []
        build_artifacts = None
        for pair in runs:
            evidence_count += 1
            if not isinstance(pair, dict) or evidence_count > 256:
                raise ValueError("Invalid or excessive history trial pairs")
            verification = local_artifact(pair.get("verification"))
            native = local_artifact(pair.get("native"))
            if verification is None or native is None:
                observations.append({"state": "missing"})
                continue
            run = read_run(verification, native)
            if (run.build_id, run.source) != (build_id, source):
                raise ValueError("Archive build/source differs from the requested history entry")
            if build_artifacts is not None and (run.payload, run.manifest) != build_artifacts:
                raise ValueError("Archive trials disagree on the payload or manifest of one build")
            build_artifacts = (run.payload, run.manifest)
            observed_jobs = run.observed_jobs()
            if jobs & observed_jobs or run.run_id in verification_ids:
                raise ValueError("A history trial reused an execution")
            jobs.update(observed_jobs)
            verification_ids.add(run.run_id)
            check = run.checks.get((kind, name), {})
            state = check.get("state", "not_run")
            if not run.inputs_confirmed or state not in {"passed", "failed"}:
                state = "incomplete"
            else:
                conditions = (
                    run.conditions,
                    check.get("target"),
                    check.get("mode"),
                    check.get("definition_hash"),
                    check.get("expected_state"),
                )
                if reference_conditions is None:
                    reference_conditions = conditions
                elif not same_json(conditions, reference_conditions):
                    state = "not_comparable"
            trial = {"run_id": run.run_id, "job_id": run.job_id, "state": state}
            if check.get("child_job_ids"):
                trial["check_job_ids"] = check["child_job_ids"]
            observations.append(trial)
        states = {row["state"] for row in observations}
        status = "missing"
        if {"passed", "failed"} <= states:
            status = "intermittent"
        elif states in ({"passed"}, {"failed"}):
            status = states.pop()
        elif states and states != {"missing"}:
            status = "incomplete"
        timeline.append(
            {
                "build_id": build_id,
                "source_revision": source,
                "state": status,
                "trials": observations,
            }
        )

    states = [row["state"] for row in timeline]
    first_failed = next((i for i, v in enumerate(states) if v == "failed"), None)
    observed_good = [
        i
        for i, v in enumerate(states)
        if v == "passed" and (first_failed is None or i < first_failed)
    ]
    last_good = observed_good[-1] if observed_good else None
    gaps = [row["build_id"] for row in timeline if row["state"] in {"incomplete", "missing"}]
    if (
        "intermittent" in states
        or first_failed is not None
        and "passed" in states[first_failed + 1 :]
    ):
        status = "unstable"
    elif first_failed is None:
        status = "incomplete" if gaps else "no_observed_failure"
    elif last_good is None:
        status = "missing_passing_baseline"
    elif gaps:
        status = "candidate_range"
    else:
        status = "first_failure"
    return {
        "schema": "verification_regression_window@1",
        "state": status,
        "check": {"kind": kind, "id": name},
        "last_observed_passing_build": timeline[last_good]["build_id"]
        if last_good is not None
        else None,
        "first_observed_failing_build": timeline[first_failed]["build_id"]
        if first_failed is not None
        else None,
        "candidate_builds": [
            row["build_id"]
            for row in timeline[(last_good + 1 if last_good is not None else 0) : first_failed + 1]
        ]
        if first_failed is not None
        else [],
        "unverified_builds": gaps,
        "timeline": timeline,
    }
