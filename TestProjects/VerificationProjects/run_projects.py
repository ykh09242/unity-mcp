"""Build two independent Unity projects against one staged distribution on Windows CI."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "Server/src"))

from cli.utils.play_scenario_player import PlayerRunOptions, run_player
from models.play_scenarios import PlayScenario
from player_e2e_process import Invocation, execute
from services.verification.input_chunks import source_revision
from unity_ci_packages import prepare


def tree_revision(directory):
    inputs = {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }
    return source_revision(inputs)


def project_inputs(project):
    assets = project / "Assets"
    return {
        path.relative_to(project).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in assets.rglob("*")
        if path.is_file() and (path.suffix != ".meta" or path.with_suffix("").is_file())
    }


def build_and_run(editor, example, project, output):
    (definition_file,) = (example / "ProjectSettings/MCPForUnity/PlayScenarios").glob("*.json")
    definition = PlayScenario.model_validate_json(definition_file.read_bytes())
    revision = source_revision(project_inputs(project))
    output.mkdir()
    build = output / "build"
    process = execute(
        Invocation(
            [
                str(editor),
                "-batchmode",
                "-nographics",
                "-quit",
                "-projectPath",
                str(project),
                "-buildTarget",
                "Win64",
                "-logFile",
                "-",
                "-executeMethod",
                "MCPForUnity.Editor.Services.PlayScenarios.PlayScenarioPlayerBuild.BuildFromCommandLine",
                "--mcp-scenario-name",
                definition.name,
                "--mcp-scenario-output",
                str(build),
                "--mcp-scenario-source-revision",
                revision,
            ],
            output / "editor-process",
            timeout=1800,
        )
    )
    if process["actual_exit_code"] != 0 or process["forced_termination"]:
        raise ValueError(f"{example.name} build failed; see {output / 'editor-process'}")
    if source_revision(project_inputs(project)) != revision:
        raise ValueError(f"{example.name} project assets changed during the build")
    bundle = json.loads((build / "scenario-bundle.json").read_bytes())
    outcome = run_player(
        PlayerRunOptions(
            build_directory=build,
            output_directory=output / "runs",
            timeout_seconds=120,
            expected_build_id=bundle["build_id"],
            expected_payload_hash=bundle["payload_hash"],
            expected_build_revision=revision,
            source_revision=revision,
            expected_scenario=definition.name,
            require_verified_payload=True,
        )
    )
    expected = [(step.name, step.action) for step in definition.steps]
    steps = outcome.report.get("steps", [])
    if (
        outcome.exit_code != 0
        or outcome.report.get("status") != "succeeded"
        or [(step["name"], step["action"]) for step in steps] != expected
        or any(step["status"] != "passed" for step in steps)
    ):
        raise ValueError(f"{example.name} state verification failed; see {outcome.directory}")
    return {
        "scenario": definition.name,
        "build_id": bundle["build_id"],
        "source_revision": revision,
        "state_steps": [
            step.model_dump(exclude_none=True)
            for step in definition.steps
            if step.action == "wait_state"
        ],
        "native_report": str(outcome.directory / "run.json"),
    }


def run(editor, output):
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "distribution"
    prepare("6000.0.69f1", editor.parent / "Data", stage, fixture_sources={})
    package = stage / "package"
    revision = tree_revision(package)
    cases = {}
    for name in ("Harbor", "Observatory"):
        example = EXAMPLES / name
        project = stage / name
        shutil.copytree(example, project)
        # Keep relative file: dependencies at the same depth as the prepared project.
        shutil.copyfile(
            stage / "project/Packages/manifest.json", project / "Packages/manifest.json"
        )
        cases[name] = build_and_run(editor, example, project, output / name)
        if tree_revision(package) != revision:
            raise ValueError("The shared package changed during verification")
    result = {"package_sha256": revision, "projects": cases}
    (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(run(options.editor.resolve(), options.output.resolve()), indent=2))
