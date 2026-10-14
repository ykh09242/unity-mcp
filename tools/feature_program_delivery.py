"""Assess frozen caller-recorded provider execution without IO or provider authority."""

from copy import deepcopy
from datetime import datetime
import json
import re
from typing import Final
from urllib.parse import urlsplit

try:
    from .feature_program_coordination import Json, Record
except ImportError:
    from feature_program_coordination import Json, Record


LIMIT: Final = 8 * 1024 * 1024
ROWS: Final = 256
SHOWN: Final = 32
STATUSES: Final = {"queued", "in_progress", "completed"}
CONCLUSIONS: Final = {
    "success",
    "failure",
    "canceled",
    "cancelled",
    "skipped",
    "neutral",
    "timed_out",
    "action_required",
    "pending",
}
MODES: Final = {"required", "policy_skip", "not_applicable", "advisory"}
FAILURES: Final = {"failure", "canceled", "cancelled", "timed_out", "action_required"}


class DeliveryError(ValueError):
    """An explicitly adopted frozen provider envelope is malformed."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeliveryError(message)


def _record(value: Json, name: str) -> Record:
    match value:
        case dict():
            _require(all(isinstance(key, str) for key in value), f"{name} keys must be strings")
            return value
        case _:
            raise DeliveryError(f"{name} must be an object")


def _rows(value: Json, name: str, *, nonempty: bool = False) -> list[Json]:
    match value:
        case list():
            _require(len(value) <= ROWS, f"{name} exceeds {ROWS} rows")
            _require(not nonempty or bool(value), f"{name} cannot be empty")
            return value
        case _:
            raise DeliveryError(f"{name} must be an array")


def _text(value: Json, name: str) -> str:
    match value:
        case str():
            _require(bool(value.strip()), f"{name} cannot be empty")
            _require(len(value.encode("utf-8")) <= 4096, f"{name} exceeds 4 KiB")
            return value
        case _:
            raise DeliveryError(f"{name} must be text")


def _shape(value: Record, required: set[str], optional: set[str], name: str) -> None:
    _require(required <= value.keys(), f"{name} lacks required fields")
    _require(value.keys() <= required | optional, f"{name} has unknown fields")


def _sha(value: Json, name: str) -> str:
    result = _text(value, name)
    _require(re.fullmatch(r"[0-9a-f]{40}", result) is not None, f"{name} needs a full SHA")
    return result


def _positive(value: Json, name: str) -> int:
    _require(type(value) is int and value > 0, f"{name} must be a positive integer")
    return value


def _boolean(value: Json, name: str, *, unknown: bool = False) -> None:
    _require(type(value) is bool or (unknown and value is None), f"{name} must be boolean")


def _time(value: Json, name: str) -> datetime | None:
    if value is None:
        return None
    text = _text(value, name)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryError(f"{name} must be an ISO timestamp") from exc
    _require(result.tzinfo is not None, f"{name} must include a timezone offset")
    return result


def _choice(value: Json, choices: set[str], name: str, *, unknown: bool = False) -> None:
    if unknown and value is None:
        return
    _require(_text(value, name) in choices, f"Unsupported {name}")


def _unique(values: list[Json], field: str, name: str) -> None:
    identifiers = [_record(value, name)[field] for value in values]
    _require(len(identifiers) == len(set(identifiers)), f"Duplicate {name} {field}")


def _bounded(value: Json) -> None:
    try:
        size = len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise DeliveryError("Provider envelope must be finite JSON") from exc
    _require(size <= LIMIT, "Provider envelope exceeds 8 MiB")
    pending = [value]
    nodes = 0
    while pending:
        node = pending.pop()
        nodes += 1
        _require(nodes <= 20000, "Provider envelope exceeds node budget")
        match node:
            case dict():
                pending.extend(node.values())
            case list():
                pending.extend(node)
            case _:
                continue


def _pr(value: Json, repository: str) -> Record:
    pr = _record(value, "PR identity")
    _shape(pr, {"number", "url", "head_sha", "base_ref", "base_sha", "tested_sha"}, set(), "PR")
    number = _positive(pr["number"], "PR number")
    url = urlsplit(_text(pr["url"], "PR URL"))
    _require(
        url.scheme == "https"
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and url.netloc + url.path == f"{repository}/pull/{number}",
        "PR URL must match the exact repository and number",
    )
    for field in ("head_sha", "base_sha", "tested_sha"):
        _sha(pr[field], f"PR {field}")
    _text(pr["base_ref"], "PR base ref")
    return pr


def _check(value: Json) -> Record:
    check = _record(value, "check selection")
    _shape(
        check,
        {
            "id",
            "job",
            "steps",
            "mode",
            "reason",
            "policy_evidence",
            "requires_native",
            "requires_output",
        },
        set(),
        "Check",
    )
    for field in ("id", "job", "reason"):
        _text(check[field], f"Check {field}")
    _choice(check["mode"], MODES, "check mode")
    steps = _rows(check["steps"], "Required steps")
    for step in steps:
        _text(step, "Required step")
    _require(len(steps) == len(set(steps)), "Duplicate selected step")
    policies = _rows(check["policy_evidence"], "Policy evidence")
    for policy in policies:
        _text(policy, "Policy evidence reference")
    for field in ("requires_native", "requires_output"):
        _boolean(check[field], field)
    _require(check["mode"] != "required" or bool(steps), "Required check needs actual test steps")
    _require(
        check["mode"] not in {"policy_skip", "not_applicable"} or bool(policies),
        "Exclusion requires explicit policy evidence",
    )
    return check


def _plans(value: Json, *, captured: bool) -> list[Json]:
    stages = _rows(value, "Stages", nonempty=True)
    _require(len(stages) <= SHOWN, "Provider stages exceed 32")
    for value in stages:
        stage = _record(value, "Stage")
        required = {"id", "kind", "expected_sha", "checks"}
        if captured:
            required.add("observations")
        _shape(stage, required, set(), "Stage")
        _text(stage["id"], "Stage ID")
        _choice(stage["kind"], {"pr_ci", "beta_ci"}, "stage kind")
        _sha(stage["expected_sha"], "Stage expected SHA")
        checks = _rows(stage["checks"], "Selected checks", nonempty=True)
        for check in checks:
            _check(check)
        _unique(checks, "id", "selected check")
        if captured:
            _observations(stage["observations"])
    _unique(stages, "id", "stage")
    return stages


def _observations(value: Json) -> None:
    observations = _rows(value, "Run observations")
    previous: datetime | None = None
    attempts: dict[int, int] = {}
    for value in observations:
        run = _record(value, "Run observation")
        _shape(
            run,
            {
                "run_id",
                "run_attempt",
                "event",
                "head_sha",
                "checkout_sha",
                "status",
                "conclusion",
                "captured_at",
                "complete",
                "jobs",
            },
            set(),
            "Run",
        )
        _positive(run["run_id"], "Run ID")
        if run["run_attempt"] is not None:
            attempt = _positive(run["run_attempt"], "Run attempt")
            run_id = _positive(run["run_id"], "Run ID")
            _require(attempt >= attempts.get(run_id, 0), "Known run attempt moved backward")
            attempts[run_id] = attempt
        _text(run["event"], "Run event")
        for field in ("head_sha", "checkout_sha"):
            _sha(run[field], f"Run {field}")
        _choice(run["status"], STATUSES, "run status")
        _choice(run["conclusion"], CONCLUSIONS, "run conclusion", unknown=True)
        _boolean(run["complete"], "Capture completeness")
        timestamp = _time(run["captured_at"], "Capture timestamp")
        _require(timestamp is not None, "Capture timestamp is required")
        _require(previous is None or timestamp >= previous, "Run capture history is out of order")
        previous = timestamp
        jobs = _rows(run["jobs"], "Jobs")
        for value in jobs:
            job = _record(value, "Job")
            _shape(
                job,
                {
                    "job_id",
                    "name",
                    "status",
                    "conclusion",
                    "started_at",
                    "completed_at",
                    "native",
                    "steps",
                },
                set(),
                "Job",
            )
            _positive(job["job_id"], "Job ID")
            _text(job["name"], "Job name")
            _choice(job["status"], STATUSES, "job status")
            _choice(job["conclusion"], CONCLUSIONS, "job conclusion", unknown=True)
            _boolean(job["native"], "Native execution")
            start = _time(job["started_at"], "Job start")
            end = _time(job["completed_at"], "Job end")
            _require(start is None or end is None or end >= start, "Job timestamps are reversed")
            _require(start is None or start <= timestamp, "Job start is after capture")
            _require(end is None or end <= timestamp, "Job end is after capture")
            steps = _rows(job["steps"], "Job steps")
            for value in steps:
                step = _record(value, "Step")
                _shape(step, {"name", "status", "conclusion", "output_present"}, set(), "Step")
                _text(step["name"], "Step name")
                _choice(step["status"], STATUSES, "step status")
                _choice(step["conclusion"], CONCLUSIONS, "step conclusion", unknown=True)
                _boolean(step["output_present"], "Actual output presence")
            _unique(steps, "name", "step")
        _unique(jobs, "job_id", "job")
        _unique(jobs, "name", "job")


def _context(value: Json) -> Record:
    context = _record(value, "Provider context")
    _bounded(context)
    _shape(context, {"repository", "pr", "beta_sha", "root_actor_id", "stages"}, set(), "Context")
    repository = _text(context["repository"], "Repository")
    _require(
        re.fullmatch(r"[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None,
        "Repository needs hostname/owner/name",
    )
    pr = _pr(context["pr"], repository)
    _text(context["root_actor_id"], "Root actor ID")
    if context["beta_sha"] is not None:
        _sha(context["beta_sha"], "Receiving beta SHA")
    for value in _plans(context["stages"], captured=False):
        stage = _record(value, "Stage")
        expected_sha = pr["tested_sha"] if stage["kind"] == "pr_ci" else context["beta_sha"]
        _require(stage["expected_sha"] == expected_sha, "Stage does not bind its expected revision")
    return context


def validate_execution(value: Json) -> Record:
    """Parse a bounded explicit version-1 capture; return detached caller-recorded facts."""
    capture = _record(value, "Provider execution")
    _bounded(capture)
    _shape(
        capture,
        {
            "schema_version",
            "repository",
            "pr",
            "test_merge",
            "merge",
            "stages",
            "premerge",
            "cleanup",
        },
        set(),
        "Provider execution",
    )
    _require(
        type(capture["schema_version"]) is int and capture["schema_version"] == 1,
        "Unsupported provider execution version",
    )
    repository = _text(capture["repository"], "Repository")
    _pr(capture["pr"], repository)
    _plans(capture["stages"], captured=True)
    for name in ("test_merge", "merge", "premerge", "cleanup"):
        if capture[name] is not None:
            _advisory_shape(name, _record(capture[name], name))
    return deepcopy(capture)


def _advisory_shape(name: str, value: Record) -> None:
    fields = {
        "test_merge": {"head_sha", "base_sha", "merge_sha"},
        "merge": {"method", "reviewed_head", "squash_sha", "receiving_sha", "actor_id"},
        "premerge": {
            "head_sha",
            "base_sha",
            "observed_at",
            "actor_id",
            "method",
            "expected_head_guard",
            "remote_state_checked",
        },
        "cleanup": {
            "branch_sha",
            "target_sha",
            "reviewed_head",
            "squash_sha",
            "content_included",
            "protected",
            "default_branch",
            "integration_branch",
            "dependent_prs",
            "open_unmerged_pr",
            "in_use",
            "dirty",
            "ignored_artifacts_preserved",
            "recovery_verified",
            "actor_id",
            "observed_at",
        },
    }
    _shape(value, fields[name], {"worktree"} if name == "cleanup" else set(), name)
    for key, item in value.items():
        if key.endswith("_sha") or key in {"reviewed_head", "expected_head_guard"}:
            _sha(item, key)
        elif key == "dependent_prs":
            _require(
                item is None or (type(item) is int and item >= 0), "Invalid dependent PR count"
            )
        elif key == "observed_at":
            _require(_time(item, key) is not None, "Observation timestamp is required")
        elif key in {"method", "actor_id"}:
            _text(item, key)
        elif key == "worktree":
            worktree = _record(item, "Worktree observations")
            _shape(
                worktree,
                {
                    "usage_checked",
                    "in_use",
                    "dirty",
                    "unpushed",
                    "ignored_artifacts_preserved",
                    "recovery_verified",
                },
                set(),
                "Worktree",
            )
            for flag in worktree.values():
                _boolean(flag, "Worktree observation", unknown=True)
        else:
            _boolean(item, key, unknown=True)


def _identity(capture: Record, context: Record) -> list[str]:
    reasons: list[str] = []
    for field in ("repository", "pr"):
        if capture[field] != context[field]:
            reasons.append(f"Captured {field} differs from expected context")
    pr = _record(context["pr"], "Expected PR")
    if pr["head_sha"] != pr["tested_sha"]:
        relation = {
            "head_sha": pr["head_sha"],
            "base_sha": pr["base_sha"],
            "merge_sha": pr["tested_sha"],
        }
        if capture["test_merge"] != relation:
            reasons.append("PR test-merge relationship is missing or mismatched")
    if context["beta_sha"] is not None:
        merge = capture["merge"]
        expected_merge = {
            "method": "squash",
            "reviewed_head": pr["head_sha"],
            "squash_sha": context["beta_sha"],
            "receiving_sha": context["beta_sha"],
            "actor_id": context["root_actor_id"],
        }
        if merge != expected_merge:
            reasons.append("Root squash and receiving beta mapping is missing or mismatched")
    return reasons


def _result(status: Json, conclusion: Json) -> str:
    if status != "completed" or conclusion in {None, "pending"}:
        return "pending"
    if conclusion == "success":
        return "passed"
    if conclusion in FAILURES:
        return "failed"
    return "not_assessed"


def _advisory_run_failure(plan: Record, run: Record | None) -> bool:
    """Exclude only complete failures attributed to explicitly selected advisory jobs."""
    if (
        run is None
        or run["status"] != "completed"
        or run["conclusion"] != "failure"
        or run["complete"] is not True
    ):
        return False
    advisory = {
        _record(check, "Check")["job"]
        for check in _rows(plan["checks"], "Checks")
        if _record(check, "Check")["mode"] == "advisory"
    }
    failed = [
        _record(job, "Job")
        for job in _rows(run["jobs"], "Jobs")
        if _record(job, "Job")["conclusion"] in FAILURES
    ]
    return bool(failed) and all(
        job["name"] in advisory and job["status"] == "completed" and job["conclusion"] == "failure"
        for job in failed
    )


def _assess_check(
    selection: Record, run: Record | None, current: bool, advisory_failure: bool
) -> Record:
    result: Record = {
        "id": selection["id"],
        "mode": selection["mode"],
        "status": "not_assessed",
        "applicability": "current" if current else "stale",
        "reported_outcome": None,
        "run_id": None,
        "run_attempt": None,
        "job_id": None,
        "reasons": [],
    }
    reasons: list[Json] = []
    mode = selection["mode"]
    if run is None:
        result["applicability"] = "unknown"
        reasons.append("No current run observation")
    else:
        result.update({"run_id": run["run_id"], "run_attempt": run["run_attempt"]})
        jobs = [_record(value, "Job") for value in _rows(run["jobs"], "Jobs")]
        job = next((job for job in jobs if job["name"] == selection["job"]), None)
        if job is not None:
            result.update({"job_id": job["job_id"], "reported_outcome": job["conclusion"]})
        if not current:
            result["status"] = "failed"
            reasons.append("Run source/event context differs from expected stage")
        elif run["complete"] is not True:
            reasons.append("Run capture is incomplete")
        elif run["conclusion"] in FAILURES and not advisory_failure:
            result["status"] = "failed"
            reasons.append("Current run failed or was canceled")
        elif job is not None and mode != "advisory" and job["conclusion"] in FAILURES:
            result["status"] = "failed"
            reasons.append("Selected execution has an observed failure")
        elif _result(run["status"], run["conclusion"]) != "passed" and not advisory_failure:
            result["status"] = _result(run["status"], run["conclusion"])
            reasons.append("Current run has not completed successfully")
        elif run["run_attempt"] is None:
            reasons.append("Current run attempt is unknown")
        elif mode in {"policy_skip", "not_applicable"}:
            if selection["requires_native"] is True:
                result["status"] = "failed"
                reasons.append("Native-required obligation cannot use an exclusion")
            elif mode == "policy_skip" and (job is None or job["conclusion"] != "skipped"):
                reasons.append("Policy skip lacks an actually skipped job")
            elif job is not None and job["status"] != "completed":
                result["status"] = "pending"
                reasons.append("Excluded job is still running or queued")
            elif job is not None and job["conclusion"] in FAILURES:
                result["status"] = "failed"
                reasons.append("Exclusion conflicts with an observed failure")
            else:
                result["status"] = "policy_skipped" if mode == "policy_skip" else "not_applicable"
        elif job is None:
            if mode == "advisory":
                result["status"] = "advisory"
                reasons.append("Advisory report is unavailable")
            else:
                reasons.append("Selected required job is missing")
        else:
            outcome = _result(job["status"], job["conclusion"])
            if mode == "advisory":
                result["status"] = "advisory"
            else:
                result["status"] = outcome
                if outcome == "passed":
                    step_outcomes: list[str] = []
                    steps = {
                        _record(value, "Step")["name"]: _record(value, "Step")
                        for value in _rows(job["steps"], "Steps")
                    }
                    for name in _rows(selection["steps"], "Selected steps"):
                        step = steps.get(name)
                        if step is None:
                            step_outcomes.append("not_assessed")
                            reasons.append("Selected required test step is missing")
                        elif _result(step["status"], step["conclusion"]) != "passed":
                            step_outcomes.append(_result(step["status"], step["conclusion"]))
                            reasons.append(
                                "Selected required test step did not execute successfully"
                            )
                        elif (
                            selection["requires_output"] is True
                            and step["output_present"] is not True
                        ):
                            step_outcomes.append("not_assessed")
                            reasons.append("Selected test step lacks actual output")
                        else:
                            step_outcomes.append("passed")
                    result["status"] = _combined(step_outcomes)
                    if selection["requires_native"] is True and job["native"] is not True:
                        result["status"] = "failed"
                        reasons.append("Required native execution was not observed")
                elif outcome == "not_assessed":
                    reasons.append("Required execution was skipped or unassessed")
    result["reasons"] = reasons
    return result


def _combined(statuses: list[str]) -> str:
    if "failed" in statuses:
        return "failed"
    if "pending" in statuses:
        return "pending"
    if "not_assessed" in statuses or not statuses:
        return "not_assessed"
    return "passed"


def _timing(run: Record | None) -> Record:
    timing: Record = {"started_at": None, "completed_at": None, "duration_seconds": None}
    if run is None:
        return timing
    jobs = [_record(value, "Job") for value in _rows(run["jobs"], "Jobs")]
    starts = [_time(job["started_at"], "Job start") for job in jobs]
    ends = [_time(job["completed_at"], "Job end") for job in jobs]
    if not jobs or any(value is None for value in starts + ends):
        return timing
    start = min(value for value in starts if value is not None)
    end = max(value for value in ends if value is not None)
    timing.update(
        {
            "started_at": start.isoformat(),
            "completed_at": end.isoformat(),
            "duration_seconds": (end - start).total_seconds(),
        }
    )
    return timing


def assess_execution(value: Json, expected: Json) -> Record:
    """Compare frozen captures with explicit expected obligations; never authenticate them."""
    capture = validate_execution(value)
    context = _context(expected)
    reasons = _identity(capture, context)
    stages = {
        _record(item, "Stage")["id"]: _record(item, "Stage")
        for item in _rows(capture["stages"], "Captured stages")
    }
    plans = [_record(item, "Stage") for item in _rows(context["stages"], "Expected stages")]
    if set(stages) != {plan["id"] for plan in plans}:
        reasons.append("Captured stages do not cover exact expected stage identities")
    projected: list[Json] = []
    pr = _record(context["pr"], "Expected PR")
    for plan in plans:
        stage = stages.get(plan["id"])
        stage_matches = stage is not None and all(stage[key] == plan[key] for key in plan)
        observations = (
            []
            if stage is None
            else [_record(item, "Run") for item in _rows(stage["observations"], "Observations")]
        )
        run = observations[-1] if observations else None
        expected_event = "pull_request" if plan["kind"] == "pr_ci" else "push"
        expected_head = pr["head_sha"] if plan["kind"] == "pr_ci" else context["beta_sha"]
        current = bool(
            not reasons
            and stage_matches
            and run is not None
            and run["event"] == expected_event
            and run["head_sha"] == expected_head
            and run["checkout_sha"] == plan["expected_sha"]
        )
        advisory_failure = _advisory_run_failure(plan, run)
        checks = [
            _assess_check(_record(item, "Check"), run, current, advisory_failure)
            for item in _rows(plan["checks"], "Selected checks")
        ]
        statuses = [
            _text(_record(item, "Check result")["status"], "Check status") for item in checks
        ]
        status = _combined(statuses)
        if not stage_matches or reasons:
            status = "failed"
        elif run is not None and run["status"] != "completed" and status != "failed":
            status = "pending"
        history: list[Json] = [
            {
                "run_id": item["run_id"],
                "run_attempt": item["run_attempt"],
                "reported_outcome": item["conclusion"],
                "status": item["status"],
                "head_sha": item["head_sha"],
                "checkout_sha": item["checkout_sha"],
                "captured_at": item["captured_at"],
                "job_ids": [
                    _record(job, "Job")["job_id"] for job in _rows(item["jobs"], "Jobs")[:SHOWN]
                ],
                "jobs_total": len(_rows(item["jobs"], "Jobs")),
                "jobs_shown": min(SHOWN, len(_rows(item["jobs"], "Jobs"))),
                "jobs_omitted": max(0, len(_rows(item["jobs"], "Jobs")) - SHOWN),
            }
            for item in observations
        ]
        projected.append(
            {
                "id": plan["id"],
                "kind": plan["kind"],
                "status": status,
                "current": current,
                "reported_outcome": None if run is None else run["conclusion"],
                "source": {
                    "head_sha": expected_head,
                    "base_sha": pr["base_sha"],
                    "tested_sha": plan["expected_sha"],
                },
                "checks": checks[:SHOWN],
                "checks_total": len(checks),
                "checks_shown": min(SHOWN, len(checks)),
                "checks_omitted": max(0, len(checks) - SHOWN),
                "history": history[-SHOWN:],
                "history_total": len(history),
                "history_shown": min(SHOWN, len(history)),
                "history_omitted": max(0, len(history) - SHOWN),
                "timing": _timing(run),
            }
        )
    result: Record = {
        "status": "failed"
        if reasons
        else _combined(
            [_text(_record(stage, "Stage result")["status"], "Stage status") for stage in projected]
        ),
        "remote_verified_by_tool": False,
        "stages": projected,
        "reasons": reasons,
        "total": len(projected),
        "shown": len(projected),
        "omitted": 0,
    }
    _bounded(result)
    return result


def _advisory(status: str, reasons: list[str]) -> Record:
    return {
        "status": status,
        "reasons": reasons,
        "advisory": True,
        "remote_verified_by_tool": False,
    }


def assess_premerge(value: Json, expected: Json) -> Record:
    """Prepare a root-only squash decision; no output authorizes or performs merge."""
    capture = validate_execution(value)
    context = _context(expected)
    observation = capture["premerge"]
    if observation is None:
        return _advisory("unknown", ["No premerge observation"])
    data = _record(observation, "Premerge")
    pr = _record(context["pr"], "Expected PR")
    reasons = _identity(capture, context)
    required = {
        "head_sha": pr["head_sha"],
        "base_sha": pr["base_sha"],
        "actor_id": context["root_actor_id"],
        "method": "squash",
        "expected_head_guard": pr["head_sha"],
        "remote_state_checked": True,
    }
    if any(data[key] != item for key, item in required.items()):
        reasons.append(
            "Premerge identity, root inspection or expected-head squash guard is stale/unknown"
        )
    assessment = assess_execution(capture, context)
    pr_stages = [
        _record(stage, "Stage")
        for stage in _rows(assessment["stages"], "Stages")
        if _record(stage, "Stage")["kind"] == "pr_ci"
    ]
    if not pr_stages or any(stage["status"] != "passed" for stage in pr_stages):
        reasons.append("Required current PR execution is incomplete")
    return _advisory("deferred" if reasons else "ready", reasons)


def assess_cleanup(value: Json, expected: Json) -> Record:
    """Assess recorded retirement facts without inferring use, inclusion or deletion authority."""
    capture = validate_execution(value)
    context = _context(expected)
    observation = capture["cleanup"]
    if observation is None:
        return _advisory("unknown", ["No cleanup observation"])
    data = _record(observation, "Cleanup")
    pr = _record(context["pr"], "Expected PR")
    reasons = _identity(capture, context)
    required = {
        "branch_sha": pr["head_sha"],
        "reviewed_head": pr["head_sha"],
        "target_sha": context["beta_sha"],
        "squash_sha": context["beta_sha"],
        "actor_id": context["root_actor_id"],
        "content_included": True,
        "protected": False,
        "default_branch": False,
        "integration_branch": False,
        "dependent_prs": 0,
        "open_unmerged_pr": False,
        "in_use": False,
        "dirty": False,
        "ignored_artifacts_preserved": True,
        "recovery_verified": True,
    }
    if context["beta_sha"] is None or any(data[key] != item for key, item in required.items()):
        reasons.append("Branch identity, inclusion, use or preservation is unsafe/unknown")
    if "worktree" in data:
        tree = _record(data["worktree"], "Worktree")
        safe = {
            "usage_checked": True,
            "in_use": False,
            "dirty": False,
            "unpushed": False,
            "ignored_artifacts_preserved": True,
            "recovery_verified": True,
        }
        if tree != safe:
            reasons.append("Worktree use, changes or private recovery is unsafe/unknown")
    assessment = assess_execution(capture, context)
    beta = [
        _record(stage, "Stage")
        for stage in _rows(assessment["stages"], "Stages")
        if _record(stage, "Stage")["kind"] == "beta_ci"
    ]
    if not beta or assessment["status"] != "passed":
        reasons.append("Required PR and receiving-beta execution is incomplete")
    return _advisory("deferred" if reasons else "ready", reasons)
