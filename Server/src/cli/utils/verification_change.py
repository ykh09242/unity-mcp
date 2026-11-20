"""Match public verification reports to separately retained native Player evidence."""

from dataclasses import dataclass
from pathlib import Path
import re

from cli.utils.play_scenario_analysis_io import read_json


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OBSERVED = {"passed", "failed"}
_TERMINAL = _OBSERVED | {"incomplete", "stale", "canceled"}
_ENV = (
    "operating_system",
    "platform",
    "architecture",
    "scripting_backend",
    "build_mode",
    "runtime_version",
)


def _object(value, field):
    if not isinstance(value, dict):
        raise ValueError(f"Missing or invalid {field}")
    return value


def _name(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"Missing or invalid {field}")
    return value


def _digest(value, field):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"Missing or invalid {field}")
    return value


def _rows(data, field):
    value = data.get(field, [])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Invalid {field}")
    return value


def _stages(report):
    stages = {}
    for row in _rows(report, "stages"):
        key = _name(row.get("name"), "stage name")
        if key in stages:
            raise ValueError("Duplicate verification stage")
        stages[key] = row
    return stages


@dataclass
class CapturedRun:
    run_id: str
    job_id: str
    build_id: str
    source: str
    manifest: str
    baseline_manifest: str | None
    baseline_source: str | None
    conditions: dict
    checks: dict
    state: str
    scenario: str

    def identity(self):
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "build_id": self.build_id,
            "source_revision": self.source,
        }


def read_run(report_path: Path, native_path: Path) -> CapturedRun:
    report = read_json(report_path)
    if report.get("success") is True:
        report = _object(report.get("data"), "verification response data")
    native = read_json(native_path)
    if report.get("schema") != "verification_report@1" or report.get("state") not in _TERMINAL:
        raise ValueError("Expected a terminal verification report")
    if report.get("native_settled") is not True:
        raise ValueError("Native work has not settled")
    stages = _stages(report)
    player_stage = _object(stages.get("standalone_player"), "standalone_player stage")
    evidence = _object(player_stage.get("evidence"), "Player evidence")
    source = _digest(report.get("source_token"), "verified source token")
    comparison = _object(report.get("comparison"), "candidate comparison")
    reproduction = _object(native.get("reproduction"), "Player reproduction")
    job = _name(evidence.get("job_id"), "Player job id")
    build = _name(evidence.get("build_id"), "Player build id")
    payload = _digest(evidence.get("payload_hash"), "verified payload")
    scenario = _name(evidence.get("scenario"), "Player scenario")
    if (
        native.get("execution_environment") != "player"
        or native.get("phase") != "finished"
        or native.get("runner_resources_released") is not True
    ):
        raise ValueError("Expected a finished native Player execution")
    if (
        native.get("finalization_state") != "completed"
        or evidence.get("finalization_state") != "completed"
    ):
        raise ValueError("Player execution did not finalize")
    status = native.get("status")
    if (
        native.get("job_id"),
        reproduction.get("build_id"),
        reproduction.get("payload_hash"),
        reproduction.get("build_source_revision"),
        comparison.get("source_revision"),
        evidence.get("build_source_revision"),
        _object(native.get("scenario"), "native scenario").get("name"),
        evidence.get("status"),
    ) != (job, build, payload, source, source, source, scenario, status):
        raise ValueError("Verification and native Player identities or outcomes disagree")
    if reproduction.get("payload_verification") != "launcher_admission":
        raise ValueError("Expected a launcher-verified Player payload")
    owned = {
        (c.get("kind"), c.get("job_id"))
        for c in _rows(report, "children")
        if c.get("settled") is True
    }
    if ("standalone_player", job) not in owned:
        raise ValueError("Player job is not a settled child of this verification")
    if not any(
        row.get("kind") == "scenario"
        and row.get("id") == scenario
        and row.get("job_id") == job
        and row.get("status") == status
        for row in _rows(report, "executed_checks")
    ):
        raise ValueError("Missing native scenario execution in verification report")
    state = player_stage.get("state")
    if state not in _TERMINAL:
        raise ValueError("Invalid standalone Player stage state")
    if type(native.get("exit_code")) is not int or native["exit_code"] != evidence.get("exit_code"):
        raise ValueError("Recorded process exit disagrees with Player evidence")
    if state == "passed" and (status != "succeeded" or evidence.get("exit_code") != 0):
        raise ValueError("Passing Player stage contradicts its native outcome")
    if state == "failed" and status not in {"failed", "timed_out"}:
        raise ValueError("Failed Player stage contradicts its native outcome")
    installation = _object(report.get("installation"), "installation")
    environment = _object(reproduction.get("environment"), "Player environment")
    installed_unity = _name(installation.get("unity_version"), "installed Unity version")
    if reproduction.get("unity_version") != installed_unity:
        raise ValueError("Editor and Player Unity versions differ")
    conditions = {key: _name(environment.get(key), key) for key in _ENV}
    conditions.update(
        project=_name(report.get("project_scope"), "project scope"),
        unity_version=installed_unity,
        package_version=_name(reproduction.get("package_version"), "package version"),
        definition_hash=_digest(reproduction.get("definition_hash"), "scenario definition hash"),
        repeat_count=native.get("repeat_count"),
        scenario=scenario,
        replay_seed=reproduction.get("replay_seed"),
        start_delay_frames=reproduction.get("start_delay_frames"),
    )
    observed = stages.get("observed_state", {})
    conditions["expected_state"] = observed.get("evidence", {}).get("expected")
    signature = _object(native.get("failure") or {}, "native failure")
    signature = {
        k: signature[k]
        for k in (
            "code",
            "stage",
            "iteration",
            "step_index",
            "target",
            "property_path",
            "expected",
            "actual",
        )
        if k in signature
    }
    primary = ("scenario", scenario)
    checks = {
        primary: {
            "state": state,
            "target": "standalone_player",
            "job_id": job,
            "signature": signature,
        }
    }
    if observed:
        observation = observed.get("evidence", {})
        checks[("observation", scenario)] = {
            "state": observed.get("state"),
            "target": "standalone_player",
            "signature": observation.get("observed") if observed.get("state") == "failed" else None,
        }
    selected = set()
    for row in _rows(report, "selected_check_results"):
        key = (_name(row.get("kind"), "check kind"), _name(row.get("id"), "check id"))
        if key in selected:
            raise ValueError("Duplicate selected check")
        selected.add(key)
        data = _object(row.get("evidence"), "selected check evidence")
        if key == primary:
            if row.get("job_id") != job or row.get("state") != state:
                raise ValueError("Selected Player check contradicts native evidence")
            continue
        result = row.get("state")
        target = data.get("target")
        if result in _OBSERVED:
            ids = data.get("child_job_ids", [])
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                raise ValueError("Invalid selected job identities")
            child_ids = set(ids)
            if row.get("job_id"):
                child_ids.add(row["job_id"])
            if target != "editor" or not child_ids or not child_ids.issubset({j for _, j in owned}):
                result = "incomplete"
        checks[key] = {
            "state": result,
            "target": target,
            "mode": data.get("mode"),
            "definition_hash": data.get("definition_hash"),
        }
    for row in _rows(report, "additional_checks_outstanding"):
        key = (_name(row.get("kind"), "outstanding kind"), _name(row.get("id"), "outstanding id"))
        checks.setdefault(key, {"state": "not_run"})
    # A failed or partial stage can invalidate the parent even when every named check passed.
    for name, stage in stages.items():
        if name not in {"standalone_player", "observed_state", "selected_checks"} and stage.get(
            "required", True
        ):
            checks[("stage", name)] = {"state": stage.get("state"), "target": "verification"}
    return CapturedRun(
        _name(report.get("run_id"), "run id"),
        job,
        build,
        source,
        _digest(comparison.get("manifest_sha256"), "candidate manifest"),
        comparison.get("baseline_manifest_sha256"),
        comparison.get("baseline_source_revision"),
        conditions,
        checks,
        report["state"],
        scenario,
    )


def compare_runs(before: CapturedRun, after: CapturedRun):
    if before.run_id == after.run_id or before.job_id == after.job_id:
        raise ValueError("Reused verification or Player job evidence")
    if before.build_id == after.build_id and before.source != after.source:
        raise ValueError("One Player build claims different source revisions")
    if (after.baseline_manifest, after.baseline_source) != (before.manifest, before.source):
        raise ValueError("Candidate comparison did not capture this baseline")
    changed = sorted(
        k
        for k in before.conditions.keys() | after.conditions.keys()
        if before.conditions.get(k) != after.conditions.get(k)
    )
    result = {
        "schema": "verification_change@1",
        "baseline": before.identity(),
        "candidate": after.identity(),
        "different_conditions": changed,
        "checks": [],
    }
    if changed:
        return {**result, "state": "not_comparable"}
    new_failure = False
    partial = before.state not in _OBSERVED or after.state not in _OBSERVED
    for kind, key in sorted(before.checks.keys() | after.checks.keys()):
        old = before.checks.get((kind, key), {"state": "not_run"})
        new = after.checks.get((kind, key), {"state": "not_run"})
        if old["state"] not in _OBSERVED or new["state"] not in _OBSERVED:
            status = "not_run" if "not_run" in (old["state"], new["state"]) else "incomplete"
            partial = True
        elif any(
            old.get(field) != new.get(field) for field in ("target", "mode", "definition_hash")
        ):
            status = "not_comparable"
            partial = True
        elif old["state"] == new["state"] == "failed":
            status = (
                "changed_failure"
                if old.get("signature") != new.get("signature")
                else "existing_failure"
            )
        elif new["state"] == "failed":
            status = "new_failure"
        elif old["state"] == "failed":
            status = "repaired"
        else:
            status = "unchanged_pass"
        new_failure |= status in {"new_failure", "changed_failure"}
        result["checks"].append(
            {"kind": kind, "id": key, "classification": status, "before": old, "after": new}
        )
    result["coverage_complete"] = not partial
    result["state"] = "regressed" if new_failure else "incomplete" if partial else "no_new_failures"
    return result
