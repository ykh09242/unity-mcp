"""Prepare and run the native pointer and localized layout demonstration."""

import argparse
from pathlib import Path, PurePosixPath
from shutil import copyfile, copyfileobj
from struct import unpack
import subprocess
from tarfile import open as open_tar
from typing import Final
from xml.etree import ElementTree

from player_e2e_artifacts import digest, read_json, write_json
from player_e2e_process import Invocation, execute

ROOT: Final = Path(__file__).resolve().parents[1]
TESTS: Final = ROOT / "TestProjects/UnityMCPTests/Assets/Tests/EditMode"
TEST_NAMES: Final = (
    "MCPForUnityTests.EditMode.Tools.Input.UguiRaycastDiagnosticsTests."
    "TransparentOverlayAndRepairUseTheSamePointAndDifferentGameState",
    "MCPForUnityTests.Editor.Tools.UguiTmpLayoutDiagnosticsTests."
    "LatinAndCjkClippingChangesAcrossTwoRenderedViewports",
)
VERSION: Final = "6000.0.69f1"


def copy_shaders(archive_path: Path, project: Path) -> None:
    """Copy only TMP's built-in shader files from the installed Unity resource package."""
    prefix = ("Assets", "TextMesh Pro", "Shaders")
    with open_tar(archive_path) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith("/pathname"):
                continue
            with archive.extractfile(member) as path_stream:
                pathname = PurePosixPath(path_stream.read().decode().strip())
            if (
                pathname.parts[:3] != prefix
                or len(pathname.parts) != 4
                or pathname.suffix not in {".shader", ".cginc", ".hlsl"}
                or "\\" in pathname.name
                or ":" in pathname.name
            ):
                continue
            folder = member.name.removesuffix("/pathname")
            target = project.joinpath(*pathname.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            for kind, path in (
                ("asset", target),
                ("asset.meta", target.with_suffix(target.suffix + ".meta")),
            ):
                asset = archive.getmember(folder + "/" + kind)
                if not asset.isfile():
                    raise ValueError("Expected a regular file in the installed TMP shader archive")
                with archive.extractfile(asset) as asset_stream, path.open("xb") as output:
                    copyfileobj(asset_stream, output)
    if not (project.joinpath(*prefix) / "TMP_SDF-Mobile.shader").is_file():
        raise ValueError(
            "The installed Unity resource archive must supply the built-in TMP SDF shaders"
        )


def prepare(output: Path, shader_package: Path, font: Path) -> Path:
    """Copy maintained test sources into a new project with a local package dependency."""
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    raycast = project / "Assets/Tests/Raycast"
    text = project / "Assets/Tests/Text"
    for folder in (
        raycast,
        text,
        project / "Packages",
        project / "ProjectSettings",
        output / "font-input",
        output / "exports",
    ):
        folder.mkdir(parents=True)
    for source, target, names in (
        (
            TESTS / "Tools/Input/UGUI",
            raycast,
            (
                "MCPForUnity.Input.UGUI.Tests.asmdef",
                "UguiRaycastDiagnosticsTests.cs",
                "VerifiedPointerOverlayTests.cs",
            ),
        ),
        (
            TESTS / "Tools",
            text,
            ("UguiTmpLayoutDiagnosticsTests.cs", "VerifiedPointerLocalizedLayoutTests.cs"),
        ),
    ):
        for name in names:
            for suffix in ("", ".meta"):
                copyfile(source / (name + suffix), target / (name + suffix))
    # The maintained assembly's TestAsmdef dependency belongs to fixtures outside this run.
    assembly = read_json(TESTS / "MCPForUnityTests.Editor.asmdef", 16384)
    assembly["references"].remove("TestAsmdef")
    write_json(text / "MCPForUnityTests.Editor.asmdef", assembly)
    package = ROOT / "MCPForUnity"
    package_name = read_json(package / "package.json", 16384)["name"]
    write_json(
        project / "Packages/manifest.json",
        {
            "dependencies": {
                package_name: "file:" + package.as_posix(),
                "com.unity.ugui": "2.0.0",
                "com.unity.test-framework": "1.4.6",
            }
        },
    )
    (project / "ProjectSettings/ProjectVersion.txt").write_text(
        f"m_EditorVersion: {VERSION}\n", encoding="utf-8"
    )
    copyfile(font, output / "font-input/UI08.ttf")
    copy_shaders(shader_package, project)
    return project


def verify_exports(output: Path) -> None:
    test_cases = ElementTree.parse(output / "native/results.xml").getroot().findall(".//test-case")
    actual = {case.get("fullname"): case.get("result") for case in test_cases}
    if actual != dict.fromkeys(TEST_NAMES, "Passed"):
        raise ValueError(f"Expected two native Unity test passes, received {actual}")
    exports = output / "exports"
    pointer = read_json(exports / "verified-pointer-overlay.json", 1024 * 1024)
    layout = read_json(exports / "verified-localized-layout.json", 1024 * 1024)
    if pointer["unity_version"] != VERSION or layout["unity_version"] != VERSION:
        raise ValueError("The observed Editor version differs from the fixture version")
    if layout["fixture_font_sha256"] != digest(output / "font-input/UI08.ttf"):
        raise ValueError("The observed font identity differs from the fixture font")
    for measurement in layout["measurements"]:
        width, height = measurement["viewport"]
        capture = exports / f"verified-layout-{width}x{height}.png"
        with capture.open("rb") as stream:
            header = stream.read(24)
        if (
            header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            or unpack(">II", header[16:24]) != (width, height)
            or measurement["capture"] != capture.name
            or measurement["capture_sha256"] != digest(capture)
        ):
            raise ValueError(f"The native capture does not match its receipt at {width}x{height}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--editor", required=True, type=Path, help=f"Unity {VERSION} Editor executable"
    )
    parser.add_argument(
        "--font", required=True, type=Path, help="Latin/CJK font file with fixture glyphs"
    )
    parser.add_argument("--output", required=True, type=Path, help="New directory under .unity-ci")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".unity-ci") or output == ROOT / ".unity-ci":
        parser.error("The new output directory must be inside the checkout's .unity-ci directory.")
    editor = args.editor.resolve(strict=True)
    shader_package = editor.parent.joinpath(
        "Data",
        "Resources",
        "PackageManager",
        "BuiltInPackages",
        "com.unity.ugui",
        "Package Resources",
        "TMP Essential Resources.unitypackage",
    )
    project = prepare(output, shader_package, args.font.resolve(strict=True))
    native = output / "native"
    command = [
        str(editor),
        "-batchmode",
        "-projectPath",
        str(project),
        "-job-worker-count",
        "1",
        "-screen-width",
        "640",
        "-screen-height",
        "480",
        "-runTests",
        "-testPlatform",
        "editmode",
        "-testFilter",
        ";".join(TEST_NAMES),
        "-testResults",
        str(native / "results.xml"),
        "-logFile",
        "-",
    ]
    start_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    process = execute(
        Invocation(
            command,
            native,
            timeout=600,
            environment={"UNITY_MCP_UI09_EXPORT_ROOT": str(output / "exports")},
        )
    )
    if process["actual_exit_code"] or process["forced_termination"]:
        print(
            f"Unity did not pass the selected tests. See {native / 'launch.json'} and {native / 'process.log'}."
        )
        return 1
    verify_exports(output)
    script_assemblies = project / "Library/ScriptAssemblies"
    write_json(
        output / "receipt.json",
        {
            "starting_checkout_revision": start_revision,
            "unity_version": VERSION,
            "native_exit_code": process["actual_exit_code"],
            "fixture_inputs": {
                path.relative_to(project).as_posix(): digest(path)
                for path in sorted((project / "Assets/Tests").rglob("*"))
                if path.suffix in {".cs", ".asmdef"}
            },
            "compiled_assemblies": {
                name: digest(script_assemblies / name)
                for name in (
                    "MCPForUnity.Editor.dll",
                    "MCPForUnity.Runtime.dll",
                    "MCPForUnity.Input.UGUI.Runtime.dll",
                    "MCPForUnity.Input.UGUI.Tests.dll",
                    "MCPForUnityTests.EditMode.dll",
                )
            },
            "evidence": {
                path.relative_to(output).as_posix(): digest(path)
                for path in sorted((output / "exports").iterdir())
            },
            "native_test_results_sha256": digest(native / "results.xml"),
        },
    )
    print(output / "receipt.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
