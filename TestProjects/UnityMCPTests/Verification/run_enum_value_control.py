"""Recompile a changed and repaired enum, reopening the same saved Unity prefab each time."""

import argparse
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "Server/src")]

from cli.commands.verification_enum_value import compare_enum_values, load_enum

from TestProjects.UnityMCPTests.Verification.run_prefab_move import FIXTURE, fixture_project
from tools.player_e2e_artifacts import contained, digest, write_json
from tools.player_e2e_process import Invocation, execute

SCRIPT = "Assets/Scripts/ScenarioPlayerFixture/SavedEnumProbe.cs"
PREFAB = "Assets/SavedEnumControl/Encounter.prefab"
COMPONENT = "MCPForUnityTests.PlayScenarios.Player.SavedEnumProbe"


def exercise(editor, project, output):
    owner = uuid4().hex
    with (project / ".enum-value-owner").open("x", encoding="ascii") as marker:
        marker.write(owner)
    script = contained(project, SCRIPT)
    original = (FIXTURE / SCRIPT).read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"

    def members(lines):
        return newline.join(b"        " + name.encode() + b"," for name in lines)

    old_members = members(["Idle", "Combat", "Reward"])
    if script.read_bytes() != original or original.count(old_members) != 1:
        raise ValueError("Expected the maintained original enum source")
    previous = original
    original_hash = None
    roots = []
    for phase, replacement in (
        ("before", old_members),
        ("after", members(["Idle", "Reward", "Combat"])),
        ("repaired", members(["Idle = 0", "Reward = 2", "Combat = 1"])),
    ):
        directory = output / phase
        directory.mkdir()
        capture_root = directory / "capture"
        roots.append(capture_root)
        if phase != "before":
            if (
                script.read_bytes() != previous
                or digest(contained(project, PREFAB)) != original_hash
            ):
                raise ValueError(
                    "The controlled script or original prefab changed before recompilation"
                )
            previous = original.replace(old_members, replacement)
            script.write_bytes(previous)
        method = (
            "MCPForUnityTests.PlayScenarios.Player.SavedEnumControl.CreateAndCapture"
            if phase == "before"
            else "MCPForUnity.Editor.Tools.VerificationEnumValue.CaptureFromCommandLine"
        )
        process = execute(
            Invocation(
                [
                    str(editor),
                    "-batchmode",
                    "-nographics",
                    "-quit",
                    "-projectPath",
                    str(project),
                    "-logFile",
                    "-",
                    "-executeMethod",
                    method,
                    "--enum-owner",
                    owner,
                    "--enum-prefab",
                    PREFAB,
                    "--enum-component",
                    COMPONENT,
                    "--enum-field",
                    "State",
                    "--enum-output",
                    str(capture_root),
                ],
                directory / "editor-process",
                timeout=1200,
            )
        )
        if process["actual_exit_code"] or process["forced_termination"]:
            raise ValueError(
                f"Native prefab read failed. See {directory / 'editor-process/process.log'}"
            )
        read = load_enum(capture_root)
        if any(digest(contained(project, p)) != h for p, h in read.inputs.items()):
            raise ValueError("Native prefab read no longer matches the project")
        if phase == "before":
            original_hash = read.inputs[PREFAB]
    result = compare_enum_values(roots)
    if (
        result["value"] != 1
        or result["original_meaning_restored"] is not True
        or [r["members_for_saved_integer"] for r in result["reads"].values()]
        != [["Combat"], ["Reward"], ["Combat"]]
    ):
        raise ValueError("The imported original prefab must change meaning and recover")
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
