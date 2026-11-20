"""Exercise the maintained prefab move in an owned project with the real Editor and Player."""

import argparse
import hashlib
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "Server/src"))

from tools.player_e2e_artifacts import contained, digest, read_json, verify_bundle, write_json
from tools.player_e2e_process import Invocation, execute
from TestProjects.UnityMCPTests.Verification.shop_workflow_observation import (
    check_observation,
    read_player_observation,
)

FIXTURE = ROOT / "TestProjects/UnityMCPTests"
SCENE = "Assets/VerifiedWorkflowShop/Shop.unity"
PROBE = "Assets/Scripts/ScenarioPlayerFixture/ShopWorkflowProbe.cs"
OLD_KEY = "VerifiedShop/UI/ShopBanner"
NEW_KEY = "VerifiedShop/Store/ShopBanner"
PHASES = ("original", "moved", "repaired")
METHOD = "MCPForUnityTests.PlayScenarios.Player.ShopWorkflowFixtureBuild.BuildFromCommandLine"


def fixture_project(path: Path) -> Path:
    project = path.resolve(strict=True)
    for parent in (ROOT / ".unity-ci", ROOT / ".tmp"):
        if project.is_relative_to(parent) and project != parent:
            contained(parent, project.relative_to(parent).as_posix())
            break
    else:
        raise ValueError("Prepare a disposable fixture project under .unity-ci or .tmp")
    if not (project / "ProjectSettings/ProjectVersion.txt").is_file():
        raise ValueError("The selected directory is not a prepared Unity project")
    if digest(contained(project, PROBE)) != digest(FIXTURE / PROBE):
        raise ValueError("The prepared project does not contain the original maintained fixture")
    return project


def confirm_inputs(project: Path, editor: dict, phase: str, session: str) -> str:
    key = OLD_KEY if phase == "original" else NEW_KEY
    prefab = f"Assets/VerifiedWorkflowShop/Resources/{key}.prefab"
    paths = sorted(path + suffix for path in (SCENE, prefab, PROBE) for suffix in ("", ".meta"))
    if (
        editor.get("session") != session
        or editor.get("phase") != phase
        or Path(editor.get("project", "")).resolve() != project
        or editor.get("scene") != SCENE
        or editor.get("asset_path") != prefab
        or not editor.get("unity_version")
        or not editor.get("package_version")
    ):
        raise ValueError("Editor observation belongs to a different fixture or session")
    current = {path: digest(contained(project, path)) for path in paths}
    revision = hashlib.sha256(
        "".join(f"{p}\n{h}\n" for p, h in current.items()).encode()
    ).hexdigest()
    if editor.get("inputs") != current or editor.get("source_revision") != revision:
        raise ValueError("Selected source files changed since the Editor observation")
    return revision


def repair(project: Path) -> None:
    script = contained(project, PROBE)
    source = (FIXTURE / PROBE).read_bytes()
    original = f'"{OLD_KEY}"'.encode()
    if script.read_bytes() != source or source.count(original) != 1:
        raise ValueError("The prepared loader changed; preserving its contents")
    script.write_bytes(source.replace(original, f'"{NEW_KEY}"'.encode()))


def retain_sources(project: Path, directory: Path, inputs: dict) -> None:
    for relative, expected in inputs.items():
        source = contained(project, relative)
        destination = contained(directory, "sources/" + relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, destination.open("xb") as writer:
            while chunk := reader.read(64 * 1024):
                writer.write(chunk)
        if digest(destination) != expected:
            raise ValueError(f"Retained input differs from the observed revision: {relative}")


def run_player(directory: Path, revision: str) -> tuple[dict, dict, Path]:
    build = directory / "build"
    bundle = verify_bundle(build, revision)
    runs = directory / "runs"
    process = execute(
        Invocation(
            [
                sys.executable,
                "-m",
                "cli.main",
                "--format",
                "json",
                "play-scenario",
                "player-run",
                str(build),
                "--output-dir",
                str(runs),
                "--expected-build-revision",
                revision,
                "--require-verified-payload",
                "--timeout-seconds",
                "45",
                "--cleanup-wait-seconds",
                "10",
            ],
            directory / "launcher",
            timeout=180,
            environment={
                "PYTHONPATH": str(ROOT / "Server/src"),
                "UNITY_MCP_PLAYER_REFERENCE_CAPTURE": "1",
            },
        )
    )
    children = list(runs.iterdir()) if runs.is_dir() else []
    if len(children) != 1 or process["forced_termination"]:
        raise ValueError(
            f"Expected one naturally terminated Player. See {directory / 'launcher/process.log'}"
        )
    job = children[0]
    run = read_json(job / "run.json")
    diagnostics = read_json(job / "diagnostics.json")
    native = read_json(job / "process.json")
    expected_exit = 0 if run["status"] == "succeeded" else 1
    if (
        process["actual_exit_code"] != expected_exit
        or run.get("execution_environment") != "player"
        or run.get("finalization_state") != "completed"
        or run.get("runner_resources_released") is not True
        or run.get("job_id") != job.name
        or diagnostics.get("job_id") != job.name
        or diagnostics.get("cleanup_state") != "native_finalized"
        or diagnostics.get("deadline_reached") is not False
        or diagnostics.get("process_exit_code") != run["exit_code"]
        or native.get("actual_exit_code") != run["exit_code"]
        or native.get("process_ended") is not True
        or native.get("forced_termination") is not False
        or any(
            run["reproduction"].get(field) != bundle.get(field)
            for field in ("build_id", "build_source_revision", "payload_hash", "definition_hash")
        )
    ):
        raise ValueError(f"Native result does not match its owned process or frozen build: {job}")
    return bundle, run, job


def verify_steps(phase: str, run: dict) -> None:
    main = [step for step in run["steps"] if step["stage"] == "main"]
    expected = [
        ("open shop", "load_scene"),
        ("confirm shop scene", "wait_scene"),
        ("show shop banner", "wait_state"),
    ]
    if [(step.get("name"), step.get("action")) for step in main] != expected:
        raise ValueError("Native report does not contain the frozen shop scenario")
    if any(step.get("status") != "passed" for step in main[:-1]):
        raise ValueError("Scene did not open before the readiness checkpoint")
    if phase == "moved":
        if run.get("status") not in {"failed", "timed_out"} or main[-1]["status"] not in {
            "failed",
            "timed_out",
        }:
            raise ValueError("The moved fixture must fail at its readiness checkpoint")
    elif run.get("status") != "succeeded" or main[-1]["status"] != "passed":
        raise ValueError("The original and repaired readiness checkpoints must pass")


def exercise(editor_exe: Path, project: Path, root: Path, session: str, reduced: bool) -> None:
    guid = None
    results = {}
    previous = None
    for phase in PHASES:
        if previous is not None:
            confirm_inputs(project, previous, previous["phase"], session)
        if phase == "repaired":
            repair(project)
        directory = root / phase
        directory.mkdir()
        args = [
            str(editor_exe),
            "-batchmode",
            "-nographics",
            "-quit",
            "-job-worker-count",
            "2",
            "-projectPath",
            str(project),
            "-logFile",
            "-",
            "-executeMethod",
            METHOD,
            "--shop-session",
            session,
            "--shop-phase",
            phase,
            "--mcp-scenario-output",
            str(directory / "build"),
        ]
        if reduced and phase != "original":
            args.extend(["--shop-reduced-output", str(directory / "reduced-build")])
        print(f"{phase}: building with {editor_exe}", flush=True)
        process = execute(Invocation(args, directory / "editor-process", timeout=1200))
        if process["actual_exit_code"] or process["forced_termination"]:
            raise ValueError(
                f"Native Editor failed. See {directory / 'editor-process/process.log'}"
            )
        editor = read_json(directory / "editor.json")
        revision = confirm_inputs(project, editor, phase, session)
        retain_sources(project, directory, editor["inputs"])
        if guid is None:
            guid = editor["asset_guid"]
        bundle, run, job = run_player(directory, revision)
        verify_steps(phase, run)
        observed = read_player_observation(job / "player.log")
        check_observation(phase, revision, guid, editor, observed)
        confirm_inputs(project, editor, phase, session)
        candidate = {
            "schema_version": 1,
            "project": str(project),
            "scenario": "verified-shop",
            "source_revision": revision,
            "inputs": editor["inputs"],
            "assets": {editor["asset_path"]: guid},
            "observations": {"loader_key": editor["key"]},
            "build_id": bundle["build_id"],
            "payload_hash": bundle["payload_hash"],
            "mappings": [
                {"path": editor["asset_path"], "scenarios": ["verified-shop"]},
                {"path": SCENE, "scenarios": ["verified-shop"]},
                {"path": PROBE, "scenarios": ["verified-shop"]},
            ],
            "state_check": {
                "log_marker": "SHOP_WORKFLOW_OBSERVATION ",
                "expected": {
                    "expected": "ShopBanner",
                    "loaded": "ShopBanner",
                    "same_asset": True,
                    "visible_banners": 1,
                },
            },
        }
        write_json(directory / "candidate.json", candidate)
        results[phase] = {
            "source_revision": revision,
            "asset_guid": guid,
            "editor": f"{phase}/editor.json",
            "run": (job / "run.json").relative_to(root).as_posix(),
            "player_log": (job / "player.log").relative_to(root).as_posix(),
            "status": run["status"],
            "exit_code": run["exit_code"],
            "job_id": run["job_id"],
            "build_id": bundle["build_id"],
            "payload_hash": bundle["payload_hash"],
            "observation": observed,
        }
        previous = editor
        print(f"{phase}: {run['status']}, exit {run['exit_code']}, observed {observed}", flush=True)
    write_json(
        root / "comparison.json", {"schema_version": 1, "session": session, "phases": results}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory for retained builds and evidence"
    )
    parser.add_argument(
        "--reduced-builds",
        action="store_true",
        help="Also freeze matched shorter replays for reduction trials",
    )
    args = parser.parse_args()
    project = fixture_project(args.project)
    editor = args.editor.resolve(strict=True)
    root = args.output.resolve()
    if root.is_relative_to(project) or project.is_relative_to(root):
        parser.error("Evidence and the disposable Unity project need separate directories")
    if (project / ".shop-workflow-owner").exists() or (
        project / "Assets/VerifiedWorkflowShop"
    ).exists():
        parser.error("This project already contains a fixture session; prepare a fresh project")
    root.mkdir(parents=True, exist_ok=False)
    session = uuid.uuid4().hex
    with (project / ".shop-workflow-owner").open("x", encoding="ascii") as marker:
        marker.write(session)
    exercise(editor, project, root, session, args.reduced_builds)


if __name__ == "__main__":
    main()
