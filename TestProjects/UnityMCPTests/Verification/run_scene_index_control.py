"""Build and observe three ordinary Windows Players with controlled scene indices."""

import argparse
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "Server/src")]

from cli.commands.verification_scene_index import SETTINGS, capture, compare_scene_indices

from TestProjects.UnityMCPTests.Verification.run_prefab_move import FIXTURE, fixture_project
from tools.player_e2e_artifacts import contained, digest, write_json
from tools.player_e2e_process import Invocation, execute

SOURCE = "Assets/Scripts/ScenarioPlayerFixture/SceneIndexProbe.cs"
FOLDER = "Assets/SceneIndexControl"
METHOD = "MCPForUnityTests.PlayScenarios.Player.SceneIndexControlBuild.Run"


def exercise(editor, project, output):
    owner = uuid4().hex
    with (project / ".scene-index-owner").open("x", encoding="ascii") as marker:
        marker.write(owner)
    source = contained(project, SOURCE)
    original = (FIXTURE / SOURCE).read_bytes()
    if source.read_bytes() != original or original.count(b"SceneManager.LoadScene(1);") != 1:
        raise ValueError("Expected the maintained numeric loader")
    roots = []
    before_inputs = None
    for phase in ("before", "after", "repaired"):
        directory = output / phase
        directory.mkdir()
        roots.append(directory)
        if phase == "repaired":
            if source.read_bytes() != original:
                raise ValueError("Loader changed outside the scene control")
            source.write_bytes(
                original.replace(b"SceneManager.LoadScene(1);", b"SceneManager.LoadScene(2);")
            )
        process = execute(
            Invocation(
                [
                    str(editor),
                    "-batchmode",
                    "-nographics",
                    "-quit",
                    "-buildTarget",
                    "Win64",
                    "-projectPath",
                    str(project),
                    "-logFile",
                    "-",
                    "-executeMethod",
                    METHOD,
                    "--scene-owner",
                    owner,
                    "--scene-phase",
                    phase,
                    "--scene-output",
                    str(directory),
                ],
                directory / "editor-process",
                timeout=1200,
            )
        )
        if process["actual_exit_code"] or process["forced_termination"]:
            raise ValueError(
                f"Native scene build failed. See {directory / 'editor-process/process.log'}"
            )
        assets = [SOURCE, *(f"{FOLDER}/{name}.unity" for name in ("Start", "World", "Menu"))]
        paths = [SETTINGS, *(p + suffix for p in assets for suffix in ("", ".meta"))]
        inputs = {p: digest(contained(project, p)) for p in paths}
        # Retain at the same project-relative paths the offline command accepts.
        for relative, expected in inputs.items():
            path = contained(directory, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as target:
                target.write(contained(project, relative).read_bytes())
            if digest(path) != expected:
                raise ValueError("Source input changed during retention")
        if before_inputs is None:
            before_inputs = inputs
        else:
            expected_changes = {SETTINGS} if phase == "after" else {SETTINGS, SOURCE}
            if {p for p in paths if inputs[p] != before_inputs[p]} != expected_changes:
                raise ValueError("Unexpected fixture inputs changed")
        execute(
            Invocation(
                [
                    str(directory / "build/SceneControl.exe"),
                    "-batchmode",
                    "-nographics",
                    "-logFile",
                    str(directory / "player.log"),
                    "--scene-trial",
                    uuid4().hex,
                ],
                directory / "player-process",
                timeout=90,
            )
        )
    sites = capture(roots[0], SOURCE)["calls"]
    if len(sites) != 1:
        raise ValueError("Expected exactly one numeric loader in the maintained source")
    result = compare_scene_indices(roots, SOURCE, sites[0]["line"])
    if (
        not result["scene_reorder_only_in_enabled_list"]
        or len(result["affected_calls"]) != 1
        or not result["player_observation"]["original_scene_restored"]
        or [e["scene"] for e in result["player_observation"]["events"]]
        != [f"{FOLDER}/{name}.unity" for name in ("World", "Menu", "World")]
    ):
        raise ValueError("The same numeric call must open the wrong scene until repaired")
    write_json(output / "comparison.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("editor", "project", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    project = fixture_project(args.project)
    output = args.output.resolve()
    if output.is_relative_to(project) or project.is_relative_to(output):
        parser.error("Evidence must be outside the disposable project")
    output.mkdir(parents=True, exist_ok=False)
    exercise(args.editor.resolve(strict=True), project, output)


if __name__ == "__main__":
    main()
