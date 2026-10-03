"""Exercise the real compile harness with a hermetic reference tree and compiler shim."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "compile-check.sh"
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

    def run(self, version: str, platforms: str = "linux", *, test_project: Path | None = None,
            framework: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, UNITY_DATA=self.data.as_posix(), REPO=self.repo.as_posix(),
                   UNITY_VERSION=version, EXTRA_REFS=self.extra.as_posix(),
                   TEST_FRAMEWORK_SOURCE=framework.as_posix() if framework else "",
                   TEST_PROJECT=test_project.as_posix() if test_project else "",
                   PLATFORMS=platforms, OUT=self.output.as_posix(),
                   FAKE_COMPILER_CALLS=self.calls.as_posix())
        return subprocess.run([self.bash, SCRIPT.as_posix()], env=env, capture_output=True, text=True)


@pytest.fixture
def harness(tmp_path: Path) -> CompileHarness:
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    repo, data, extra = (tmp_path / name for name in ("repo", "data", "extra"))
    for path in (repo / "tools" / "compile-refs", data / "DotNetSdkRoslyn", data / "NetCoreRuntime", extra):
        path.mkdir(parents=True)
    (data / "DotNetSdkRoslyn" / "csc.dll").touch()
    reference = data / "Managed" / "Core.dll"
    reference.parent.mkdir()
    reference.touch()
    for name in ("Runtime", "Editor"):
        (repo / "tools" / "compile-refs" / f"{name}.txt").write_text("DATA/Managed/Core.dll\n", encoding="utf-8")
        directory = repo / "MCPForUnity" / name
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}\n", encoding="utf-8")
    (repo / "tools" / "compile-defines.txt").write_text("UNITY_EDITOR\n", encoding="utf-8")
    bcl = repo / "tools" / "compile-refs" / "BCL"
    bcl.mkdir()
    for name in ("Runtime", "Editor"):
        (bcl / f"{name}.txt").write_text("", encoding="utf-8")
    for version in ("2021.3", "2022.3"):
        profile = repo / "tools" / "compile-refs" / version
        profile.mkdir()
        for name in ("Runtime", "Editor"):
            (profile / f"{name}.txt").write_text("DATA/Managed/Core.dll\n", encoding="utf-8")
    compiler = data / "NetCoreRuntime" / "dotnet"
    compiler.write_text('''#!/usr/bin/env bash
set -eu
rsp=${2#@}
echo "$rsp" >> "$FAKE_COMPILER_CALLS"
while IFS= read -r line; do
  case "$line" in
    -out:*) output=${line#-out:}; output=${output#\\"}; output=${output%\\"}; touch "$output" ;;
  esac
done < "$rsp"
''', encoding="utf-8")
    compiler.chmod(0o755)
    (tmp_path / "output" / "linux").mkdir(parents=True)
    return CompileHarness(repo, data, extra, tmp_path / "output", tmp_path / "calls.txt", bash)


@pytest.mark.parametrize("version,profile", [
    ("2021.3.45f2", "2021.3"),
    ("2022.3.62f1", "2022.3"),
    ("2022.3.76f1", "2022.3"),
    ("6000.0.75f1", ""),
    ("6000.0.84f1", ""),
    ("6000.4.8f1", ""),
    ("6000.3.25f1", ""),
    ("6000.6.4f1", ""),
    ("6000.7.0b2", ""),
    ("6000.7.0a6", ""),
])
def test_matrix_compiles_all_platforms_with_selected_explicit_profile(
    harness: CompileHarness, version: str, profile: str,
) -> None:
    selected = harness.repo / "tools" / "compile-refs" / profile
    selected.mkdir(exist_ok=True)
    reference = harness.data / "Managed" / "Selected.dll"
    reference.touch()
    for assembly in ("Runtime", "Editor"):
        (selected / f"{assembly}.txt").write_text("DATA/Managed/Selected.dll\n", encoding="utf-8")
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run(version, "win osx linux")
    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        for assembly in ("Runtime", "Editor"):
            rsp = (harness.output / platform / f"MCPForUnity.{assembly}.rsp").read_text(encoding="utf-8")
            assert '/Managed/Selected.dll"' in rsp
            assert f"-define:UNITY_EDITOR_{platform.upper()}" in rsp
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 6


@pytest.mark.parametrize("version", ["6000.7.0b2", "6000.7.0a6", "6000.8.0a1"])
def test_prerelease_and_future_minor_defines_remain_numeric(harness: CompileHarness, version: str) -> None:
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    major, minor, _ = version.split(".")
    rsp = (harness.output / "linux" / "MCPForUnity.Runtime.rsp").read_text(encoding="utf-8")
    defines = {line.removeprefix("-define:") for line in rsp.splitlines() if line.startswith("-define:")}
    assert {f"UNITY_{major}_{minor}", f"UNITY_{major}_{minor}_0", f"UNITY_{major}_{minor}_OR_NEWER"} <= defines
    assert "UNITY_6000_6_OR_NEWER" in defines
    assert not any("0a" in flag or "0b" in flag for flag in defines)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash on Windows resolves the dotnet.exe executable suffix")
def test_windows_compiler_layout_uses_bundled_runtime(harness: CompileHarness) -> None:
    shim = harness.data / "NetCoreRuntime" / "dotnet"
    shim.rename(shim.with_suffix(".exe"))
    result = harness.run("2021.3.45f2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 2


def test_shared_bcl_references_are_required(harness: CompileHarness) -> None:
    manifest = harness.repo / "tools" / "compile-refs" / "BCL" / "Runtime.txt"
    manifest.write_text("DATA/Managed/MissingBcl.dll\n", encoding="utf-8")
    result = harness.run("6000.0.75f1")
    assert result.returncode != 0
    assert "MissingBcl.dll" in result.stdout + result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version", ["2021.3.45f2", "2022.3.62f1"])
def test_missing_selected_legacy_manifest_does_not_fall_back(harness: CompileHarness, version: str) -> None:
    major, minor, _ = version.split(".")
    (harness.repo / "tools" / "compile-refs" / f"{major}.{minor}" / "Runtime.txt").unlink()
    result = harness.run(version)
    assert result.returncode != 0
    assert "reference manifest not found:" in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version,expected", [
    ("2021.3.45f2", set()),
    ("2022.3.62f1", set()),
    ("2023.1.20f1", {"UNITY_2023_1_OR_NEWER"}),
    ("2023.2.20f1", {"UNITY_2023_1_OR_NEWER", "UNITY_2023_2_OR_NEWER"}),
    ("6000.0.75f1", {"UNITY_2023_1_OR_NEWER", "UNITY_2023_2_OR_NEWER"}),
])
def test_historical_version_defines_select_the_correct_api_branches(harness: CompileHarness, version: str, expected: set[str]) -> None:
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    for assembly in ("Runtime", "Editor"):
        rsp = (harness.output / "linux" / f"MCPForUnity.{assembly}.rsp").read_text(encoding="utf-8")
        flags = {line.removeprefix("-define:") for line in rsp.splitlines() if line.startswith("-define:UNITY_2023_") and line.endswith("_OR_NEWER")}
        assert flags == expected


@pytest.mark.parametrize("reference", ["DATA/Managed/Missing.dll", "EXTRA/Missing.dll", "LIBCACHE/Missing.dll"])
@pytest.mark.parametrize("version,profile", [("6000.0.75f1", ""), ("2021.3.45f2", "2021.3"), ("2022.3.62f1", "2022.3")])
def test_missing_required_reference_fails_before_invoking_compiler(harness: CompileHarness, reference: str, version: str, profile: str) -> None:
    manifest = harness.repo / "tools" / "compile-refs" / profile / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + reference + "\n", encoding="utf-8")
    result = harness.run(version)
    assert result.returncode != 0
    assert "::error::" in result.stderr + result.stdout
    assert reference in result.stderr + result.stdout
    assert not harness.calls.exists()


def test_existing_reference_is_preserved_in_both_compilations(harness: CompileHarness) -> None:
    result = harness.run("6000.0.75f1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" not in result.stderr + result.stdout
    for assembly in ("Runtime", "Editor"):
        rsp = (harness.output / "linux" / f"MCPForUnity.{assembly}.rsp").read_text(encoding="utf-8")
        assert '/Managed/Core.dll"' in rsp
        assert '/Fixture.cs"' in rsp


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_portable_manifests_do_not_require_removed_or_optional_modules(name: str) -> None:
    references = set((ROOT / "tools" / "compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    assert not references & STALE_REFERENCES


@pytest.fixture
def staged_tests(harness: CompileHarness) -> tuple[Path, Path]:
    project, framework = harness.repo / "staged project", harness.repo / "framework"
    for relative, asmdef in (("Assets/Scripts/TestAsmdef", "TestAsmdef.asmdef"),
                             ("Assets/Tests/EditMode", "MCPForUnityTests.Editor.asmdef")):
        directory = project / relative
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}", encoding="utf-8")
        (directory / asmdef).write_text("{}", encoding="utf-8")
    for assembly in ("UnityEngine.TestRunner", "UnityEditor.TestRunner"):
        directory = framework / assembly
        directory.mkdir(parents=True)
        (directory / "Fixture.cs").write_text("class Fixture {}", encoding="utf-8")
    editor = harness.repo / "tools/compile-refs/Editor.txt"
    editor.write_text(editor.read_text(encoding="utf-8") + "LIBCACHE/UnityEngine.TestRunner.dll\nLIBCACHE/UnityEditor.TestRunner.dll\n", encoding="utf-8")
    cecil = harness.data / "Tools/Compilation/ApiUpdater"
    cecil.mkdir(parents=True)
    for name in ("Mono.Cecil.dll", "Mono.Cecil.Pdb.dll", "Mono.Cecil.Mdb.dll", "Mono.Cecil.Rocks.dll"):
        (cecil / name).touch()
    return project, framework


def test_explicit_staged_project_compiles_fixture_and_editmode_all_platforms(
    harness: CompileHarness, staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run("6000.7.0b2", "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 18
    for platform in ("win", "osx", "linux"):
        rsp = (harness.output / platform / "MCPForUnityTests.EditMode.rsp").read_text(encoding="utf-8")
        assert "/Assets/Tests/EditMode/Fixture.cs" in rsp
        for name in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "TestAsmdef", "UnityEngine.TestRunner", "UnityEditor.TestRunner"):
            assert f'/{name}.dll"' in rsp


@pytest.mark.parametrize("missing", ["Assets/Scripts/TestAsmdef", "Assets/Tests/EditMode"])
def test_explicit_staged_project_missing_sources_fails_closed(
    harness: CompileHarness, staged_tests: tuple[Path, Path], missing: str,
) -> None:
    project, framework = staged_tests
    (project / missing / "Fixture.cs").unlink()
    result = harness.run("6000.7.0a6", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "source" in result.stderr.lower()
    assert not harness.calls.exists()


def test_staged_project_missing_assembly_definition_fails_before_compiler(
    harness: CompileHarness, staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    (project / "Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef").unlink()
    result = harness.run("6000.7.0a6", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "assembly definition not found" in result.stderr
    assert not harness.calls.exists()


def test_missing_owned_assembly_does_not_report_editmode_success(
    harness: CompileHarness, staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    compiler = harness.data / "NetCoreRuntime/dotnet"
    compiler.write_text(compiler.read_text(encoding="utf-8").replace('touch "$output"',
                        'case "$output" in *TestAsmdef.dll) ;; *) touch "$output" ;; esac'), encoding="utf-8")
    result = harness.run("6000.7.0b2", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "TestAsmdef failed to compile" in result.stdout
    assert "MCPForUnityTests.EditMode" not in harness.calls.read_text(encoding="utf-8")


def test_staged_assembly_contract_matches_owned_asmdefs() -> None:
    project = ROOT / "TestProjects/UnityMCPTests"
    fixture = json.loads((project / "Assets/Scripts/TestAsmdef/TestAsmdef.asmdef").read_text(encoding="utf-8"))
    tests = json.loads((project / "Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef").read_text(encoding="utf-8"))
    assert fixture["name"] == "TestAsmdef"
    assert fixture["references"] == fixture["defineConstraints"] == fixture["versionDefines"] == []
    assert fixture["includePlatforms"] == fixture["excludePlatforms"] == []
    assert not fixture["overrideReferences"]
    assert not fixture["noEngineReferences"]
    assert tests["name"] == "MCPForUnityTests.EditMode"
    assert set(tests["references"]) == {"MCPForUnity.Editor", "MCPForUnity.Runtime", "TestAsmdef",
                                        "UnityEngine.TestRunner", "UnityEditor.TestRunner"}
    assert tests["defineConstraints"] == ["UNITY_INCLUDE_TESTS"]
    assert tests["versionDefines"] == []
    assert tests["includePlatforms"] == ["Editor"]
    assert tests["overrideReferences"]
    assert set(tests["precompiledReferences"]) == {"nunit.framework.dll", "Newtonsoft.Json.dll"}
    assert "UNITY_INCLUDE_TESTS" in (ROOT / "tools/compile-defines.txt").read_text(encoding="utf-8").splitlines()
