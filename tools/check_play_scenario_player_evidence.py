"""Independently verify fresh Windows Mono Player assurance artifacts."""

import argparse
import json
from pathlib import Path
import re

from player_e2e_artifacts import contained, digest, inventory, read_json, verify_bundle
from player_e2e_cli_evidence import check_cli
from player_e2e_iterations import check_iteration_case
from player_e2e_reports import require, check_report, check_progress, check_preflight

MANIFEST = Path(__file__).with_name("unity-player-e2e.json")


def check_case(root: Path, case: str, bundle: dict, receipt: dict) -> None:
    """Check a receipt's underlying files; receipt status alone proves nothing."""
    expected = read_json(MANIFEST)["cases"][case]
    directory = contained(root, "cases/" + case)
    process = read_json(directory / "launch.json")
    require(
        process.get("process_ended") is True
        and not process.get("log_truncated")
        and not process.get("progress_read_errors"),
        "Owned invocation is unfinished or its log was truncated",
    )
    require(
        receipt.get("completed_unix_ms", 0) >= process["finished_unix_ms"],
        "Receipt precedes body completion",
    )
    hashes = receipt.get("artifact_hashes")
    require(isinstance(hashes, dict) and hashes, "Receipt lacks underlying artifact hashes")
    observed_hashes = {
        entry["path"]: entry["sha256"]
        for entry in inventory(directory)
        if entry["path"] != "receipt.json"
    }
    require(hashes == observed_hashes, "Receipt does not cover every underlying artifact")
    for relative, expected_hash in hashes.items():
        require(digest(contained(directory, relative)) == expected_hash, "Artifact hash mismatch")
    if case.startswith("iteration-"):
        check_iteration_case(directory, bundle, process, case)
        return
    if expected.startswith("cli-") or expected.startswith("session-"):
        check_cli(directory, bundle, process, expected)
        return
    match expected:
        case "succeeded" | "failed" | "timed_out" | "cancelled":
            report = check_report(directory, bundle, process)
            require(report["status"] == expected, "Unexpected native outcome")
            failure_code = read_json(MANIFEST).get("failure_codes", {}).get(case)
            if failure_code:
                require(
                    report.get("failure", {}).get("code") == failure_code,
                    "Readonly provider failure evidence differs",
                )
            check_progress(
                process["progress_observations"],
                read_json(directory / "request.json"),
                process["process_id"],
            )
            if case == "success":
                updates = [
                    entry
                    for entry in process["progress_observations"]
                    if entry["main_loop_sequence"] > 0
                ]
                require(
                    len(updates) >= 2,
                    "Repeated positive body lacks multiple atomic Update publications",
                )
            if expected == "cancelled":
                require(
                    process.get("cancel_requested") is True, "Cancellation body was not requested"
                )
        case "startup-rejected":
            require(
                process["actual_exit_code"] == 2 and not process["forced_termination"],
                "Invalid startup did not reject naturally",
            )
            require(not process["progress_observations"], "Invalid startup ran its main loop")
            log = (directory / "process.log").read_text(encoding="utf-8", errors="replace")
            require(
                "Player fixture click:" not in log and "Player fixture reset:" not in log,
                "Invalid startup performed fixture side effects",
            )
            if case == "wrong-build":
                check_preflight(directory, bundle, process)
            elif case == "stale-output":
                require(
                    read_json(directory / "run.json").get("stale_witness") is True,
                    "Stale output was replaced",
                )
            else:
                require(not (directory / "run.json").exists(), "Rejected startup produced a report")
        case "owned-timeout":
            require(
                process["forced_termination"] is True
                and process.get("live_deadline_armed") is True
                and process["actual_exit_code"] != 0
                and not (directory / "run.json").exists(),
                "Hung Player was not truthfully terminated",
            )
            check_progress(
                process["progress_observations"],
                read_json(directory / "request.json"),
                process["process_id"],
            )
            log = (directory / "process.log").read_text(encoding="utf-8", errors="replace")
            require(
                "PLAYER_FIXTURE_AFTER_LIVE_UPDATES:hang" in log,
                "Owned timeout preceded the fixture's actual main-loop hang",
            )
        case "missing-final":
            require(
                process["actual_exit_code"] != 0 and not (directory / "run.json").exists(),
                "Crash was misreported as finalized success",
            )
            check_progress(
                process["progress_observations"],
                read_json(directory / "request.json"),
                process["process_id"],
            )
        case "boundary-verified":
            witness = read_json(directory / "ordinary.json", 16384)
            require(
                process["actual_exit_code"] == 0
                and not process["forced_termination"]
                and not (directory / "run.json").exists(),
                "Ordinary process boundary failed",
            )
            require(
                witness.get("session_id") == receipt["session_id"]
                and witness.get("process_id") == process["process_id"]
                and process["started_unix_ms"]
                <= witness.get("completed_unix_ms", 0)
                <= process["finished_unix_ms"],
                "Ordinary body witness is stale",
            )
            for key in (
                "runner_absent",
                "input_backend_absent",
                "tracker_disabled",
                "registration_disposal_verified",
            ):
                require(witness.get(key) is True, "Ordinary body assertion missing: " + key)
        case "admission-rejected":
            require(
                process["actual_exit_code"] != 0 and not process["forced_termination"],
                "Tampered bundle was admitted",
            )
            require(
                not list(directory.glob("cli-output/*/request.json")),
                "Tampered bundle launched a child",
            )
        case _:
            raise ValueError("Unknown expected case outcome")


def check(root: Path, session_id: str, builds: tuple[Path, Path]) -> None:
    """Require every manifest case, matching fresh session, and unchanged full payloads."""
    session = read_json(root / ".session.json", 16384)
    require(
        session.get("session_id") == session_id and re.fullmatch(r"[0-9a-f]{32}", session_id),
        "Required evidence session mismatch",
    )
    bundle = verify_bundle(builds[0], session["build_source_revision"])
    require(
        session.get("build_id") == bundle.get("build_id")
        and session.get("payload_hash") == bundle.get("payload_hash"),
        "Evidence references a different build",
    )
    ordinary = read_json(root / "ordinary-inventory.json")
    require(ordinary.get("files") == inventory(builds[1]), "Ordinary payload changed")
    if session.get("build_mode") == "native":
        for kind in ("scenario", "ordinary"):
            directory = contained(root, "build-evidence/" + kind)
            process = read_json(directory / "launch.json")
            receipt = read_json(directory / "receipt.json")
            require(
                process["started_unix_ms"] >= session["started_unix_ms"],
                "Native build receipt is stale",
            )
            require(
                process.get("actual_exit_code") == 0
                and process.get("process_ended") is True
                and process.get("forced_termination") is False
                and not process.get("log_truncated"),
                "Actual native build did not complete",
            )
            require(
                receipt.get("native_build_completed") is True
                and receipt.get("completed_unix_ms", 0) >= process["finished_unix_ms"]
                and receipt.get("build_log_sha256") == digest(directory / "process.log"),
                "Native build body receipt mismatch",
            )
    else:
        require(session.get("build_mode") == "provided", "Unknown build evidence mode")
    cases = read_json(MANIFEST)["cases"]
    discovered = set()
    for path in (root / "cases").iterdir():
        discovered.add(path.name)
        require(len(discovered) <= len(cases), "Required case count exceeds manifest")
    require(
        discovered == set(cases),
        "Missing or unknown required case",
    )
    job_ids = set()
    for case in cases:
        directory = contained(root, "cases/" + case)
        receipt = read_json(directory / "receipt.json", 65536)
        require(
            receipt.get("session_id") == session_id and receipt.get("case") == case,
            "Stale or mismatched body receipt",
        )
        require(
            receipt.get("completed_unix_ms", 0) >= session["started_unix_ms"], "Stale body receipt"
        )
        check_case(root, case, bundle, receipt)
        request_path = directory / "request.json"
        if request_path.exists():
            job = read_json(request_path).get("job_id")
            require(job not in job_ids, "Required cases reused a job identity")
            job_ids.add(job)


def main() -> int:
    """Command-line gate for always-run CI evidence verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--ordinary-build", type=Path)
    args = parser.parse_args()
    try:
        check(
            args.root,
            args.session_id,
            (
                args.build or args.root / "builds/scenario",
                args.ordinary_build or args.root / "builds/ordinary",
            ),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Player evidence rejected: {exc}")
        return 1
    print("All required fresh Windows Mono Player cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
