"""Match public verification reports to separately retained native Player evidence."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .play_scenario_analysis_io import AnalysisError, read_json

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
        raise AnalysisError(f"Missing or invalid {field}")
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


def _state(value, field):
    if not isinstance(value, str) or value not in _TERMINAL:
        raise ValueError(f"Invalid {field} state")
    return value


def _check_key(row):
    return _name(row.get("kind"), "check kind"), _name(row.get("id"), "check id")


def _indexed_checks(report, field):
    checks = {}
    for row in _rows(report, field):
        key = _check_key(row)
        if key in checks:
            raise ValueError(f"Duplicate check in {field}")
        checks[key] = row
    return checks


def _job_ids(data):
    ids = data.get("child_job_ids", [])
    if not isinstance(ids, list):
        raise AnalysisError("Invalid selected child job IDs")
    ids = [_name(job, "selected child job ID") for job in ids]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate selected child job ID")
    return ids


def _editor_receipt(key, row, execution, owned, source):
    kind, name = key
    data = row["evidence"]
    digest, job = row.get("definition_hash"), row.get("job_id")
    if (
        data.get("target") != "editor"
        or not isinstance(data.get("source_token"), str)
        or not data["source_token"]
        or data.get("candidate_source_revision") != source
        or not isinstance(digest, str)
        or not _HEX.fullmatch(digest)
        or data.get("definition_hash") != digest
        or execution.get("definition_hash") != digest
        or execution.get("job_id") != job
        or execution.get("status") != (data.get("status") or row["state"])
    ):
        return []
    if job is not None:
        _name(job, "selected job ID")
    ids = _job_ids(data)
    if kind == "scenario":
        ids = [job] if job else []
    elif kind == "test":
        if not job or ids != [job] or data.get("mode") not in ("EditMode", "PlayMode"):
            return []
        leaf = data.get("test_result")
        summary = _object(data.get("summary", {}), "test summary")
        if (
            not isinstance(leaf, dict)
            or leaf.get("job_id") != job
            or leaf.get("full_name") != name
            or leaf.get("state") != ("Passed" if row["state"] == "passed" else "Failed")
            or type(summary.get("total")) is not int
            or summary["total"] != 1
            or type(summary.get(row["state"])) is not int
            or summary[row["state"]] != 1
        ):
            return []
    elif kind == "profile":
        if _job_ids(execution) != ids or (job and job not in ids):
            return []
    else:
        return []
    kinds = {
        "test": ("tests",),
        "scenario": ("editor_scenario",),
        "profile": ("analysis", "tests", "compile", "import"),
    }
    tagged = [c for c in owned.values() if (c.get("check_kind"), c.get("check_id")) == key]
    if set(ids) != {child["job_id"] for child in tagged}:
        return []
    for child in tagged:
        if (
            child.get("kind") not in kinds[kind]
            or child.get("definition_hash") != digest
            or child.get("settled") is not True
            or child.get("dispatch") != "acknowledged"
        ):
            return []
        if kind == "scenario" and (
            child.get("name") != name
            or child.get("status") != data.get("status")
            or data.get("runner_resources_released") is not True
            or data.get("status")
            not in (("succeeded",) if row["state"] == "passed" else ("failed", "timed_out"))
        ):
            return []
    if kind == "profile" and not any(child["kind"] in ("tests", "analysis") for child in tagged):
        return []
    return ids


def _stages(report):
    stages = {}
    for row in _rows(report, "stages"):
        key = _name(row.get("name"), "stage name")
        if key in stages:
            raise ValueError("Duplicate verification stage")
        _state(row.get("state"), "verification stage")
        stages[key] = row
    return stages


def _source_current(report, comparison, stages):
    if stages.get("final_source", {}).get("state") != "passed" or report.get("source_mode") not in (
        "current",
        "frozen",
    ):
        return False
    if (
        report.get("source_mode") == "current"
        and comparison.get("live_matches_candidate") is not True
    ):
        return False
    for inventory in (
        comparison.get("input_inventory", {}),
        report.get("latest_input_inventory", {}),
    ):
        for record in _object(inventory, "input inventory").values():
            if _object(record, "input chunk inventory").get("complete") is not True:
                return False
    return True


@dataclass
class CapturedRun:
    run_id: str
    job_id: str
    build_id: str
    payload: str
    source: str
    manifest: str
    baseline_manifest: str | None
    baseline_source: str | None
    conditions: dict
    checks: dict
    state: str
    scenario: str
    selection_gaps: list
    inputs_confirmed: bool

    def observed_jobs(self):
        jobs = {self.job_id}
        for check in self.checks.values():
            jobs.update(check.get("child_job_ids", []))
        return jobs

    def identity(self):
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "build_id": self.build_id,
            "payload_hash": self.payload,
            "source_revision": self.source,
        }


def read_run(report_path: Path, native_path: Path) -> CapturedRun:
    report = read_json(report_path)
    if report.get("success") is True:
        report = _object(report.get("data"), "verification response data")
    native = read_json(native_path)
    if report.get("schema") != "verification_report@1":
        raise ValueError("Expected a terminal verification report")
    parent_state = _state(report.get("state"), "verification report")
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
    status = _name(native.get("status"), "Player status")
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
    owned = {}
    for child in _rows(report, "children"):
        if child.get("job_id") is None:
            continue
        child_id = _name(child["job_id"], "child job ID")
        if child_id in owned:
            raise ValueError("Duplicate child job ID")
        owned[child_id] = child
    player = owned.get(job, {})
    if player.get("kind") != "standalone_player" or player.get("settled") is not True:
        raise ValueError("Player job is not a settled child of this verification")
    executed = _indexed_checks(report, "executed_checks")
    primary = ("scenario", scenario)
    witness = executed.get(primary, {})
    if witness.get("job_id") != job or witness.get("status") != status:
        raise ValueError("Missing native scenario execution in verification report")
    state = player_stage["state"]
    if (
        type(native.get("exit_code")) is not int
        or type(evidence.get("exit_code")) is not int
        or native["exit_code"] != evidence["exit_code"]
    ):
        raise ValueError("Recorded process exit disagrees with Player evidence")
    if state == "passed" and (status != "succeeded" or evidence.get("exit_code") != 0):
        raise ValueError("Passing Player stage contradicts its native outcome")
    if state == "failed" and (status not in {"failed", "timed_out"} or native["exit_code"] == 0):
        raise ValueError("Failed Player stage contradicts its native outcome")
    native_unreliable = bool(
        native.get("report_error") or native.get("progress_error") or evidence.get("client_error")
    )
    installation = _object(report.get("installation"), "installation")
    environment = _object(reproduction.get("environment"), "Player environment")
    installed_unity = _name(installation.get("unity_version"), "installed Unity version")
    if reproduction.get("unity_version") != installed_unity:
        raise ValueError("Editor and Player Unity versions differ")
    conditions = {key: _name(environment.get(key), key) for key in _ENV}
    for field in ("repeat_count", "timeout_seconds"):
        if type(native.get(field)) is not int or native[field] < 1:
            raise ValueError(f"Invalid Player {field}")
    conditions.update(
        project=_name(report.get("project_scope"), "project scope"),
        unity_version=installed_unity,
        package_version=_name(reproduction.get("package_version"), "package version"),
        definition_hash=_digest(reproduction.get("definition_hash"), "scenario definition hash"),
        repeat_count=native.get("repeat_count"),
        timeout_seconds=native["timeout_seconds"],
        scenario=scenario,
        replay_seed=reproduction.get("replay_seed"),
        start_delay_frames=reproduction.get("start_delay_frames"),
    )
    observed = stages.get("observed_state", {})
    observation = _object(observed.get("evidence", {}), "observation evidence")
    signature = _object(
        native.get("failure") if native.get("failure") is not None else {}, "native failure"
    )
    signature = {
        k: signature[k]
        for k in (
            "code",
            "stage",
            "iteration",
            "step_index",
            "target",
            "component",
            "property_path",
            "expected",
            "actual",
        )
        if k in signature
    }
    checks = {
        primary: {
            "state": state,
            "target": "standalone_player",
            "job_id": job,
            "signature": signature,
        }
    }
    if observed:
        if observed["state"] in _OBSERVED:
            expected = _object(observation.get("expected"), "expected observation")
            actual = _object(observation.get("observed"), "actual observation")
            if not expected or same_json(expected, actual) != (observed["state"] == "passed"):
                raise ValueError("State observation contradicts its recorded outcome")
        checks[("observation", scenario)] = {
            "state": observed.get("state"),
            "target": "standalone_player",
            "expected_state": observation.get("expected"),
            "signature": observation.get("observed") if observed.get("state") == "failed" else None,
        }
    selection = _object(comparison.get("selected_checks"), "candidate selection")
    declared = _indexed_checks(selection, "checks")
    for key, row in _indexed_checks(report, "selected_check_results").items():
        data = _object(row.get("evidence"), "selected check evidence")
        if key == primary:
            if (
                row.get("job_id") != job
                or row.get("state") != state
                or data.get("target") != "standalone_player"
                or data.get("status") != status
                or any(
                    item["definition_hash"] != conditions["definition_hash"]
                    for item in (row, data, witness, player)
                    if "definition_hash" in item
                )
            ):
                raise ValueError("Selected Player check contradicts native evidence")
            continue
        if key[0] not in ("scenario", "test", "profile"):
            raise ValueError("Unknown selected check kind")
        result = _state(row.get("state"), "selected check")
        target = data.get("target")
        ids = []
        if result in _OBSERVED:
            if key in declared:
                ids = _editor_receipt(key, row, executed.get(key, {}), owned, source)
            if not ids:
                result = "incomplete"
        checks[key] = {
            "state": result,
            "target": target,
            "mode": data.get("mode"),
            "definition_hash": data.get("definition_hash"),
            "child_job_ids": ids,
        }
    for key in declared:
        checks.setdefault(key, {"state": "not_run"})
    for key in _indexed_checks(report, "additional_checks_outstanding"):
        if checks.get(key, {}).get("state") in _OBSERVED:
            raise ValueError("Observed check also marked outstanding")
        checks.setdefault(key, {"state": "not_run"})
    gaps = selection.get("outstanding", [])
    if not isinstance(gaps, list):
        raise AnalysisError("Invalid outstanding selection reasons")
    gaps = [_name(gap, "selection gap") for gap in gaps]
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
        payload,
        source,
        _digest(comparison.get("manifest_sha256"), "candidate manifest"),
        comparison.get("baseline_manifest_sha256"),
        comparison.get("baseline_source_revision"),
        conditions,
        checks,
        parent_state,
        scenario,
        gaps,
        _source_current(report, comparison, stages)
        and not native_unreliable
        and parent_state not in ("stale", "canceled")
        and "interruption" not in stages,
    )


def same_json(left, right):
    # Python treats True and 1 as equal. Native state observations distinguish their JSON types.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def compare_runs(before: CapturedRun, after: CapturedRun):
    if before.run_id == after.run_id or before.observed_jobs() & after.observed_jobs():
        raise ValueError("Reused verification or native job evidence")
    if before.build_id == after.build_id and (before.source, before.payload) != (
        after.source,
        after.payload,
    ):
        raise ValueError("One Player build claims different source revisions or payloads")
    if (after.baseline_manifest, after.baseline_source) != (before.manifest, before.source):
        raise ValueError("Candidate comparison did not capture this baseline")
    changed = sorted(
        k
        for k in before.conditions.keys() | after.conditions.keys()
        if not same_json(before.conditions.get(k), after.conditions.get(k))
    )
    result = {
        "schema": "verification_change@1",
        "baseline": before.identity(),
        "candidate": after.identity(),
        "different_conditions": changed,
        "outstanding_selection_reasons": after.selection_gaps,
        "checks": [],
    }
    if changed:
        return {**result, "state": "not_comparable"}
    new_failure = False
    source_blocked = not before.inputs_confirmed or not after.inputs_confirmed
    partial = (
        source_blocked
        or before.state not in _OBSERVED
        or after.state not in _OBSERVED
        or bool(after.selection_gaps)
    )
    for kind, key in sorted(before.checks.keys() | after.checks.keys()):
        old = before.checks.get((kind, key), {"state": "not_run"})
        new = after.checks.get((kind, key), {"state": "not_run"})
        expected_changed = (
            kind == "observation"
            and old.get("expected_state") is not None
            and new.get("expected_state") is not None
            and not same_json(old["expected_state"], new["expected_state"])
        )
        if expected_changed:
            result["different_conditions"].append("expected_state")
        if source_blocked or old["state"] not in _OBSERVED or new["state"] not in _OBSERVED:
            status = "not_run" if "not_run" in (old["state"], new["state"]) else "incomplete"
            partial = True
        elif expected_changed or any(
            old.get(field) != new.get(field) for field in ("target", "mode", "definition_hash")
        ):
            status = "not_comparable"
            partial = True
        elif old["state"] == new["state"] == "failed":
            if (
                old.get("signature") is None
                or new.get("signature") is None
                or (kind == "scenario" and (not old["signature"] or not new["signature"]))
            ):
                status = "incomplete"
                partial = True
            else:
                status = (
                    "changed_failure"
                    if not same_json(old.get("signature"), new.get("signature"))
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
