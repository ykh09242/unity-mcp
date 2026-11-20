"""Build two Windows Players and measure a stripped shader variant on a real graphics device."""

import argparse
from pathlib import Path
import sys
from uuid import UUID, uuid4

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from TestProjects.UnityMCPTests.Verification.run_prefab_move import fixture_project
from tools.player_e2e_artifacts import contained, digest, inventory, read_json, write_json
from tools.player_e2e_process import Invocation, execute

METHOD = "MCPForUnityTests.PlayScenarios.Player.ShaderVariantPairBuild.Run"
FOLDER = "Assets/Scripts/ScenarioPlayerFixture"
SCENE = "Assets/ShaderVariantPair/Tint.unity"
MATERIAL = "Assets/ShaderVariantPair/Surface.mat"
SHADER = FOLDER + "/RequiredTint.shader"
INPUTS = (
    SCENE,
    MATERIAL,
    SHADER,
    FOLDER + "/ShaderVariantPixelProbe.cs",
    FOLDER + "/Editor/ShaderVariantPairBuild.cs",
)
PHASES = ("stripped", "retained")


def require_process(command, directory, timeout):
    result = execute(Invocation(command, directory, timeout=timeout))
    if result["actual_exit_code"] != 0 or result["forced_termination"]:
        raise ValueError(f"Native execution failed; inspect {directory / 'process.log'}")


def verify_frame(directory, observation, color):
    with Image.open(directory / "frame.png") as frame:
        if frame.size != (64, 64) or frame.mode != "RGBA":
            raise ValueError("Unexpected camera image format")
        region = frame.crop((28, 28, 36, 36)).tobytes()
        if list(region) != color * 64 or list(region) != observation["rgba"]:
            raise ValueError(f"{directory.name} did not render the expected material region")
        if list(frame.getpixel((0, 63))) != observation["background"]:
            raise ValueError("Camera background differs between the rendered PNG and readback")


def measure(directory, phase, owner):
    build = read_json(directory / "build.json")
    player = read_json(directory / "player.json")
    guid = UUID(build["buildGuid"])
    if (
        guid.int == 0
        or UUID(player["buildGuid"]) != guid
        or build["owner"] != owner
        or build["phase"] != phase
        or build["target"] != "StandaloneWindows64"
        or build["apis"] != ["Direct3D11"]
        or player["editor"] is not False
        or player["graphicsApi"] != "Direct3D11"
        or not player["device"]
        or build["offered"] <= 0
        or build["retained"] != (build["offered"] if phase == "retained" else 0)
        or player["scene"] != build["scene"]
        or player["scene"] != SCENE
        or player["camera"] != "Tint camera"
        or player["material"] != "Surface"
        or player["materialGuid"] != build["materialGuid"]
        or UUID(player["materialGuid"]).int == 0
        or build["shaderPath"] != SHADER
        or player["shader"] != "Hidden/MCPFixture/RequiredTint"
        or player["keyword"] != build["keyword"]
        or player["keyword"] != "MCP_REQUIRED_TINT"
        or player["keywordEnabled"] is not True
        or player["region"] != [28, 28, 8, 8]
        or player["background"] != [0, 0, 255, 255]
    ):
        raise ValueError(f"{phase} build and Player observations disagree")
    expected = [255, 0, 0, 255] if phase == "stripped" else [0, 255, 0, 255]
    verify_frame(directory, player, expected)
    if phase == "stripped":
        reference = directory / "editor-reference"
        editor = read_json(reference / "observation.json")
        stable = (
            "graphicsApi",
            "device",
            "scene",
            "camera",
            "material",
            "materialGuid",
            "shader",
            "keyword",
            "keywordEnabled",
            "region",
            "background",
        )
        if editor["editor"] is not True or any(editor[key] != player[key] for key in stable):
            raise ValueError("Editor reference differs from the stripped Player's rendering inputs")
        verify_frame(reference, editor, [0, 255, 0, 255])
    return {
        "build_guid": guid.hex,
        "variant_candidates": build["offered"],
        "variant_retained": build["retained"],
        "unity_version": build["unityVersion"],
        "graphics_device": player["device"],
        "region_rgba": expected,
        "material_guid": player["materialGuid"],
        "files": {
            name: digest(directory / name)
            for name in ("build.json", "player.json", "frame.png", "payload.json")
        },
    }


def exercise(editor, project, output):
    owner = uuid4().hex
    with (project / ".shader-variant-owner").open("x", encoding="ascii") as stream:
        stream.write(owner)
    inputs = {}
    controls = {}
    for phase in PHASES:
        directory = output / phase
        directory.mkdir()
        require_process(
            [
                str(editor),
                "-batchmode",
                "-force-d3d11",
                "-quit",
                "-buildTarget",
                "Win64",
                "-projectPath",
                str(project),
                "-logFile",
                "-",
                "-executeMethod",
                METHOD,
                "--variant-owner",
                owner,
                "--variant-phase",
                phase,
                "--variant-output",
                str(directory),
            ],
            directory / "editor-process",
            1200,
        )
        observed = {
            name + suffix: digest(contained(project, name + suffix))
            for name in INPUTS
            for suffix in ("", ".meta")
        }
        if inputs and inputs != observed:
            raise ValueError("Scene, camera, shader, material or probe changed between builds")
        inputs = observed
        payload = directory / "build"
        entries = inventory(payload)
        write_json(directory / "payload.json", {"files": entries})
        require_process(
            [
                str(payload / "Tint.exe"),
                "-batchmode",
                "-force-d3d11",
                "-screen-width",
                "128",
                "-screen-height",
                "128",
                "-screen-fullscreen",
                "0",
                "-logFile",
                "-",
                "--variant-output",
                str(directory),
            ],
            directory / "player-process",
            120,
        )
        if inventory(payload) != entries:
            raise ValueError(f"{phase} Player payload changed during execution")
        controls[phase] = measure(directory, phase, owner)
    first, second = (controls[phase] for phase in PHASES)
    if first["build_guid"] == second["build_guid"] or any(
        first[key] != second[key]
        for key in ("unity_version", "graphics_device", "material_guid", "variant_candidates")
    ):
        raise ValueError(
            "Expected distinct builds with identical fixture inputs and graphics device"
        )
    write_json(
        output / "comparison.json",
        {
            "session": owner,
            "inputs": inputs,
            "controls": controls,
            "editor_reference": {
                name: digest(output / "stripped/editor-reference" / name)
                for name in ("frame.png", "observation.json")
            },
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    project = fixture_project(args.project)
    output = args.output.resolve()
    if project.is_relative_to(output) or output.is_relative_to(project):
        parser.error("Use a fresh output directory separate from the disposable project")
    output.mkdir(parents=True, exist_ok=False)
    exercise(args.editor.resolve(strict=True), project, output)


if __name__ == "__main__":
    main()
