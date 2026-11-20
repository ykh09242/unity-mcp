"""Build the opt-in Android IL2CPP shop fixture in an isolated Unity project."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

from player_e2e_artifacts import digest, write_json
from player_e2e_process import Invocation, execute
from run_android_scenario_fixture import FixtureBuild, PilotError
from unity_ci_packages import PreparationError, prepare

SUBTREE = Path("Assets/Scripts/ScenarioPlayerFixture")
FILES = (
    "MCPForUnity.ScenarioPlayerFixture.asmdef",
    "ShopWorkflowProbe.cs",
    "ShopAndroidProbe.cs",
    "Editor/MCPForUnity.ScenarioPlayerFixture.Editor.asmdef",
    "Editor/ShopWorkflowFixtureBuild.cs",
    "Editor/ShopAndroidFixtureBuild.cs",
)


def captured_source(prepared: Path, output: Path) -> str:
    """Hash the staged product and fixture inputs before the Editor generates additional files."""
    groups = {
        name: {
            path.relative_to(prepared / name).as_posix(): digest(path)
            for path in sorted((prepared / name).rglob("*"))
            if path.is_file()
        }
        for name in ("package", "project/Assets", "project/Packages", "project/ProjectSettings")
    }
    canonical = json.dumps(groups, sort_keys=True, separators=(",", ":")).encode("ascii")
    revision = sha256(canonical).hexdigest()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, encoding="ascii").strip()
    write_json(
        output / "source.json",
        {"checkout_head": head, "source_revision": revision, "inputs": groups},
    )
    return revision


def build(editor: Path, unity_version: str, output: Path, cache: Path | None) -> None:
    """Prepare an isolated package and invoke the exact fixture Editor method."""
    source = ROOT / "TestProjects/UnityMCPTests"
    assets = [SUBTREE / filename for filename in FILES]
    assets.extend(Path(f"{path}.meta") for path in tuple(assets))
    assets.extend([Path(f"{SUBTREE}.meta"), SUBTREE / "Editor.meta"])
    fixtures = {path.as_posix(): source / path for path in assets}
    if not editor.with_name("Data").joinpath("PlaybackEngines/AndroidPlayer").is_dir():
        raise PilotError("The selected Editor has no Android Build Support module")
    output.mkdir(parents=True)
    prepared = output / "prepared"
    prepare(
        unity_version,
        editor.parent / "Data",
        prepared,
        registry_cache=cache,
        extra_packages={"com.unity.modules.androidjni": {"source": "editor"}},
        fixture_sources=fixtures,
    )
    project = prepared / "project"
    build_id = uuid4().hex
    revision = captured_source(prepared, output)
    (project / ".android-fixture-owner").write_text(build_id, encoding="ascii")
    bundle = output / "bundle"
    bundle.mkdir()
    log = output / "editor.log"
    command = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(project),
        "-buildTarget",
        "Android",
        "-job-worker-count",
        "2",
        "-executeMethod",
        "MCPForUnityTests.PlayScenarios.Player.ShopAndroidFixtureBuild.BuildFromCommandLine",
        "--android-fixture-build-id",
        build_id,
        "--android-fixture-source",
        revision,
        "--android-fixture-output",
        str(bundle),
        "-logFile",
        str(log),
    ]
    process = execute(Invocation(command, output, timeout=1200))
    if process["actual_exit_code"] != 0 or process["forced_termination"]:
        raise PilotError(f"Android fixture build failed. See {log}")
    result = FixtureBuild.read(bundle / "build.json")
    if (result.build_id, result.source_revision, result.unity_version) != (
        build_id,
        revision,
        unity_version,
    ):
        raise PilotError("Android Editor output did not match the prepared source and build")
    print(bundle / "build.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--unity-version", required=True)
    parser.add_argument("--registry-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    try:
        build(
            options.editor.resolve(strict=True),
            options.unity_version,
            options.output.resolve(),
            options.registry_cache,
        )
    except (OSError, ValueError, PilotError, PreparationError, subprocess.SubprocessError) as error:
        print(f"Android build unavailable: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
