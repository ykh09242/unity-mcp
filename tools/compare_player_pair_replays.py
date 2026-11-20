"""Compare semantics and verify retained native evidence from independent replay jobs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from cli.utils.play_scenario_analysis_io import read_bytes, write_new
from cli.utils.play_scenario_player import PROCESS_LOG_LIMIT, REPORT_LIMIT
from services.verification.input_chunks import decode_object
from services.verification.player_candidate import StateCheck
from services.verification.player_runner import _state_result


def _receipt(directory, pin):
    report = decode_object(read_bytes(directory / "reproduction.json", 16 * 1024**2))
    if (
        report.get("kind") != "verification_player_reproduction"
        or report.get("status") != "reproduced"
        or not re.fullmatch("[0-9a-f]{64}", pin)
        or report.get("manifest_sha256") != pin
    ):
        raise ValueError("incomplete_or_unadmitted_replay")
    semantic = {
        "scenario": report["scenario"],
        "builds": report["required_builds"],
        "admissions": report["admissions"],
        "runs": {},
    }
    jobs = []
    for name in ("baseline", "candidate"):
        row = report["runs"][name]
        build = semantic["builds"][name]
        job = row["job_id"]
        if (
            not isinstance(job, str)
            or not re.fullmatch("[0-9a-f]{32}", job)
            or row["artifacts"] != f"{name}/{job}"
            or row["matched"] is not True
            or row["launcher_host"]["host_platform"] != "win32"
            or row["launcher_host"]["build_target"] != "windows64"
            or row["environment"]["platform"] != "WindowsPlayer"
            or row["status"] != build["status"]
            or any(
                row[key] != build[key]
                for key in ("build_id", "payload_hash", "source_revision", "checkpoint", "action")
            )
            or json.dumps(row["state"]["expected"], sort_keys=True)
            != json.dumps(row["state"]["observed"], sort_keys=True)
        ):
            raise ValueError("replay_lacks_matching_native_observation: " + name)
        jobs.append(job)
        native_dir = directory / name / job
        for file, limit in (
            ("run.json", REPORT_LIMIT),
            ("process.json", 65536),
            ("diagnostics.json", 65536),
            ("player.log", PROCESS_LOG_LIMIT),
        ):
            content = read_bytes(native_dir / file, limit)
            if sha256(content).hexdigest() != row["files"][file]:
                raise ValueError("retained_replay_evidence_changed: " + name + "/" + file)
            if file.endswith(".json"):
                record = decode_object(content)
                if record["job_id"] != job:
                    raise ValueError("retained_replay_job_mismatch")
                if file == "run.json":
                    steps = [
                        {key: step.get(key) for key in ("stage", "name", "action", "status")}
                        for step in record["steps"]
                    ]
                    native = record["reproduction"]
                    if (
                        steps != row["steps"]
                        or record["status"] != row["status"]
                        or record["exit_code"] != row["exit_code"]
                        or any(
                            native[k] != row[k]
                            for k in ("definition_hash", "build_id", "payload_hash")
                        )
                        or native["build_source_revision"] != row["source_revision"]
                        or native["environment"] != row["environment"]
                    ):
                        raise ValueError("retained_replay_step_or_identity_mismatch")
                if file == "process.json" and record["launch_environment"] != row["launcher_host"]:
                    raise ValueError("retained_replay_host_mismatch")
        verdict, observed = _state_result(
            native_dir, StateCheck(log_marker=row["log_marker"], expected=row["state"]["expected"])
        )
        if verdict != "passed" or json.dumps(observed, sort_keys=True) != json.dumps(
            row["state"], sort_keys=True
        ):
            raise ValueError("retained_replay_state_mismatch")
        semantic["runs"][name] = {
            key: row[key]
            for key in (
                "status",
                "exit_code",
                "checkpoint",
                "action",
                "definition_hash",
                "build_id",
                "payload_hash",
                "source_revision",
                "steps",
                "state",
                "log_marker",
            )
        }
        semantic["runs"][name]["platform"] = row["environment"]["platform"]
        semantic["runs"][name]["scripting_backend"] = row["environment"]["scripting_backend"]
    return report, semantic, jobs


def compare(first, second, pin):
    result = {
        "schema_version": 1,
        "kind": "verification_player_pair_consistency",
        "manifest_sha256": pin,
        "status": "unavailable",
    }
    try:
        a, left, jobs_a = _receipt(first, pin)
        b, right, jobs_b = _receipt(second, pin)
        if len(set(jobs_a + jobs_b)) != 4:
            raise ValueError("independent_replay_jobs_required")
        result["jobs"] = {"first": jobs_a, "second": jobs_b}
        for key in ("verifier_commit", "project_checkout_commit", "verifier_uv_lock_sha256"):
            if a[key] != b[key]:
                raise ValueError("different_replay_checkouts: " + key)
            result[key] = a[key]
        # JSON comparison preserves boolean versus integer distinctions in project state.
        if json.dumps(left, sort_keys=True) != json.dumps(right, sort_keys=True):
            result.update(status="disagreement", first=left, second=right)
        else:
            result.update(status="consistent", semantics=left)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["reason"] = str(error)[:1024]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.first, args.second, args.sha256)
    write_new(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(report["status"], report.get("reason", ""))
    return {"consistent": 0, "disagreement": 1, "unavailable": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
