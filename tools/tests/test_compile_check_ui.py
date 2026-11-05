"""Optional uGUI and Input System assembly compilation contracts."""

import json
import os

from pathlib import Path

import shlex
import subprocess

import shutil

import pytest
import yaml

from .compile_check_helpers import (
    CompileHarness,
    INPUT_SYSTEM_ASSEMBLIES,
    OPTIONAL_ASSEMBLIES,
    OWNED_ASSEMBLIES,
    ROOT,
    UGUI_ASSEMBLIES,
    harness,
    input_system_sources,
    staged_tests,
    ugui_sources,
)


@pytest.mark.parametrize(
    "version", ["2021.3.45f2", "2022.3.62f1", "6000.0.69f1", "6000.6.4f1", "7000.0.0a7"]
)
def test_enabled_ugui_compiles_separate_assemblies_in_dependency_order(
    harness: CompileHarness, ugui_sources: tuple[Path, Path], version: str
) -> None:
    # Given nested uGUI asmdefs, real asmdef dependencies, and the selected Unity profile.
    project, framework = ugui_sources
    # When optional uGUI compilation is explicitly selected.
    result = harness.run(version, test_project=project, framework=framework, ugui=True)
    # Then the runtime adapter, Editor wrapper, and optional tests are distinct consumers.
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    integration_runtime = "MCPForUnity.PlayScenarios.Integration.Runtime"
    integration_tests = "MCPForUnity.PlayScenarios.Integration.Tests"
    assert calls.index("MCPForUnity.Runtime") < calls.index(integration_runtime)
    assert calls.index(integration_runtime) < calls.index(integration_tests)
    integration_rsp = (harness.output / "linux" / f"{integration_tests}.rsp").read_text(
        encoding="utf-8"
    )
    assert f'/{integration_runtime}.dll"' in integration_rsp
    fixture = "MCPForUnity.ScenarioPlayerFixture"
    fixture_editor = "MCPForUnity.ScenarioPlayerFixture.Editor"
    assert calls.index("MCPForUnity.Runtime") < calls.index(fixture) < calls.index(fixture_editor)
    fixture_rsp = (harness.output / "linux" / f"{fixture_editor}.rsp").read_text(encoding="utf-8")
    assert f'/{fixture}.dll"' in fixture_rsp
    for profile in ("player", "player-scenarios"):
        response = (harness.output / "linux" / profile / f"{fixture}.rsp").read_text(
            encoding="utf-8"
        )
        assert f'/{profile}/MCPForUnity.Runtime.dll"' in response
        assert "-define:UNITY_EDITOR" not in response
        assert ("-define:MCP_FOR_UNITY_PLAY_SCENARIOS" in response) == (
            profile == "player-scenarios"
        )
        assert ("CS0649" in response) == (profile == "player-scenarios")
    ordered = (
        "MCPForUnity.Runtime",
        UGUI_ASSEMBLIES[0],
        "MCPForUnity.Editor",
        UGUI_ASSEMBLIES[1],
        "MCPForUnityTests.EditMode",
        UGUI_ASSEMBLIES[2],
    )
    assert [calls.index(name) for name in ordered] == sorted(calls.index(name) for name in ordered)
    responses = {
        name: (harness.output / "linux" / f"{name}.rsp").read_text(encoding="utf-8")
        for name in ordered
    }
    for parent in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "MCPForUnityTests.EditMode"):
        assert "/UGUI/Fixture.cs" not in responses[parent]
        assert "/Excluded.cs" not in responses[parent]
        assert "-define:MCP_INPUT_UGUI" not in responses[parent]
    for assembly in UGUI_ASSEMBLIES:
        response = responses[assembly]
        assert "/UGUI/Fixture.cs" in response
        assert "-define:MCP_INPUT_UGUI" in response.splitlines()
        assert "-warnaserror+" in response.splitlines()
        assert '/UnityEngine.UI.dll"' in response
    for assembly, dependencies in (
        (UGUI_ASSEMBLIES[0], ("MCPForUnity.Runtime",)),
        (UGUI_ASSEMBLIES[1], ("MCPForUnity.Runtime", "MCPForUnity.Editor", UGUI_ASSEMBLIES[0])),
        (
            UGUI_ASSEMBLIES[2],
            ("MCPForUnity.Runtime", "MCPForUnity.Editor", UGUI_ASSEMBLIES[0], UGUI_ASSEMBLIES[1]),
        ),
    ):
        for dependency in dependencies:
            assert f'/linux/{dependency}.dll"' in responses[assembly]
    assert '/UnityEngine.TestRunner.dll"' in responses[UGUI_ASSEMBLIES[2]]
    assert '/UnityEditor.TestRunner.dll"' in responses[UGUI_ASSEMBLIES[2]]
    assert set(OPTIONAL_ASSEMBLIES) <= set(calls)


def test_disabled_ugui_keeps_nested_sources_out_of_parent_assemblies(
    harness: CompileHarness, ugui_sources: tuple[Path, Path]
) -> None:
    # Given optional nested assemblies installed in both package and test trees.
    project, framework = ugui_sources
    # When the existing default compilation path runs.
    result = harness.run("6000.0.69f1", test_project=project, framework=framework)
    # Then optional types stay excluded and every existing owned assembly still compiles.
    assert result.returncode == 0, result.stdout + result.stderr
    calls = {Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()}
    assert set(OWNED_ASSEMBLIES) <= calls
    assert not calls.intersection(UGUI_ASSEMBLIES)
    for assembly in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "MCPForUnityTests.EditMode"):
        response = (harness.output / "linux" / f"{assembly}.rsp").read_text(encoding="utf-8")
        assert "/UGUI/Fixture.cs" not in response
        assert "/Excluded.cs" not in response
        assert "-define:MCP_INPUT_UGUI" not in response


@pytest.mark.parametrize("version", ["2021.3.45f2", "2022.3.76f1", "6000.3.25f1"])
def test_ugui_test_compiler_receives_current_asmdef_dependencies_on_all_platforms(
    harness: CompileHarness, ugui_sources: tuple[Path, Path], version: str
) -> None:
    project, framework = ugui_sources
    runtime_directory = harness.repo / "MCPForUnity/Runtime/PlayScenarios/UGUI"
    observer = "UguiScenarioRecordingObserver.cs"
    shutil.copy2(ROOT / "MCPForUnity/Runtime/PlayScenarios/UGUI" / observer, runtime_directory)
    definition = json.loads(
        (
            project / "Assets/Tests/EditMode/Tools/Input/UGUI" / f"{UGUI_ASSEMBLIES[2]}.asmdef"
        ).read_text(encoding="utf-8")
    )
    required = {
        reference for reference in definition["references"] if reference.startswith("MCPForUnity.")
    }
    result = harness.run(
        version, "win osx linux", test_project=project, framework=framework, ugui=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        runtime_response = (harness.output / platform / f"{UGUI_ASSEMBLIES[0]}.rsp").read_text(
            encoding="utf-8"
        )
        test_response = (harness.output / platform / f"{UGUI_ASSEMBLIES[2]}.rsp").read_text(
            encoding="utf-8"
        )
        assert f"/UGUI/{observer}" in runtime_response
        assert f"/UGUI/{observer}" not in test_response
        assert "-define:MCP_INPUT_UGUI" in runtime_response.splitlines()
        for reference in required:
            assert (
                f'-r:"{harness.output.as_posix()}/{platform}/{reference}.dll"'
                in test_response.splitlines()
            )


@pytest.mark.parametrize("missing", UGUI_ASSEMBLIES)
def test_enabled_ugui_missing_asmdef_fails_before_compiler(
    harness: CompileHarness, ugui_sources: tuple[Path, Path], missing: str
) -> None:
    # Given an explicitly selected optional assembly with missing assembly metadata.
    project, framework = ugui_sources
    roots = (harness.repo / "MCPForUnity", project / "Assets")
    definition = next(path for root in roots for path in root.rglob(f"{missing}.asmdef"))
    definition.unlink()
    # When the harness validates optional compilation prerequisites.
    result = harness.run("6000.0.69f1", test_project=project, framework=framework, ugui=True)
    # Then it fails honestly before any compile pass can create partial evidence.
    assert result.returncode == 2
    assert missing in result.stderr
    assert not harness.calls.exists()


def test_missing_ugui_runtime_output_blocks_all_downstream_consumers(
    harness: CompileHarness, ugui_sources: tuple[Path, Path]
) -> None:
    # Given a compiler that fails to produce the selected optional runtime output.
    project, framework = ugui_sources
    compiler = harness.data / "NetCoreRuntime/dotnet"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            ': > "$output"',
            f'case "$output" in *{UGUI_ASSEMBLIES[0]}.dll) ;; *) : > "$output" ;; esac',
        ),
        encoding="utf-8",
    )
    # When the dependency graph reaches the optional runtime pass.
    result = harness.run("6000.0.69f1", test_project=project, framework=framework, ugui=True)
    # Then neither Editor nor optional test consumers receive a nonexistent runtime.
    assert result.returncode == 1
    calls = {Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()}
    assert UGUI_ASSEMBLIES[0] in calls
    assert "MCPForUnity.Editor" not in calls
    assert UGUI_ASSEMBLIES[1] not in calls
    assert UGUI_ASSEMBLIES[2] not in calls


@pytest.mark.parametrize("version", ["2021.3.45f2", "6000.0.69f1", "7000.0.0a7"])
def test_optional_ugui_uses_runtime_bcl_and_respects_editor_profile(
    harness: CompileHarness, ugui_sources: tuple[Path, Path], version: str
) -> None:
    # Given distinguishable Runtime and Editor BCL manifests.
    project, framework = ugui_sources
    for name in ("Runtime", "Editor"):
        reference = harness.data / "Managed" / f"{name}Bcl.dll"
        reference.touch()
        manifest = harness.repo / "tools/compile-refs/BCL" / f"{name}.txt"
        manifest.write_text(f"DATA/Managed/{name}Bcl.dll\n", encoding="utf-8")
    # When the selected optional assemblies compile against a version-specific profile.
    result = harness.run(version, test_project=project, framework=framework, ugui=True)
    # Then Runtime always uses the portable API surface, including on older Editors.
    assert result.returncode == 0, result.stdout + result.stderr
    for assembly in UGUI_ASSEMBLIES:
        response = (harness.output / "linux" / f"{assembly}.rsp").read_text(encoding="utf-8")
        runtime_surface = assembly == UGUI_ASSEMBLIES[0] or version.startswith("7000.")
        assert ('/RuntimeBcl.dll"' in response) == runtime_surface
        assert ('/EditorBcl.dll"' in response) != runtime_surface


def test_license_free_ci_enables_separate_ugui_compile_passes() -> None:
    # Given the CI step that invokes the license-free compiler in Docker.
    workflow = (ROOT / ".github/workflows/compile-check.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: Compile\n", 1)[1].split("\n      - name:", 1)[0]
    # When its real shell argument list is parsed without executing the container.
    arguments = shlex.split(step.split("        run: |\n", 1)[1])
    # Then the optional compile flag belongs to Docker's environment for this script.
    selected = arguments.index("COMPILE_INPUT_UGUI=1")
    assert arguments[selected - 1] == "-e"
    assert arguments[-1] == "/repo/tools/compile-check.sh"
    assert arguments.index("docker") < selected < arguments.index("$UNITY_IMAGE")


@pytest.mark.parametrize("version,selected", [("6000.0.84f1", True), ("6000.3.25f1", False)])
def test_workflow_prepares_profile_sources_and_passes_them_to_the_compiler(
    harness: CompileHarness, version: str, selected: bool
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/compile-check.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["compile"]["steps"]
    preparation = next(step for step in steps if step.get("id") == "packages")
    capture = harness.repo / "arguments.txt"
    environment = {
        **os.environ,
        "UNITY_VERSION": version,
        "UNITY_DATA": "verified/editor",
        "CAPTURE": capture.as_posix(),
    }
    result = subprocess.run(
        [
            harness.bash,
            "-c",
            'python3() { printf \'%s\\n\' "$@" > "$CAPTURE"; };\n' + preparation["run"],
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "--include-input-system" in capture.read_text(encoding="utf-8").splitlines()
    ) == selected
    compiler = next(step for step in steps if step.get("name") == "Compile")
    assert (
        compiler["env"]["INPUT_SYSTEM_SOURCE"]
        == "${{ steps.packages.outputs.input_system_source }}"
    )
    assert (
        compiler["env"]["COMPILE_INPUT_SYSTEM"]
        == "${{ startsWith(matrix.unity.version, '6000.0.') && '1' || '0' }}"
    )
    environment.update(
        {
            "INPUT_SYSTEM_SOURCE": "verified/input-package",
            "COMPILE_INPUT_SYSTEM": "1" if selected else "0",
            "UNITY_IMAGE": "verified/image",
            "EXTRA_REFS": "verified/refs",
            "TEST_FRAMEWORK_SOURCE": "verified/test-framework",
            "EDITOR_COROUTINES_SOURCE": "verified/coroutines",
            "TEST_PROJECT": "isolated/project",
        }
    )
    result = subprocess.run(
        [
            harness.bash,
            "-c",
            'docker() { printf \'%s\\n\' "$@" > "$CAPTURE"; return 7; };\n' + compiler["run"],
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 7
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert f"COMPILE_INPUT_SYSTEM={1 if selected else 0}" in arguments
    assert "INPUT_SYSTEM_SOURCE=/repo/verified/input-package" in arguments
    assert arguments[-1] == "/repo/tools/compile-check.sh"


@pytest.mark.parametrize("version", ["2021.3.45f2", "6000.0.69f1", "7000.0.0a7"])
def test_selected_input_system_compiles_runtime_editor_and_tests_in_order(
    harness: CompileHarness, input_system_sources: tuple[Path, Path], version: str
) -> None:
    project, framework = input_system_sources
    result = harness.run(version, test_project=project, framework=framework, input_system=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    order = (
        "MCPForUnity.Runtime",
        INPUT_SYSTEM_ASSEMBLIES[0],
        "MCPForUnity.Editor",
        INPUT_SYSTEM_ASSEMBLIES[1],
        "MCPForUnityTests.EditMode",
        INPUT_SYSTEM_ASSEMBLIES[2],
    )
    assert [calls.index(name) for name in order] == sorted(calls.index(name) for name in order)
    for name in INPUT_SYSTEM_ASSEMBLIES:
        response = (harness.output / "linux" / f"{name}.rsp").read_text(encoding="utf-8")
        assert "/InputSystem/Fixture.cs" in response
        assert "-define:MCP_INPUT_SYSTEM" in response.splitlines()
        assert "-define:ENABLE_INPUT_SYSTEM" in response.splitlines()
        assert "-warnaserror+" in response.splitlines()
        assert '/Unity.InputSystem.dll"' in response
        assert '/MCPForUnity.Runtime.dll"' in response
    for name in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "MCPForUnityTests.EditMode"):
        response = (harness.output / "linux" / f"{name}.rsp").read_text(encoding="utf-8")
        assert "/InputSystem/Fixture.cs" not in response
        assert "-define:MCP_INPUT_SYSTEM" not in response
    tests = (harness.output / "linux" / f"{INPUT_SYSTEM_ASSEMBLIES[2]}.rsp").read_text(
        encoding="utf-8"
    )
    assert '/Unity.InputSystem.TestFramework.dll"' in tests
    assert '/MCPForUnity.Input.InputSystem.Runtime.dll"' in tests


def test_input_system_absent_path_excludes_optional_sources(
    harness: CompileHarness, input_system_sources: tuple[Path, Path]
) -> None:
    project, framework = input_system_sources
    result = harness.run("6000.0.69f1", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = {Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()}
    assert not calls.intersection(INPUT_SYSTEM_ASSEMBLIES)
    for name in ("MCPForUnity.Runtime", "MCPForUnity.Editor", "MCPForUnityTests.EditMode"):
        response = (harness.output / "linux" / f"{name}.rsp").read_text(encoding="utf-8")
        assert "/InputSystem/Fixture.cs" not in response
        assert '/Unity.InputSystem.dll"' not in response


@pytest.mark.parametrize(
    "missing",
    INPUT_SYSTEM_ASSEMBLIES + ("Unity.InputSystem.dll", "Unity.InputSystem.TestFramework.dll"),
)
def test_selected_input_system_rejects_missing_assembly_metadata_and_references(
    harness: CompileHarness, input_system_sources: tuple[Path, Path], missing: str
) -> None:
    project, framework = input_system_sources
    if missing.endswith(".dll"):
        (harness.extra / missing).unlink()
    else:
        next(
            path for root in (harness.repo, project) for path in root.rglob(f"{missing}.asmdef")
        ).unlink()
    result = harness.run(
        "6000.0.69f1", test_project=project, framework=framework, input_system=True
    )
    assert result.returncode == 2
    assert missing in result.stderr
    assert not harness.calls.exists()


def test_input_system_runtime_compile_failure_blocks_adapter_consumers(
    harness: CompileHarness, input_system_sources: tuple[Path, Path]
) -> None:
    project, framework = input_system_sources
    compiler = harness.data / "NetCoreRuntime/dotnet"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            ': > "$output"',
            f'case "$output" in *{INPUT_SYSTEM_ASSEMBLIES[0]}.dll) ;; *) : > "$output" ;; esac',
        ),
        encoding="utf-8",
    )
    result = harness.run(
        "6000.0.69f1", test_project=project, framework=framework, input_system=True
    )
    assert result.returncode == 1
    calls = {Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()}
    assert INPUT_SYSTEM_ASSEMBLIES[0] in calls
    assert INPUT_SYSTEM_ASSEMBLIES[1] not in calls
    assert INPUT_SYSTEM_ASSEMBLIES[2] not in calls


@pytest.mark.parametrize("failure", [None, "Unity.InputSystem", "Unity.InputSystem.TestFramework"])
def test_verified_package_sources_build_references_before_optional_consumers(
    harness: CompileHarness, input_system_sources: tuple[Path, Path], failure: str | None
) -> None:
    project, framework = input_system_sources
    package = harness.repo / "verified package"
    for relative, name in (
        ("InputSystem", "Unity.InputSystem"),
        ("Tests/TestFixture", "Unity.InputSystem.TestFramework"),
    ):
        directory = package / relative
        directory.mkdir(parents=True)
        (directory / f"{name}.asmdef").write_text("{}", encoding="utf-8")
        (directory / "PackageSource.cs").write_text("class PackageSource {}", encoding="utf-8")
    nested = package / "InputSystem/Nested"
    nested.mkdir()
    (nested / "Separate.asmdef").write_text("{}", encoding="utf-8")
    (nested / "Excluded.cs").write_text("#error Separate assembly", encoding="utf-8")
    if failure:
        compiler = harness.data / "NetCoreRuntime/dotnet"
        compiler.write_text(
            compiler.read_text(encoding="utf-8").replace(
                ': > "$output"',
                f'case "$output" in *{failure}.dll) exit 7 ;; *) : > "$output" ;; esac',
            ),
            encoding="utf-8",
        )
    result = harness.run(
        "6000.0.69f1",
        test_project=project,
        framework=framework,
        input_system=True,
        input_system_source=package,
    )
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    if failure:
        assert result.returncode == 1, result.stdout + result.stderr
        assert failure in calls
        assert INPUT_SYSTEM_ASSEMBLIES[0] not in calls
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.index("Unity.InputSystem") < calls.index("Unity.InputSystem.TestFramework")
    assert calls.index("Unity.InputSystem.TestFramework") < calls.index(INPUT_SYSTEM_ASSEMBLIES[0])
    response = (harness.output / "linux/Unity.InputSystem.rsp").read_text(encoding="utf-8")
    assert "PackageSource.cs" in response and "Excluded.cs" not in response
    assert "-unsafe+" in response
    consumer = (harness.output / f"linux/{INPUT_SYSTEM_ASSEMBLIES[0]}.rsp").read_text(
        encoding="utf-8"
    )
    assert f'-r:"{harness.output.as_posix()}/linux/Unity.InputSystem.dll"' in consumer
