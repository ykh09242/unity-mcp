"""Function-scoped fixtures for independent compile harness test families."""

from dataclasses import dataclass

import json

import os

from pathlib import Path

import shlex

import shutil

import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


SCRIPT = ROOT / "tools" / "compile-check.sh"


ROSLYN_DIRECTORY = "MonoBleedingEdge/lib/mono/4.5"


ROSLYN_REFERENCES = (
    "Microsoft.CodeAnalysis.dll",
    "Microsoft.CodeAnalysis.CSharp.dll",
    "System.Collections.Immutable.dll",
    "System.Reflection.Metadata.dll",
)


OPTIONAL_ASSEMBLIES = ("MCPForUnity.CustomTools.RoslynOff", "MCPForUnity.CustomTools.RoslynOn")


OWNED_ASSEMBLIES = (
    "MCPForUnity.Runtime",
    "MCPForUnity.Editor",
    *OPTIONAL_ASSEMBLIES,
    "TestAsmdef",
    "MCPForUnityTests.EditMode",
)


COROUTINES_ASSEMBLY = "Unity.EditorCoroutines.Editor"


STALE_REFERENCES = {
    "DATA/Managed/UnityEngine/UnityEditor.PackageManagerUIModule.dll",
    "DATA/Managed/UnityEngine/UnityEditor.UIServiceModule.dll",
    "DATA/Managed/UnityEngine/UnityEngine.ProfilerModule.dll",
    "DATA/Managed/UnityEngine/UnityEngine.UIElementsNativeModule.dll",
    "DATA/Managed/UnityEngine/UnityEngine.UNETModule.dll",
    "DATA/PlaybackEngines/AndroidPlayer/Unity.Android.Gradle.dll",
    "DATA/PlaybackEngines/AndroidPlayer/Unity.Android.Types.dll",
    "DATA/PlaybackEngines/AndroidPlayer/UnityEditor.Android.Extensions.dll",
    "DATA/PlaybackEngines/WindowsStandaloneSupport/UnityEditor.WindowsStandalone.Extensions.dll",
}


@dataclass(frozen=True)
class CompileHarness:
    repo: Path
    data: Path
    extra: Path
    output: Path
    calls: Path
    bash: str
    coroutines: Path

    def run(
        self,
        version: str,
        platforms: str = "linux",
        *,
        test_project: Path | None = None,
        framework: Path | None = None,
        coroutines: Path | None = None,
        ugui: bool = False,
        input_system: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(
            os.environ,
            UNITY_DATA=self.data.as_posix(),
            REPO=self.repo.as_posix(),
            UNITY_VERSION=version,
            EXTRA_REFS=self.extra.as_posix(),
            TEST_FRAMEWORK_SOURCE=framework.as_posix() if framework else "",
            TEST_PROJECT=test_project.as_posix() if test_project else "",
            EDITOR_COROUTINES_SOURCE=(coroutines or self.coroutines).as_posix(),
            PLATFORMS=platforms,
            COMPILE_INPUT_UGUI="1" if ugui else "0",
            COMPILE_INPUT_SYSTEM="1" if input_system else "0",
            INPUT_SYSTEM_REFS=self.extra.as_posix(),
            OUT=self.output.as_posix(),
            FAKE_COMPILER_CALLS=self.calls.as_posix(),
        )
        return subprocess.run(
            [self.bash, SCRIPT.as_posix()], env=env, capture_output=True, text=True
        )


@pytest.fixture
def harness(tmp_path: Path) -> CompileHarness:
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    repo, data, extra = (tmp_path / name for name in ("repo", "data", "extra"))
    for path in (
        repo / "tools" / "compile-refs",
        data / "DotNetSdkRoslyn",
        data / "NetCoreRuntime",
        extra,
    ):
        path.mkdir(parents=True)
    (data / "DotNetSdkRoslyn" / "csc.dll").touch()
    reference = data / "Managed" / "Core.dll"
    reference.parent.mkdir()
    reference.touch()
    for name in ("Runtime", "Editor"):
        (repo / "tools" / "compile-refs" / f"{name}.txt").write_text(
            "DATA/Managed/Core.dll\n", encoding="utf-8"
        )
        directory = repo / "MCPForUnity" / name
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}\n", encoding="utf-8")
    (repo / "tools" / "compile-defines.txt").write_text("UNITY_EDITOR\n", encoding="utf-8")
    optional = repo / "CustomTools/RoslynRuntimeCompilation"
    optional.mkdir(parents=True)
    for name in ("RoslynRuntimeCompiler.cs", "ManageRuntimeCompilation.cs"):
        (optional / name).write_text("class Fixture {}\n", encoding="utf-8")
    roslyn = data / ROSLYN_DIRECTORY
    roslyn.mkdir(parents=True)
    for name in ROSLYN_REFERENCES:
        (roslyn / name).touch()
    ugui = data / "Resources/PackageManager/BuiltInPackages/com.unity.ugui"
    for source, name in (("Runtime/UGUI", "UnityEngine.UI"), ("Editor/UGUI", "UnityEditor.UI")):
        directory = ugui / source
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class UiFixture {}\n", encoding="utf-8")
        (directory / f"{name}.asmdef").write_text(json.dumps({"name": name}), encoding="utf-8")
    bcl = repo / "tools" / "compile-refs" / "BCL"
    bcl.mkdir()
    for name in ("Runtime", "Editor"):
        (bcl / f"{name}.txt").write_text("", encoding="utf-8")
    for version in ("2021.3", "2022.3", "6000.3", "6000.6", "6000.7", "7000.0"):
        profile = repo / "tools" / "compile-refs" / version
        profile.mkdir()
        for name in ("Runtime", "Editor"):
            (profile / f"{name}.txt").write_text("DATA/Managed/Core.dll\n", encoding="utf-8")
    compiler = data / "NetCoreRuntime" / "dotnet"
    compiler.write_text(
        """#!/usr/bin/env bash
set -eu
rsp=${2#@}
echo "$rsp" >> "$FAKE_COMPILER_CALLS"
while IFS= read -r line; do
  case "$line" in
    -out:*) output=${line#-out:}; output=${output#\\"}; output=${output%\\"}; : > "$output" ;;
  esac
done < "$rsp"
""",
        encoding="utf-8",
    )
    compiler.chmod(0o755)
    (tmp_path / "output" / "linux").mkdir(parents=True)
    coroutines = repo / "editor coroutines"
    (coroutines / "Editor").mkdir(parents=True)
    (coroutines / "Editor/Fixture.cs").write_text(
        "class EditorCoroutineFixture {}", encoding="utf-8"
    )
    (coroutines / f"Editor/{COROUTINES_ASSEMBLY}.asmdef").write_text(
        json.dumps({"name": COROUTINES_ASSEMBLY, "includePlatforms": ["Editor"]}), encoding="utf-8"
    )
    (coroutines / "Tests").mkdir()
    (coroutines / "Tests/Excluded.cs").write_text(
        "#error Package tests must not compile", encoding="utf-8"
    )
    result = CompileHarness(
        repo, data, extra, tmp_path / "output", tmp_path / "calls.txt", bash, coroutines
    )
    modern_sdk(result)
    return result


def modern_sdk(
    harness: CompileHarness, root: str = "Tools/Scripting/DotNetSdk", version: str = "9.0.100"
) -> Path:
    sdk = harness.data / root
    compiler = sdk / "sdk" / version / "Roslyn" / "bincore" / "csc.dll"
    compiler.parent.mkdir(parents=True, exist_ok=True)
    compiler.touch()
    sdk.mkdir(exist_ok=True)
    shutil.copy2(harness.data / "NetCoreRuntime" / "dotnet", sdk / "dotnet")
    return sdk


@pytest.fixture
def staged_tests(harness: CompileHarness) -> tuple[Path, Path]:
    project, framework = harness.repo / "staged project", harness.repo / "framework"
    for relative, asmdef in (
        ("Assets/Scripts/TestAsmdef", "TestAsmdef.asmdef"),
        ("Assets/Tests/EditMode", "MCPForUnityTests.Editor.asmdef"),
    ):
        directory = project / relative
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}", encoding="utf-8")
        (directory / asmdef).write_text("{}", encoding="utf-8")
    for assembly in ("UnityEngine.TestRunner", "UnityEditor.TestRunner"):
        directory = framework / assembly
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}", encoding="utf-8")
    refs = harness.repo / "tools/compile-refs"
    for editor in refs.rglob("Editor.txt"):
        if editor.parent.name != "BCL":
            editor.write_text(
                editor.read_text(encoding="utf-8")
                + "LIBCACHE/UnityEngine.TestRunner.dll\nLIBCACHE/UnityEditor.TestRunner.dll\n",
                encoding="utf-8",
            )
    cecil = harness.data / "Tools/Compilation/ApiUpdater"
    cecil.mkdir(parents=True)
    for name in (
        "Mono.Cecil.dll",
        "Mono.Cecil.Pdb.dll",
        "Mono.Cecil.Mdb.dll",
        "Mono.Cecil.Rocks.dll",
    ):
        (cecil / name).touch()
    for suffix in ("", ".Pdb", ".Mdb", ".Rocks"):
        (harness.data / f"Managed/Unity.Cecil{suffix}.dll").touch()
    return project, framework


UGUI_ASSEMBLIES = (
    "MCPForUnity.Input.UGUI.Runtime",
    "MCPForUnity.Input.UGUI.Editor",
    "MCPForUnity.Input.UGUI.Tests",
)


@pytest.fixture
def ugui_sources(harness: CompileHarness, staged_tests: tuple[Path, Path]) -> tuple[Path, Path]:
    project, framework = staged_tests
    for root, relative, assembly in (
        (harness.repo, "MCPForUnity/Runtime/PlayScenarios/UGUI", UGUI_ASSEMBLIES[0]),
        (harness.repo, "MCPForUnity/Editor/Tools/Input/UGUI", UGUI_ASSEMBLIES[1]),
        (project, "Assets/Tests/EditMode/Tools/Input/UGUI", UGUI_ASSEMBLIES[2]),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class OptionalUiFixture {}", encoding="utf-8")
        original = (
            ROOT / relative / f"{assembly}.asmdef"
            if root == harness.repo
            else ROOT / "TestProjects/UnityMCPTests" / relative / f"{assembly}.asmdef"
        )
        shutil.copy2(original, directory / f"{assembly}.asmdef")
    for root, relative in (
        (harness.repo, "MCPForUnity/Runtime/OtherOptional"),
        (harness.repo, "MCPForUnity/Editor/Tools/Input/InputSystem"),
        (project, "Assets/Tests/EditMode/OtherOptional"),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "Other.asmdef").write_text('{"name":"Other.Optional"}', encoding="utf-8")
        (directory / "Excluded.cs").write_text(
            "#error Not owned by the parent assembly", encoding="utf-8"
        )
    cache = harness.data / "Resources/PackageManager/ProjectTemplates/libcache/ui/ScriptAssemblies"
    cache.mkdir(parents=True)
    for assembly in ("UnityEngine.UI", "UnityEditor.UI"):
        (cache / f"{assembly}.dll").touch()
    for manifest in (harness.repo / "tools/compile-refs").rglob("*.txt"):
        if manifest.parent.name == "BCL":
            continue
        prefix = "COMPILED" if manifest.parent.name == "7000.0" else "LIBCACHE"
        with manifest.open("a", encoding="utf-8") as stream:
            stream.write(f"{prefix}/UnityEngine.UI.dll\n")
    return project, framework


INPUT_SYSTEM_ASSEMBLIES = (
    "MCPForUnity.Input.InputSystem.Runtime",
    "MCPForUnity.Input.InputSystem.Editor",
    "MCPForUnity.Input.InputSystem.Tests",
)


@pytest.fixture
def input_system_sources(
    harness: CompileHarness, staged_tests: tuple[Path, Path]
) -> tuple[Path, Path]:
    project, framework = staged_tests
    for root, relative, assembly in (
        (harness.repo, "MCPForUnity/Runtime/PlayScenarios/InputSystem", INPUT_SYSTEM_ASSEMBLIES[0]),
        (harness.repo, "MCPForUnity/Editor/Tools/Input/InputSystem", INPUT_SYSTEM_ASSEMBLIES[1]),
        (project, "Assets/Tests/EditMode/Tools/Input/InputSystem", INPUT_SYSTEM_ASSEMBLIES[2]),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class OptionalInputFixture {}", encoding="utf-8")
        original = (
            ROOT / relative / f"{assembly}.asmdef"
            if root == harness.repo
            else ROOT / "TestProjects/UnityMCPTests" / relative / f"{assembly}.asmdef"
        )
        shutil.copy2(original, directory / f"{assembly}.asmdef")
    for name in ("Unity.InputSystem.dll", "Unity.InputSystem.TestFramework.dll"):
        (harness.extra / name).touch()
    return project, framework
