"""Staged Editor and test assembly compilation contracts."""

import json

import os

from pathlib import Path

import shutil

import subprocess

import pytest

from .compile_check_helpers import (
    COROUTINES_ASSEMBLY,
    CompileHarness,
    OWNED_ASSEMBLIES,
    ROOT,
    SCRIPT,
    harness,
    modern_sdk,
    staged_tests,
)


def test_unity7_compiles_bundled_ui_before_all_consumers_without_template_cache(
    harness: CompileHarness, staged_tests: tuple[Path, Path]
) -> None:
    # Given the verified alpha's top-level SDK and actual declared reference manifests.
    project, framework = staged_tests
    shutil.rmtree(harness.data / "Tools/Scripting/DotNetSdk")
    modern_sdk(harness, root="DotNetSdk", version="10.0.303")
    profile = Path("tools/compile-refs/7000.0")
    for name in ("Runtime", "Editor"):
        text = (ROOT / profile / f"{name}.txt").read_text(encoding="utf-8")
        (harness.repo / profile / f"{name}.txt").write_text(text, encoding="utf-8")
        for entry in text.splitlines():
            if entry.startswith("DATA/") or entry.startswith("EXTRA/"):
                prefix, relative = entry.split("/", 1)
                target = (harness.data if prefix == "DATA" else harness.extra) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
    # When all target defines compile, uGUI must come from sources before any consumer.
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run("7000.0.0a7", "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    # Then every platform compiles both UI assemblies and all nine previous assemblies.
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 33
    for platform in ("win", "osx", "linux"):
        for name in ("UnityEngine.UI", "UnityEditor.UI"):
            rsp = (harness.output / platform / f"{name}.rsp").read_text(encoding="utf-8")
            assert "/UGUI/Fixture.cs" in rsp
            assert ("-define:PACKAGE_UITOOLKIT" in rsp) == (name == "UnityEngine.UI")
        editor = (harness.output / platform / "MCPForUnity.Editor.rsp").read_text(encoding="utf-8")
        assert f'/{platform}/UnityEditor.UI.dll"' in editor
        assert f'/{platform}/UnityEngine.UI.dll"' in editor
        assert "Unity.Scripting.dll" in editor
        assert "UnityEngine.EntitiesModule.dll" in editor
    assert not (harness.data / "Resources/PackageManager/ProjectTemplates/libcache").exists()


@pytest.mark.parametrize("version", ["6000.7.0b2", "7000.0.0a7"])
def test_coreclr_netstandard_editor_contract_does_not_change_older_versions(
    harness: CompileHarness, version: str
) -> None:
    # Given distinct framework and standard reference APIs and the shared legacy defines.
    legacy = {"ENABLE_MONO", "PLATFORM_SUPPORTS_MONO", "NET_4_6", "NET_UNITY_4_8"}
    (harness.repo / "tools/compile-defines.txt").write_text("\n".join(sorted(legacy)) + "\n")
    for assembly, reference in (("Editor", "FrameworkApi"), ("Runtime", "StandardApi")):
        (harness.data / "Managed" / f"{reference}.dll").touch()
        (harness.repo / "tools/compile-refs/BCL" / f"{assembly}.txt").write_text(
            f"DATA/Managed/{reference}.dll\n"
        )
    # When compiling either Editor generation, select the matching API and backend contract.
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    editor = (harness.output / "linux/MCPForUnity.Editor.rsp").read_text(encoding="utf-8")
    flags = {
        line.removeprefix("-define:") for line in editor.splitlines() if line.startswith("-define:")
    }
    # Then Unity 7 uses Standard 2.1/CoreCLR and Unity 6 retains its prior framework/Mono setup.
    if version.startswith("7000."):
        assert '/Managed/StandardApi.dll"' in editor
        assert "FrameworkApi.dll" not in editor
        assert not flags & legacy
        assert {
            "ENABLE_CORECLR",
            "NET_STANDARD_2_1",
            "NET_STANDARD",
            "NETSTANDARD2_1",
            "NETSTANDARD",
        } <= flags
    else:
        assert '/Managed/FrameworkApi.dll"' in editor
        assert "StandardApi.dll" not in editor
        assert legacy <= flags
        assert "ENABLE_CORECLR" not in flags


@pytest.mark.parametrize("version", ["6000.0.84f1", "6000.0.85f1", "6000.7.0b3", "7000.0.0a7"])
def test_warning_policy_keeps_owned_warnings_fatal_and_vendor_exceptions_scoped(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str
) -> None:
    # Given repository assemblies and vendor packages on current and adjacent Editor versions.
    project, framework = staged_tests
    result = harness.run(version, test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [Path(line) for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    assert set(OWNED_ASSEMBLIES) <= {path.stem for path in calls}
    # Then every owned assembly rejects warnings without inheriting vendor suppressions.
    for response in calls:
        lines = response.read_text(encoding="utf-8").splitlines()
        assert ("-warnaserror+" in lines) == (response.stem in OWNED_ASSEMBLIES)
        suppressed = {
            code
            for line in lines
            if line.startswith("-nowarn:")
            for code in line.removeprefix("-nowarn:").split(",")
        }
        expected = {"CS1701", "CS1702"}
        if response.stem == "UnityEditor.TestRunner":
            expected |= {"CS0169", "CS0649"}
            if version == "6000.0.84f1":
                expected.add("CS0618")
        assert suppressed == expected, response.stem


def test_explicit_staged_project_compiles_fixture_and_editmode_all_platforms(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run("6000.7.0b2", "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 27
    for platform in ("win", "osx", "linux"):
        rsp = (harness.output / platform / "MCPForUnityTests.EditMode.rsp").read_text(
            encoding="utf-8"
        )
        assert "/Assets/Tests/EditMode/Fixture.cs" in rsp
        for name in (
            "MCPForUnity.Runtime",
            "MCPForUnity.Editor",
            "TestAsmdef",
            "UnityEngine.TestRunner",
            "UnityEditor.TestRunner",
            COROUTINES_ASSEMBLY,
        ):
            assert f'/{name}.dll"' in rsp


@pytest.mark.parametrize("missing", ["package", "Editor", "asmdef", "sources"])
def test_required_editor_coroutines_sources_fail_before_compiler(
    harness: CompileHarness, missing: str
) -> None:
    if missing == "package":
        shutil.rmtree(harness.coroutines)
    elif missing == "Editor":
        shutil.rmtree(harness.coroutines / "Editor")
    elif missing == "asmdef":
        (harness.coroutines / f"Editor/{COROUTINES_ASSEMBLY}.asmdef").unlink()
    else:
        (harness.coroutines / "Editor/Fixture.cs").unlink()
    result = harness.run("2021.3.45f2")
    assert result.returncode == 2
    assert not harness.calls.exists()


def test_editor_coroutines_source_environment_is_required(harness: CompileHarness) -> None:
    env = dict(
        os.environ,
        UNITY_DATA=harness.data.as_posix(),
        REPO=harness.repo.as_posix(),
        EDITOR_COROUTINES_SOURCE="",
        OUT=harness.output.as_posix(),
    )
    result = subprocess.run(
        [harness.bash, SCRIPT.as_posix()], env=env, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "EDITOR_COROUTINES_SOURCE must be set" in result.stderr
    assert not harness.calls.exists()


def test_editor_coroutines_compiles_only_editor_sources_and_is_referenced(
    harness: CompileHarness,
) -> None:
    result = harness.run("2021.3.45f2", "win osx linux")
    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        rsp = (harness.output / platform / f"{COROUTINES_ASSEMBLY}.rsp").read_text(encoding="utf-8")
        assert "/Editor/Fixture.cs" in rsp
        assert "/Tests/" not in rsp
        editor = (harness.output / platform / "MCPForUnity.Editor.rsp").read_text(encoding="utf-8")
        assert f'/{COROUTINES_ASSEMBLY}.dll"' in editor


def test_missing_editor_coroutines_output_blocks_editor_and_tests(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    compiler = harness.data / "Tools/Scripting/DotNetSdk/dotnet"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            ': > "$output"',
            f'case "$output" in *{COROUTINES_ASSEMBLY}.dll) ;; *) : > "$output" ;; esac',
        ),
        encoding="utf-8",
    )
    result = harness.run("6000.7.0b2", test_project=project, framework=framework)
    assert result.returncode != 0
    assert f"{COROUTINES_ASSEMBLY} failed to compile" in result.stdout
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    assert "MCPForUnity.Editor" not in calls
    assert "MCPForUnityTests.EditMode" not in calls


@pytest.mark.parametrize("missing", ["Assets/Scripts/TestAsmdef", "Assets/Tests/EditMode"])
def test_explicit_staged_project_missing_sources_fails_closed(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
    missing: str,
) -> None:
    project, framework = staged_tests
    (project / missing / "Fixture.cs").unlink()
    result = harness.run("6000.7.0a6", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "source" in result.stderr.lower()
    assert not harness.calls.exists()


def test_staged_project_missing_assembly_definition_fails_before_compiler(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    (project / "Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef").unlink()
    result = harness.run("6000.7.0a6", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "assembly definition not found" in result.stderr
    assert not harness.calls.exists()


def test_missing_owned_assembly_does_not_report_editmode_success(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
) -> None:
    project, framework = staged_tests
    compiler = harness.data / "Tools/Scripting/DotNetSdk/dotnet"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            ': > "$output"', 'case "$output" in *TestAsmdef.dll) ;; *) : > "$output" ;; esac'
        ),
        encoding="utf-8",
    )
    result = harness.run("6000.7.0b2", test_project=project, framework=framework)
    assert result.returncode != 0
    assert "TestAsmdef failed to compile" in result.stdout
    assert "MCPForUnityTests.EditMode" not in harness.calls.read_text(encoding="utf-8")


def test_missing_cecil_reports_bounded_candidates_without_selecting_them(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
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


@pytest.mark.parametrize(
    "version",
    ["2021.3.45f2", "2022.3.76f1", "6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"],
)
def test_managed_cecil_uses_complete_editor_managed_fork_group(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
    version: str,
) -> None:
    project, framework = staged_tests
    result = harness.run(version, test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    rsp = (harness.output / "linux/UnityEditor.TestRunner.rsp").read_text(encoding="utf-8")
    for suffix in ("", ".Pdb", ".Mdb", ".Rocks"):
        assert f'/Managed/Unity.Cecil{suffix}.dll"' in rsp
    assert "/Tools/Compilation/ApiUpdater/Mono.Cecil" not in rsp


@pytest.mark.parametrize("suffix", ["", ".Pdb", ".Mdb", ".Rocks"])
@pytest.mark.parametrize(
    "version",
    ["2021.3.45f2", "2022.3.76f1", "6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"],
)
def test_managed_missing_cecil_component_cannot_fall_back_to_tools_group(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
    suffix: str,
    version: str,
) -> None:
    project, framework = staged_tests
    (harness.data / f"Managed/Unity.Cecil{suffix}.dll").unlink()
    result = harness.run(version, test_project=project, framework=framework)
    assert result.returncode != 0
    assert f"Managed/Unity.Cecil{suffix}.dll" in result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 1


def test_staged_assembly_contract_matches_owned_asmdefs() -> None:
    project = ROOT / "TestProjects/UnityMCPTests"
    fixture = json.loads(
        (project / "Assets/Scripts/TestAsmdef/TestAsmdef.asmdef").read_text(encoding="utf-8")
    )
    tests = json.loads(
        (project / "Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["name"] == "TestAsmdef"
    assert fixture["references"] == fixture["defineConstraints"] == fixture["versionDefines"] == []
    assert fixture["includePlatforms"] == fixture["excludePlatforms"] == []
    assert not fixture["overrideReferences"]
    assert not fixture["noEngineReferences"]
    assert tests["name"] == "MCPForUnityTests.EditMode"
    assert set(tests["references"]) == {
        "MCPForUnity.Editor",
        "MCPForUnity.Runtime",
        "TestAsmdef",
        "UnityEngine.TestRunner",
        "UnityEditor.TestRunner",
        COROUTINES_ASSEMBLY,
    }
    editor = json.loads(
        (ROOT / "MCPForUnity/Editor/MCPForUnity.Editor.asmdef").read_text(encoding="utf-8")
    )
    assert COROUTINES_ASSEMBLY in editor["references"]
    assert tests["defineConstraints"] == ["UNITY_INCLUDE_TESTS"]
    ai_module = {
        "name": "com.unity.modules.ai",
        "expression": "0.0.0",
        "define": "MCP_FOR_UNITY_AI_MODULE",
    }
    terrain_module = {
        "name": "com.unity.modules.terrain",
        "expression": "0.0.0",
        "define": "MCP_FOR_UNITY_TERRAIN",
    }
    assert tests["versionDefines"] == [ai_module]
    assert editor["versionDefines"] == [
        {"name": "com.unity.visualeffectgraph", "expression": "0.0.0", "define": "UNITY_VFX_GRAPH"},
        ai_module,
        terrain_module,
    ]
    assert tests["includePlatforms"] == ["Editor"]
    assert tests["overrideReferences"]
    assert set(tests["precompiledReferences"]) == {"nunit.framework.dll", "Newtonsoft.Json.dll"}
    assert {"UNITY_INCLUDE_TESTS", ai_module["define"], terrain_module["define"]} <= set(
        (ROOT / "tools/compile-defines.txt").read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.parametrize("version", ["2021.3.45f2", "6000.0.69f1", "7000.0.0a7"])
def test_owned_module_version_defines_reach_staged_editor_and_test_compilation(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str
) -> None:
    # Given the real owned assembly metadata and captured shared compilation defines.
    project, framework = staged_tests
    definitions = {
        "MCPForUnity.Editor": Path("MCPForUnity/Editor/MCPForUnity.Editor.asmdef"),
        "MCPForUnityTests.EditMode": Path(
            "TestProjects/UnityMCPTests/Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef"
        ),
    }
    expected_module_defines = {
        "MCPForUnity.Editor": {"MCP_FOR_UNITY_AI_MODULE", "MCP_FOR_UNITY_TERRAIN"},
        "MCPForUnityTests.EditMode": {"MCP_FOR_UNITY_AI_MODULE"},
    }
    for name, relative in definitions.items():
        destination = (
            harness.repo / relative
            if name == "MCPForUnity.Editor"
            else project / "Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef"
        )
        shutil.copy2(ROOT / relative, destination)
    shutil.copy2(ROOT / "tools/compile-defines.txt", harness.repo / "tools/compile-defines.txt")
    # When the actual shell harness produces each platform's compiler response files.
    result = harness.run(version, "win osx linux", test_project=project, framework=framework)
    # Then both owned consumers receive their declared module symbol, not just test activation.
    assert result.returncode == 0, result.stdout + result.stderr
    for name, relative in definitions.items():
        definition = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        module_defines = {
            row["define"]
            for row in definition["versionDefines"]
            if row["name"].startswith("com.unity.modules.")
        }
        assert module_defines == expected_module_defines[name]
        for platform in ("win", "osx", "linux"):
            response = (harness.output / platform / f"{name}.rsp").read_text(encoding="utf-8")
            lines = response.splitlines()
            for symbol in module_defines:
                assert lines.count(f"-define:{symbol}") == 1, (name, platform, symbol)
            assert "-define:UNITY_INCLUDE_TESTS" in lines
            assert "-warnaserror+" in lines
