"""Build and run the maintained actual Windows standalone Player assurance profile."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

from check_play_scenario_player_evidence import check, check_case
from player_e2e_artifacts import digest, inventory, read_json, verify_bundle, write_json
from player_e2e_process import Invocation, execute
from player_e2e_budget import run_budget_checks
from player_e2e_iterations import iteration_command
from player_e2e_soak import CASES as SOAK_CASES, soak_command
from player_e2e_imports import (
    CASES as IMPORT_CASES,
    ImportEditor,
    ImportE2EError,
    execute_with_imports,
)

from player_e2e_profiles import PROFILE_NAMES, PlayerProfile, get_profile, require_profile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server/src"))
MANIFEST = Path(__file__).with_name("unity-player-e2e.json")
BUILDER = "MCPForUnityTests.PlayScenarios.Player.ScenarioPlayerFixtureBuild."


def initialize(output: Path, profile_name: str = "standard") -> str:
    """Initialize only a new evidence root, refusing stale cached evidence."""
    profile = get_profile(profile_name)
    output.mkdir(parents=True, exist_ok=False)
    nonce = uuid4().hex
    write_json(
        output / "session-start.json",
        {
            "session_id": nonce,
            "started_unix_ms": int(time.time() * 1000),
            "profile": profile.evidence(),
        },
    )
    return nonce


def build_players(args: argparse.Namespace) -> tuple[Path, Path]:
    """Build only in an explicitly prepared isolated project, preserving source projects."""
    project = args.project.resolve(strict=True)
    if not any(project.is_relative_to(ROOT / folder) for folder in (".tmp", ".unity-ci")):
        raise ValueError("Fixture project must be within owned .tmp or .unity-ci scratch")
    builds = (args.output / "builds/scenario", args.output / "builds/ordinary")
    for kind, method, target in (
        ("scenario", "BuildFromCommandLine", builds[0]),
        ("ordinary", "BuildOrdinaryFromCommandLine", builds[1]),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(args.editor.resolve(strict=True)),
            "-batchmode",
            "-nographics",
            "-quit",
            "-projectPath",
            str(project),
            "-logFile",
            "-",
            "-executeMethod",
            BUILDER + method,
            "--mcp-scenario-output",
            str(target.resolve()),
            "--mcp-fixture-prepare-baseline",
            "--mcp-fixture-click-mode",
            "raycast",
            "--mcp-scenario-source-revision",
            args.source_revision,
        ]
        if kind == "scenario":
            command.extend(("--mcp-fixture-metrics", "--mcp-scenario-backend", args.backend))
        process = execute(Invocation(command, args.output / "build-evidence" / kind, timeout=1800))
        if process["actual_exit_code"] or process["forced_termination"]:
            raise ValueError("Actual Unity fixture build failed: " + kind)
        write_json(
            args.output / "build-evidence" / kind / "receipt.json",
            {
                "completed_unix_ms": int(time.time() * 1000),
                "native_build_completed": True,
                "build_log_sha256": digest(args.output / "build-evidence" / kind / "process.log"),
            },
        )
    return builds


def cli_command(bundle_root: Path, directory: Path, revision: str) -> list[str]:
    """Invoke the repository's actual CLI entry point under the selected existing environment."""
    return [
        sys.executable,
        "-m",
        "cli.main",
        "--format",
        "json",
        "play-scenario",
        "player-run",
        str(bundle_root),
        "--output-dir",
        str(directory / "cli-output"),
        "--repeat-count",
        "2",
        "--timeout-seconds",
        "30",
        "--cleanup-wait-seconds",
        "10",
        "--expected-build-revision",
        revision,
        "--require-verified-payload",
    ]


def run_case(
    args: argparse.Namespace,
    case: str,
    bundle: dict,
    builds: tuple[Path, Path],
    remaining_seconds: float | None = None,
) -> None:
    """Write a case receipt only after real process exit and all body evidence assertions."""
    directory = args.output / "cases" / case
    directory.mkdir(parents=True, exist_ok=False)
    job = uuid4().hex
    request = {
        "schema_version": 2,
        "job_id": job,
        "scenario_name": bundle["scenario_name"],
        "definition_hash": bundle["definition_hash"],
        "build_id": bundle["build_id"],
        "payload_hash": bundle["payload_hash"],
        "build_source_revision": bundle["build_source_revision"],
        "repeat_count": 2 if case == "success" else 1,
        "timeout_seconds": 30,
        "source_revision": "player-assurance-" + args.session_id,
    }
    environment = {"PYTHONPATH": str(ROOT / "Server/src")}
    command = [
        str(builds[0] / bundle["executable"]),
        "-batchmode",
        "-screen-width",
        "640",
        "-screen-height",
        "480",
        "-logFile",
        "-",
        "--mcp-scenario-request",
        str(directory / "request.json"),
        "--mcp-fixture-mode",
        case,
    ]
    timeout = 75
    if case == "wrong-build":
        request["build_id"] = uuid4().hex
    if case != "missing-request":
        write_json(directory / "request.json", request)
    (directory / "definition.json").write_text(bundle["definition_json"], encoding="utf-8")
    if case == "stale-output":
        write_json(directory / "run.json", {"stale_witness": True, "job_id": "0" * 32})
    if case == "startup-progress-write-failure":
        command = [
            sys.executable,
            str(Path(__file__).with_name("player_e2e_startup.py")),
            str(builds[0]),
            str(directory / "cli-output"),
            args.source_revision,
        ]
        # The real CLI owns the sole three-repeat native request for this case.
        (directory / "request.json").unlink()
    if case == "ordinary":
        command = [
            str(builds[1] / "MCPOrdinaryPlayer.exe"),
            "-batchmode",
            "-logFile",
            "-",
            "--mcp-scenario-request",
            str(directory / "request.json"),
            "--mcp-fixture-witness",
            str(directory / "ordinary.json"),
            "--mcp-fixture-session",
            args.session_id,
        ]
    if case in ("cli-repeat", "tampered-payload"):
        selected = builds[0]
        if case == "tampered-payload":
            selected = args.output / "tampered-build"
            shutil.copytree(builds[0], selected)
            # Change a non-executable payload file to prove whole-payload admission.
            entry = next(
                item for item in bundle["payload_inventory"] if item["path"] != bundle["executable"]
            )
            with (selected / entry["path"]).open("ab") as stream:
                stream.write(b"owned-assurance-tamper")
        command = cli_command(selected, directory, args.source_revision)
    if case in ("cli-session", "session-stop", "session-continue"):
        policy = "continue" if case == "session-continue" else "stop"
        mode = "compare" if case == "cli-session" else "fresh-process"
        command = [
            sys.executable,
            "-m",
            "cli.main",
            "--format",
            "json",
            "play-scenario",
            "player-session",
            str(builds[0]),
            "--output-dir",
            str(directory / "cli-output"),
            "--mode",
            mode,
            "--iterations",
            "2",
            "--batch-size",
            "2",
            "--max-runtime-seconds",
            "120",
            "--timeout-seconds",
            "30",
            "--cleanup-wait-seconds",
            "10",
            "--interval-seconds",
            "0",
            "--failure-policy",
            policy,
            "--retain-reports",
            "2" if case == "cli-session" else "1",
            "--expected-build-revision",
            args.source_revision,
        ]
        timeout = 150
        if case != "cli-session":
            environment["MCP_SCENARIO_FIXTURE_MODE"] = "error"
    cancel_iteration = None
    observe_cli = False
    if case in SOAK_CASES:
        command, fixture_mode = soak_command(case, builds[0], directory, args.source_revision)
        environment["MCP_SCENARIO_FIXTURE_MODE"] = fixture_mode
        (directory / "request.json").unlink()
        timeout = SOAK_CASES[case][3] + 45
        cancel_iteration = 13 if case in {"soak-cancel", "soak-import-cancel"} else None
        observe_cli = True
    if case.startswith("iteration-"):
        command, fixture_mode, cancel_iteration = iteration_command(
            case, builds[0], directory, args.source_revision
        )
        environment["MCP_SCENARIO_FIXTURE_MODE"] = fixture_mode
        # The actual CLI creates its own attributed native request.
        (directory / "request.json").unlink()
    if remaining_seconds is not None:
        timeout = min(timeout, remaining_seconds)
    invocation = Invocation(
        command,
        directory,
        timeout=timeout,
        cancel_on_progress=case == "cancel" or cancel_iteration is not None,
        cancel_iteration=cancel_iteration,
        deadline_after_progress=3 if case == "hang" else None,
        environment=environment,
        observe_cli=observe_cli,
    )
    if case in IMPORT_CASES:
        execute_with_imports(invocation, ImportEditor(args.editor, args.project))
    else:
        execute(invocation)
    receipt = {
        "schema_version": 1,
        "session_id": args.session_id,
        "case": case,
        "completed_unix_ms": int(time.time() * 1000),
        "artifact_hashes": {},
    }
    receipt["artifact_hashes"] = {entry["path"]: entry["sha256"] for entry in inventory(directory)}
    check_case(args.output, case, bundle, receipt)
    write_json(directory / "receipt.json", receipt)
    print(f"Player case passed: {case}", flush=True)


def run_profile(
    args: argparse.Namespace, profile: PlayerProfile, bundle: dict, builds: tuple[Path, Path]
) -> bool:
    """Execute the frozen case set and budget gates within one recorded deadline."""
    failures = []
    execution_start = time.monotonic()
    deadline = execution_start + profile.max_execution_seconds
    execution_started_unix_ms = int(time.time() * 1000)
    for case in profile.cases:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.append({"case": case, "error": "Profile execution deadline expired"})
            break
        try:
            run_case(args, case, bundle, builds, remaining)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            failures.append({"case": case, "error": str(exc)})
            print(f"Player case failed: {case}: {exc}", flush=True)
    if not failures:
        try:
            run_budget_checks(args.output, args.session_id, deadline=deadline)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            failures.append({"case": "budget-checks", "error": str(exc)})
            print(f"Player budget checks failed: {exc}", flush=True)
    elapsed = time.monotonic() - execution_start
    if elapsed > profile.max_execution_seconds:
        failures.append({"case": None, "error": "Profile execution deadline expired"})
    write_json(
        args.output / "results.json",
        {
            "session_id": args.session_id,
            "profile": profile.evidence(),
            "execution_started_unix_ms": execution_started_unix_ms,
            "execution_finished_unix_ms": int(time.time() * 1000),
            "execution_elapsed_seconds": elapsed,
            "failures": failures,
        },
    )
    return not failures


def main() -> int:
    """Reusable local/CI surface; provision and activation remain separate explicit operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--source-revision")
    parser.add_argument("--editor", "--unity", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--ordinary-build", type=Path)
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="standard")
    parser.add_argument("--backend", choices=("mono", "il2cpp"), default="mono")
    args = parser.parse_args()
    args.output = args.output.resolve()
    try:
        if args.initialize:
            print(initialize(args.output, args.profile))
            return 0
        profile = get_profile(args.profile)
        start = read_json(args.output / "session-start.json", 16384)
        require_profile(start, profile)
        if (
            not args.source_revision
            or len(args.source_revision) > 128
            or start["session_id"] != args.session_id
        ):
            raise ValueError(
                "Execution requires the freshly initialized session and trusted build revision"
            )
        if any(path.name != "session-start.json" for path in args.output.iterdir()):
            raise ValueError("Execution root must contain only its fresh initializer")
        if bool(args.build) != bool(args.ordinary_build) or (
            not args.build and (not args.editor or not args.project)
        ):
            raise ValueError("Supply both prebuilt roots or an Editor and isolated project")
        if not args.editor or not args.project:
            raise ImportE2EError(
                "Concurrent imports require --editor and --project, including prebuilt runs"
            )
        ImportEditor(args.editor, args.project).validate()
        builds = (
            (args.build.resolve(), args.ordinary_build.resolve())
            if args.build and args.ordinary_build
            else build_players(args)
        )
        if args.output.is_relative_to(builds[0]) or args.output.is_relative_to(builds[1]):
            raise ValueError("Execution outputs must be external to both immutable builds")
        bundle = verify_bundle(builds[0], args.source_revision)
        if (
            bundle.get("scripting_backend", "mono") != args.backend
            or bundle.get("build_target", "windows64") != "windows64"
        ):
            raise ValueError(
                "Provided Player backend/target differs from requested Windows assurance"
            )
        write_json(
            args.output / ".session.json",
            {
                **start,
                "build_source_revision": args.source_revision,
                "build_id": bundle["build_id"],
                "payload_hash": bundle["payload_hash"],
                "scripting_backend": args.backend,
                "build_target": "windows64",
                "build_mode": "native" if not args.build else "provided",
            },
        )
        write_json(args.output / "ordinary-inventory.json", {"files": inventory(builds[1])})
        if not run_profile(args, profile, bundle, builds):
            return 1
        check(args.output, args.session_id, builds, args.profile, args.backend)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"Player assurance failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
