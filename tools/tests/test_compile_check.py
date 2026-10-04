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
ROSLYN_DIRECTORY = "MonoBleedingEdge/lib/mono/4.5"
ROSLYN_REFERENCES = ("Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.CSharp.dll",
                     "System.Collections.Immutable.dll", "System.Reflection.Metadata.dll")
OPTIONAL_ASSEMBLIES = ("MCPForUnity.CustomTools.RoslynOff", "MCPForUnity.CustomTools.RoslynOn")
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
    optional = repo / "CustomTools/RoslynRuntimeCompilation"
    optional.mkdir(parents=True)
    for name in ("RoslynRuntimeCompiler.cs", "ManageRuntimeCompilation.cs"):
        (optional / name).write_text("class Fixture {}\n", encoding="utf-8")
    roslyn = data / ROSLYN_DIRECTORY
    roslyn.mkdir(parents=True)
    for name in ROSLYN_REFERENCES:
        (roslyn / name).touch()
    bcl = repo / "tools" / "compile-refs" / "BCL"
    bcl.mkdir()
    for name in ("Runtime", "Editor"):
        (bcl / f"{name}.txt").write_text("", encoding="utf-8")
    for version in ("2021.3", "2022.3", "6000.3", "6000.6", "6000.7"):
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
    result = CompileHarness(repo, data, extra, tmp_path / "output", tmp_path / "calls.txt", bash)
    modern_sdk(result)
    return result


@pytest.mark.parametrize("version,profile", [
    ("2021.3.45f2", "2021.3"),
    ("2022.3.62f1", "2022.3"),
    ("2022.3.76f1", "2022.3"),
    ("6000.0.75f1", ""),
    ("6000.0.84f1", ""),
    ("6000.4.8f1", ""),
    ("6000.3.25f1", "6000.3"),
    ("6000.6.4f1", "6000.6"),
    ("6000.7.0b2", "6000.7"),
    ("6000.7.0a6", "6000.7"),
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
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 12


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
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 4


def test_missing_compiler_reports_distribution_candidates_without_selecting_them(harness: CompileHarness) -> None:
    (harness.data / "DotNetSdkRoslyn" / "csc.dll").unlink()
    candidate = harness.data / "Tools" / "Scripting" / "DotNetSdk" / "sdk" / "9.0.100" / "Roslyn" / "bincore" / "csc.dll"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.touch()
    result = harness.run("6000.0.84f1")
    assert result.returncode == 2
    assert "Compiler distribution candidates (diagnostic only):" in result.stderr
    assert "Tools/Scripting/DotNetSdk/sdk/9.0.100/Roslyn/bincore/csc.dll" in result.stderr
    assert "NetCoreRuntime/dotnet" in result.stderr
    assert not harness.calls.exists()


def modern_sdk(harness: CompileHarness, root: str = "Tools/Scripting/DotNetSdk", version: str = "9.0.100") -> Path:
    sdk = harness.data / root
    compiler = sdk / "sdk" / version / "Roslyn" / "bincore" / "csc.dll"
    compiler.parent.mkdir(parents=True, exist_ok=True)
    compiler.touch()
    sdk.mkdir(exist_ok=True)
    shutil.copy2(harness.data / "NetCoreRuntime" / "dotnet", sdk / "dotnet")
    return sdk


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_modern_sdk_uses_one_coherent_bundled_toolchain(harness: CompileHarness, version: str) -> None:
    modern_sdk(harness)
    (harness.data / "DotNetSdkRoslyn" / "csc.dll").unlink()
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Compiler      : " in result.stdout
    assert "Tools/Scripting/DotNetSdk/sdk/9.0.100/Roslyn/bincore/csc.dll" in result.stdout
    assert "Runtime       : " in result.stdout
    assert "Tools/Scripting/DotNetSdk/dotnet" in result.stdout
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 4


@pytest.mark.parametrize("failure", ["absent", "compiler", "runtime", "two_roots", "two_versions", "partial_version", "two_runtimes"])
def test_modern_sdk_rejects_missing_or_ambiguous_components(harness: CompileHarness, failure: str) -> None:
    shutil.rmtree(harness.data / "Tools/Scripting/DotNetSdk")
    if failure != "absent":
        sdk = modern_sdk(harness)
        if failure == "compiler":
            (sdk / "sdk/9.0.100/Roslyn/bincore/csc.dll").unlink()
        elif failure == "runtime":
            (sdk / "dotnet").unlink()
        elif failure == "two_roots":
            modern_sdk(harness, "DotNetSdk")
        elif failure == "two_versions":
            modern_sdk(harness, version="9.0.101")
        elif failure == "partial_version":
            (sdk / "sdk/9.0.101").mkdir()
        elif failure == "two_runtimes":
            shutil.copy2(sdk / "dotnet", sdk / "dotnet.exe")
    result = harness.run("6000.6.4f1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "SDK" in result.stderr
    assert not harness.calls.exists()


def test_modern_sdk_supports_explicit_executable_suffix(harness: CompileHarness) -> None:
    sdk = harness.data / "Tools/Scripting/DotNetSdk"
    (sdk / "dotnet").rename(sdk / "dotnet.exe")
    result = harness.run("6000.6.4f1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 4


def test_modern_sdk_rejects_external_symlinks(harness: CompileHarness) -> None:
    sdk = modern_sdk(harness)
    target = sdk / "sdk/9.0.100/Roslyn/bincore/csc.dll"
    target.unlink()
    try:
        target.symlink_to(harness.data.parent / "outside.dll")
    except OSError:
        pytest.skip("host does not permit creation of symlinks")
    result = harness.run("6000.6.4f1")
    assert result.returncode == 2
    assert "symlink" in result.stderr
    assert not harness.calls.exists()


def test_shared_bcl_references_are_required(harness: CompileHarness) -> None:
    manifest = harness.repo / "tools" / "compile-refs" / "BCL" / "Runtime.txt"
    manifest.write_text("DATA/Managed/MissingBcl.dll\n", encoding="utf-8")
    result = harness.run("6000.0.75f1")
    assert result.returncode != 0
    assert "MissingBcl.dll" in result.stdout + result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version", ["2021.3.45f2", "2022.3.62f1", "6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
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


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity63_profile_removes_only_confirmed_absent_test_protocol_module(name: str) -> None:
    root = ROOT / "tools/compile-refs"
    default = set((root / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    selected = set((root / "6000.3" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    assert default - selected == {"DATA/Managed/UnityEngine/UnityEngine.UnityTestProtocolModule.dll"}
    assert not selected - default


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity66_profile_removes_only_compiler_proven_absent_modules(name: str) -> None:
    default = set((ROOT / "tools/compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    selected = set((ROOT / "tools/compile-refs/6000.6" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    assert default - selected == {
        f"DATA/Managed/UnityEngine/UnityEngine.{module}Module.dll"
        for module in ("SharedInternals", "UnityTestProtocol", "VR")
    }
    assert selected - default == {"DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll"}


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_current_scripting_module_is_required_metadata(harness: CompileHarness, version: str) -> None:
    entry = "DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll"
    family = ".".join(version.split(".")[:2])
    manifest = harness.repo / "tools/compile-refs" / family / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")
    result = harness.run(version)
    assert result.returncode != 0
    assert entry in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity67_profile_removes_only_beta_compiler_proven_absent_modules(name: str) -> None:
    default = set((ROOT / "tools/compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    selected = set((ROOT / "tools/compile-refs/6000.7" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    assert default - selected == {
        f"DATA/Managed/UnityEngine/UnityEngine.{module}Module.dll"
        for module in ("AR", "SharedInternals", "Substance", "UnityTestProtocol", "VR")
    }
    assert selected - default == {
        "DATA/Managed/UnityEngine/UnityEngine.ManagedKernelModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.UICommonModule.dll",
    }


@pytest.mark.parametrize("version", ["6000.7.0b2", "6000.7.0a6"])
@pytest.mark.parametrize("name", ["Runtime", "Editor"])
@pytest.mark.parametrize("module", ["UICommon", "ManagedKernel"])
def test_preview_split_api_module_is_required_metadata(
    harness: CompileHarness, version: str, name: str, module: str,
) -> None:
    entry = f"DATA/Managed/UnityEngine/UnityEngine.{module}Module.dll"
    profile = Path("tools/compile-refs/6000.7") / f"{name}.txt"
    assert entry in (ROOT / profile).read_text(encoding="utf-8").splitlines()
    manifest = harness.repo / profile
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")
    result = harness.run(version)
    assert result.returncode != 0
    assert entry in result.stderr
    calls = harness.calls.read_text(encoding="utf-8").splitlines() if harness.calls.exists() else []
    assert len(calls) == (0 if name == "Runtime" else 1)


@pytest.mark.parametrize("family,module", [
    ("2021.3", "UnityEngine.TextRenderingModule.dll"),
    ("2022.3", "UnityEngine.TextRenderingModule.dll"),
    ("2021.3", "UnityEngine.UnityAnalyticsCommonModule.dll"),
])
def test_legacy_profiles_include_compiler_proven_vendor_api_modules(family: str, module: str) -> None:
    for name in ("Runtime", "Editor"):
        entries = (ROOT / "tools/compile-refs" / family / f"{name}.txt").read_text(encoding="utf-8").splitlines()
        assert f"DATA/Managed/UnityEngine/{module}" in entries


@pytest.mark.parametrize("family,module", [
    ("2021.3", "UnityEngine.TextRenderingModule.dll"),
    ("2022.3", "UnityEngine.TextRenderingModule.dll"),
    ("2021.3", "UnityEngine.UnityAnalyticsCommonModule.dll"),
])
def test_legacy_vendor_api_module_metadata_is_required(harness: CompileHarness, family: str, module: str) -> None:
    manifest = harness.repo / "tools/compile-refs" / family / "Runtime.txt"
    entry = f"DATA/Managed/UnityEngine/{module}"
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")
    result = harness.run(f"{family}.0f1")
    assert result.returncode != 0
    assert entry in result.stderr
    assert not harness.calls.exists()


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
    refs = harness.repo / "tools/compile-refs"
    for editor in refs.rglob("Editor.txt"):
        if editor.parent.name != "BCL":
            editor.write_text(editor.read_text(encoding="utf-8") + "LIBCACHE/UnityEngine.TestRunner.dll\nLIBCACHE/UnityEditor.TestRunner.dll\n", encoding="utf-8")
    cecil = harness.data / "Tools/Compilation/ApiUpdater"
    cecil.mkdir(parents=True)
    for name in ("Mono.Cecil.dll", "Mono.Cecil.Pdb.dll", "Mono.Cecil.Mdb.dll", "Mono.Cecil.Rocks.dll"):
        (cecil / name).touch()
    for suffix in ("", ".Pdb", ".Mdb", ".Rocks"):
        (harness.data / f"Managed/Unity.Cecil{suffix}.dll").touch()
    return project, framework


def test_explicit_staged_project_compiles_fixture_and_editmode_all_platforms(
    harness: CompileHarness, staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run("6000.7.0b2", "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 24
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
    compiler = harness.data / "Tools/Scripting/DotNetSdk/dotnet"
    compiler.write_text(compiler.read_text(encoding="utf-8").replace('touch "$output"',
                        'case "$output" in *TestAsmdef.dll) ;; *) touch "$output" ;; esac'), encoding="utf-8")
    result = harness.run("6000.7.0b2", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "TestAsmdef failed to compile" in result.stdout
    assert "MCPForUnityTests.EditMode" not in harness.calls.read_text(encoding="utf-8")


def test_missing_cecil_reports_bounded_candidates_without_selecting_them(
    harness: CompileHarness, staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    (harness.data / "Tools/Compilation/ApiUpdater/Mono.Cecil.Mdb.dll").unlink()
    candidate = harness.data / "Tools/ScriptUpdater/Mono.Cecil.Mdb.dll"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    (harness.data / "Managed/Unity.Cecil.dll").touch()
    result = harness.run("6000.0.84f1", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "available Cecil reference candidates" in result.stderr
    assert "Tools/ScriptUpdater/Mono.Cecil.Mdb.dll" in result.stderr
    assert "Managed/Unity.Cecil.dll" in result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("version", ["2021.3.45f2", "2022.3.76f1", "6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_managed_cecil_uses_complete_editor_managed_fork_group(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str,
) -> None:
    project, framework = staged_tests
    result = harness.run(version, test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    rsp = (harness.output / "linux/UnityEditor.TestRunner.rsp").read_text(encoding="utf-8")
    for suffix in ("", ".Pdb", ".Mdb", ".Rocks"):
        assert f'/Managed/Unity.Cecil{suffix}.dll"' in rsp
    assert "/Tools/Compilation/ApiUpdater/Mono.Cecil" not in rsp


@pytest.mark.parametrize("suffix", ["", ".Pdb", ".Mdb", ".Rocks"])
@pytest.mark.parametrize("version", ["2021.3.45f2", "2022.3.76f1", "6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_managed_missing_cecil_component_cannot_fall_back_to_tools_group(
    harness: CompileHarness, staged_tests: tuple[Path, Path], suffix: str, version: str,
) -> None:
    project, framework = staged_tests
    (harness.data / f"Managed/Unity.Cecil{suffix}.dll").unlink()
    result = harness.run(version, test_project=project, framework=framework)
    assert result.returncode != 0
    assert f"Managed/Unity.Cecil{suffix}.dll" in result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 1


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


@pytest.mark.parametrize("version", [row["id"] for row in json.loads(
    (ROOT / "tools/unity-versions.json").read_text(encoding="utf-8"))["versions"]])
def test_optional_examples_complete_matrix_preserves_sources_defines_and_reference_graph(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str,
) -> None:
    project, framework = staged_tests
    # Nested sources must be selected too, without changing any core assembly sources.
    nested = harness.repo / "CustomTools/RoslynRuntimeCompilation/Nested/Additional.cs"
    nested.parent.mkdir()
    nested.write_text("class Additional {}", encoding="utf-8")
    result = harness.run(version, "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    core = ("UnityEngine.TestRunner", "UnityEditor.TestRunner", "MCPForUnity.Runtime", "MCPForUnity.Editor")
    expected = (*core, *OPTIONAL_ASSEMBLIES, "TestAsmdef", "MCPForUnityTests.EditMode")
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    assert calls == list(expected) * 3
    for platform in ("win", "osx", "linux"):
        responses = {name: (harness.output / platform / f"{name}.rsp").read_text(encoding="utf-8").splitlines()
                     for name in expected}
        editor_defines = {line for line in responses["MCPForUnity.Editor"] if line.startswith("-define:")}
        for name in expected:
            lines = responses[name]
            assert ("-define:USE_ROSLYN" in lines) == (name == OPTIONAL_ASSEMBLIES[1])
            roslyn_refs = {line for line in lines if f"/{ROSLYN_DIRECTORY}/" in line}
            if name in OPTIONAL_ASSEMBLIES:
                defines = {line for line in lines if line.startswith("-define:")}
                assert defines == editor_defines | ({"-define:USE_ROSLYN"} if name.endswith("RoslynOn") else set())
                assert {Path(line.removeprefix('-r:').strip('"')).name for line in roslyn_refs} == set(ROSLYN_REFERENCES)
                for owned in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "UnityEngine.TestRunner", "UnityEditor.TestRunner"):
                    assert any(line.endswith(f'/{owned}.dll"') for line in lines)
                sources = {Path(line.strip('"')).relative_to(harness.repo).as_posix()
                           for line in lines if line.startswith('"')}
                assert sources == {f"CustomTools/RoslynRuntimeCompilation/{source}" for source in
                                   ("ManageRuntimeCompilation.cs", "RoslynRuntimeCompiler.cs", "Nested/Additional.cs")}
                assert '-nowarn:CS1701,CS1702' in lines
                assert sum(line.startswith("-nowarn:") for line in lines) == 1
                assert "-warnaserror+" in lines
            else:
                assert not roslyn_refs
                assert not any("CustomTools/" in line for line in lines)
                assert "-warnaserror+" not in lines


@pytest.mark.parametrize("name", ROSLYN_REFERENCES)
def test_optional_missing_required_roslyn_component_cannot_use_other_groups(
    harness: CompileHarness, name: str,
) -> None:
    (harness.data / ROSLYN_DIRECTORY / name).unlink()
    candidate = harness.data / "Tools/Compilation/ApiUpdater" / name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.touch()
    result = harness.run("6000.0.84f1")
    assert result.returncode != 0
    assert f"{ROSLYN_DIRECTORY}/{name}" in result.stderr + result.stdout
    calls = harness.calls.read_text(encoding="utf-8") if harness.calls.exists() else ""
    assert not any(name in calls for name in OPTIONAL_ASSEMBLIES)


@pytest.mark.parametrize("name", OPTIONAL_ASSEMBLIES)
@pytest.mark.parametrize("failure", ["exit_code", "missing_output"])
def test_optional_compiler_failure_is_not_reported_as_success(
    harness: CompileHarness, name: str, failure: str,
) -> None:
    compiler = harness.data / "NetCoreRuntime/dotnet"
    replacement = f'case "$output" in *{name}.dll) {"exit 7" if failure == "exit_code" else ":"} ;; *) touch "$output" ;; esac'
    compiler.write_text(compiler.read_text(encoding="utf-8").replace('touch "$output"', replacement), encoding="utf-8")
    result = harness.run("6000.0.84f1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{name} failed to compile" in result.stdout
    assert "compile check passed" not in result.stdout


@pytest.mark.parametrize("failure", ["directory", "sources"])
def test_optional_sources_are_required(harness: CompileHarness, failure: str) -> None:
    directory = harness.repo / "CustomTools/RoslynRuntimeCompilation"
    for path in directory.iterdir():
        path.unlink()
    if failure == "directory":
        directory.rmdir()
    result = harness.run("6000.0.84f1")
    assert result.returncode == 1
    assert "assembly sources not found:" in result.stderr
    assert "CustomTools/RoslynRuntimeCompilation" in result.stderr


def test_workflow_triggers_optional_sources_and_compile_contract_tests() -> None:
    workflow = (ROOT / ".github/workflows/compile-check.yml").read_text(encoding="utf-8")
    for path in ("CustomTools/RoslynRuntimeCompilation/**/*.cs", "tools/tests/test_compile_check.py",
                 "tools/tests/test_unity_compile_cache.py", "tools/tests/test_unity_compile_cache_diagnostics.py",
                 "tools/tests/test_unity_ci_packages.py", "tools/tests/test_unity_matrix_workflows.py"):
        assert f"- {path}" in workflow
    assert "USE_ROSLYN" in workflow and "off" in workflow and "on" in workflow
