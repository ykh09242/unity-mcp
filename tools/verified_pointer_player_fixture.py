"""Build and verify the Windows standalone pointer controls in the prepared fixture."""

import argparse
from pathlib import Path
from shutil import copyfile
import subprocess
import sys
from uuid import uuid4

from player_e2e_artifacts import contained, digest, read_json, verify_bundle, write_json
from player_e2e_process import Invocation, execute
from player_e2e_reports import check_report, require
from verified_pointer_fixture import ROOT, VERSION

SOURCE = ROOT / "TestProjects/UnityMCPTests/Assets/Scripts/ScenarioPlayerFixture"
PLAYER_FILES = (
    "MCPForUnity.ScenarioPlayerFixture.asmdef",
    "VerifiedPointerPlayerProbe.cs",
    "Editor/MCPForUnity.ScenarioPlayerFixture.Editor.asmdef",
    "Editor/VerifiedPointerPlayerBuild.cs",
)


def build_player(fixture: Path, output: Path, editor: Path, checkout: str) -> tuple[Path, str]:
    project = fixture / "project"
    target = project / "Assets/PointerPlayer"
    (target / "Editor").mkdir(parents=True, exist_ok=False)
    for name in PLAYER_FILES:
        for suffix in ("", ".meta"):
            copyfile(SOURCE / (name + suffix), target / (name + suffix))
    source = {name: digest(target / name) for name in PLAYER_FILES}
    host = ROOT / "MCPForUnity/Runtime/PlayScenarios/PlayScenarioPlayerHost.cs"
    source["PlayScenarioPlayerHost.cs"] = digest(host)
    inputs = output / "build-inputs.json"
    write_json(inputs, {"checkout": checkout, "source_sha256": source})
    bundle = output / "build"
    revision = checkout + "+pointer-" + digest(inputs)[:16]
    command = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(project),
        "-job-worker-count",
        "1",
        "-executeMethod",
        "MCPForUnityTests.PlayScenarios.Player.VerifiedPointerPlayerBuild.BuildFromCommandLine",
        "--mcp-pointer-output",
        str(bundle),
        "--mcp-pointer-revision",
        revision,
        "-logFile",
        "-",
    ]
    process = execute(Invocation(command, output / "build-process", timeout=600))
    require(
        process["actual_exit_code"] == 0 and not process["forced_termination"],
        "Native build did not complete. Inspect build-process/launch.json and process.log.",
    )
    require(
        digest(host) == source["PlayScenarioPlayerHost.cs"],
        "The external Player host source changed during build",
    )
    return bundle, revision


def run_case(
    bundle_path: Path, revision: str, output: Path, control: str, viewport: list[int]
) -> dict:
    bundle = verify_bundle(bundle_path, revision)
    case = output / control
    case.mkdir()
    request = {
        "schema_version": 2,
        **{
            key: bundle[key]
            for key in (
                "scenario_name",
                "definition_hash",
                "build_id",
                "payload_hash",
                "build_source_revision",
            )
        },
        "job_id": uuid4().hex,
        "repeat_count": 1,
        "timeout_seconds": 30,
    }
    write_json(case / "request.json", request)
    player = contained(bundle_path, bundle["executable"])
    command = [
        str(player),
        "-batchmode",
        "-screen-width",
        str(viewport[0]),
        "-screen-height",
        str(viewport[1]),
        "-logFile",
        "-",
        "--mcp-scenario-request",
        str(case / "request.json"),
    ]
    process = execute(
        Invocation(command, case, timeout=60, environment={"MCP_VERIFIED_POINTER_CONTROL": control})
    )
    report = check_report(case, bundle, process, require_cleanup=False)
    witness = read_json(case / "verified-pointer-witness.json", 16384)
    for key in ("job_id", "definition_hash", "build_id", "payload_hash", "build_source_revision"):
        require(witness[key] == request[key], f"Independent {control} witness has mismatched {key}")
    require(
        witness["control"] == control and witness["unity_version"] == VERSION,
        "Wrong native control or version",
    )
    require(witness["graphics_device"] != "Null", "Pointer evidence requires a graphics device")
    blocked = control == "blocked"
    expected_hits = (
        ["VerifiedPointerOverlay", "VerifiedPointerButton"]
        if blocked
        else ["VerifiedPointerButton"]
    )
    initial = witness["initial"]
    final = witness["final"]
    for frame in (initial, final):
        require(
            frame["viewport"] == viewport
            and frame["screen_position"] == initial["screen_position"]
            and frame["hits"] == expected_hits
            and frame["overlay_alpha"] == 0
            and frame["overlay_blocks_raycasts"] == blocked,
            f"Native pointer evidence differs within {control}",
        )
    require(
        initial["opened"] is False and initial["deliveries"] == 0,
        "Initial state was already changed",
    )
    require(
        final["opened"] is not blocked and final["deliveries"] == int(not blocked),
        f"Native game state and actual pointer deliveries differ in {control}",
    )
    require(
        report["status"] == ("timed_out" if blocked else "succeeded")
        and [step["status"] for step in report["steps"]]
        == (["passed", "passed", "timed_out", "skipped"] if blocked else ["passed"] * 4),
        f"Native scenario step verdict differs in {control}",
    )
    if blocked:
        failure = report.get("failure", {})
        require(
            failure.get("code") == "input_blocked"
            and "first uGUI raycast hit" in failure.get("expected", "")
            and "center is blocked" in failure.get("actual", ""),
            "Blocked Player report lacks structured routing evidence",
        )
    else:
        require(
            final["received_position"] == initial["screen_position"]
            and final["received_hit"] == expected_hits[0],
            "Pointer handler did not receive the native first-hit coordinate",
        )
    return witness


def main() -> int:
    # The existing process observer lazily imports the CLI's Player progress parser.
    sys.path.insert(0, str(ROOT / "Server/src"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", required=True, type=Path, help="Completed Editor fixture directory"
    )
    editor_source = parser.add_mutually_exclusive_group(required=True)
    editor_source.add_argument("--editor", type=Path, help=f"Unity {VERSION} Editor executable")
    editor_source.add_argument(
        "--build", type=Path, help="Existing verified pointer build within this fixture"
    )
    parser.add_argument("--expected-build-revision", help="Trusted build revision for --build")
    args = parser.parse_args()
    if (args.build is not None) != (args.expected_build_revision is not None):
        parser.error("--build and --expected-build-revision must be supplied together.")
    fixture = args.fixture.resolve(strict=True)
    require(
        fixture.is_relative_to(ROOT / ".unity-ci"),
        "Select a fixture within this checkout's .unity-ci",
    )
    read_json(fixture / "receipt.json", 16384)
    editor_pointer = read_json(fixture / "exports/verified-pointer-overlay.json")
    output = fixture / "player"
    if args.build is not None:
        bundle_path = args.build.resolve(strict=True)
        require(
            bundle_path.is_relative_to(fixture),
            "The existing build must belong to the selected fixture",
        )
        revision = args.expected_build_revision
        verify_bundle(bundle_path, revision)
        output.mkdir(exist_ok=False)
    else:
        output.mkdir(exist_ok=False)
        checkout = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        bundle_path, revision = build_player(
            fixture, output, args.editor.resolve(strict=True), checkout
        )
    bundle = verify_bundle(bundle_path, revision)
    require(
        bundle["scenario_name"] == "verified-pointer-player",
        "Select the frozen pointer scenario build",
    )
    viewport = editor_pointer["viewport"]
    blocked = run_case(bundle_path, revision, output, "blocked", viewport)
    repaired = run_case(bundle_path, revision, output, "repaired", viewport)
    require(
        blocked["initial"]["screen_position"]
        == repaired["initial"]["screen_position"]
        == editor_pointer["screen_position"],
        "Editor and both Player controls must exercise the same screen position",
    )
    write_json(
        output / "receipt.json",
        {
            "build": {
                key: bundle[key]
                for key in ("build_id", "payload_hash", "definition_hash", "build_source_revision")
            },
            "artifacts": {
                f"{control}/{name}": digest(output / control / name)
                for control in ("blocked", "repaired")
                for name in (
                    "request.json",
                    "launch.json",
                    "run.json",
                    "verified-pointer-witness.json",
                )
            },
            "screen_position": repaired["initial"]["screen_position"],
            "viewport": viewport,
        },
    )
    print(output / "receipt.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
