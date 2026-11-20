"""Build and measure isolated assembly and texture importer controls in a disposable Unity project."""

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "Server/src")]

from TestProjects.UnityMCPTests.Verification.run_prefab_move import fixture_project, run_player
from cli.commands.verification_configuration import compare, load_observation
from tools.player_e2e_artifacts import contained, digest, read_json, write_json
from tools.player_e2e_process import execute, Invocation

FOLDER = "Assets/ConfigurationControl"
METHOD = "MCPForUnityTests.PlayScenarios.Player.ConfigurationControlBuild.Run"


def exercise(editor, project, root):
    owner = uuid4().hex
    folder = contained(project, FOLDER)
    folder.mkdir()
    with (project / ".config-control-owner").open("x", encoding="ascii") as stream:
        stream.write(owner)
    definition = {
        "name": "ConfigurationControl.Optional",
        "defineConstraints": ["UNITY_EDITOR || CONFIG_CONTROL_RESOURCE"],
        "versionDefines": [
            {
                "name": "com.unity.modules.imageconversion",
                "expression": "1.0.0",
                "define": "CONFIG_CONTROL_RESOURCE",
            }
        ],
        "includePlatforms": [],
    }
    source = folder / "Optional.cs"
    source.write_text(
        "namespace ConfigurationControl { public class OptionalType {} }\n", encoding="utf-8"
    )
    asmdef = folder / "Optional.asmdef"
    write_json(asmdef, definition)
    before = None
    baseline_texture = None
    baseline_job = None
    stable = {}
    result = {}
    for phase in ("baseline", "importer", "version", "platform"):
        if phase == "version":
            definition["versionDefines"][0]["expression"] = "[999.0.0,1000.0.0)"
        elif phase == "platform":
            definition["versionDefines"][0]["expression"] = "1.0.0"
            definition["includePlatforms"] = ["Editor"]
        if phase in {"version", "platform"}:
            asmdef.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
        directory = root / phase
        directory.mkdir()
        invocation = Invocation(
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
                "--config-owner",
                owner,
                "--config-phase",
                phase,
                "--config-output",
                str(directory),
            ],
            directory / "editor-process",
            timeout=1200,
        )
        process = execute(invocation)
        if process["actual_exit_code"] != 0 or process["forced_termination"]:
            raise ValueError(
                f"Native configuration capture failed: {directory / 'editor-process/process.log'}"
            )
        assembly = load_observation(read_json(directory / "assembly.json"))
        texture = load_observation(read_json(directory / "texture.json"))
        if assembly.asmdef_sha256 != digest(asmdef) or assembly.source_sha256 != digest(source):
            raise ValueError("compiler_inputs_changed_after_capture")
        if texture.meta_sha256 != digest(
            folder / "Sample.png.meta"
        ) or texture.source_sha256 != digest(folder / "Sample.png"):
            raise ValueError("texture_inputs_changed_after_capture")
        for relative, expected in texture.owner_inputs.items():
            if expected is None or digest(contained(project, relative)) != expected:
                raise ValueError("render_dependency_changed_after_capture")
        if phase == "baseline":
            before = assembly
            baseline_texture = texture
            stable = {
                path.name: digest(path)
                for path in folder.iterdir()
                if path.is_file() and path.name != "Sample.png.meta"
            }
            if not (before.editor.source_included and before.player.source_included):
                raise ValueError("expected_baseline_source_in_editor_and_player")
        if phase == "importer":
            if stable != {
                path.name: digest(path)
                for path in folder.iterdir()
                if path.is_file() and path.name != "Sample.png.meta"
            }:
                raise ValueError("expected_only_texture_metadata_change_in_fixture")
        if phase in {"baseline", "importer"}:
            _, _, job = run_player(directory, texture.observation_revision)
            if phase == "baseline":
                baseline_job = job
                continue
            diff = compare(baseline_texture, texture, baseline_job, job)
            observed = diff["player_observation"]
            if (
                not diff["source_unchanged"]
                or diff["only_meta_changed_in_selected_inputs"] is not True
                or not diff["meta_changed"]
                or diff["importer_changes"] != {"sRGBTexture": {"before": True, "after": False}}
                or observed["state"] != "changed"
                or observed["before"]["color_space"] != "Linear"
                or not all(48 <= channel <= 62 for channel in observed["before"]["rgba"][:3])
                or not all(124 <= channel <= 132 for channel in observed["after"]["rgba"][:3])
            ):
                raise ValueError("importer_control_did_not_observe_expected_rendered_pixel_change")
        else:
            diff = compare(before, assembly)
            condition = "defineConstraints" if phase == "version" else "includePlatforms"
            if (
                not diff["source_unchanged"]
                or not diff["target_assembly_removed"]
                or not assembly.editor.source_included
                or assembly.player.source_included
                or [r["condition"] for r in diff["target_exclusions"]] != [condition]
            ):
                raise ValueError("assembly_control_missing_expected_compiler_exclusion")
        result[phase] = diff
    write_json(root / "comparison.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    project = fixture_project(args.project)
    root = args.output.resolve()
    if root.is_relative_to(project) or project.is_relative_to(root):
        parser.error("Use a new output separate from the owned project")
    root.mkdir(parents=True, exist_ok=False)
    exercise(args.editor.resolve(strict=True), project, root)


if __name__ == "__main__":
    main()
