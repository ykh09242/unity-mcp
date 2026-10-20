"""Optional Roslyn examples and compiler failure contracts."""

import json

from pathlib import Path

import pytest

from .compile_check_helpers import (
    COROUTINES_ASSEMBLY,
    CompileHarness,
    OPTIONAL_ASSEMBLIES,
    OWNED_ASSEMBLIES,
    ROOT,
    ROSLYN_DIRECTORY,
    ROSLYN_REFERENCES,
    harness,
    staged_tests,
)


@pytest.mark.parametrize(
    "version",
    [
        row["id"]
        for row in json.loads((ROOT / "tools/unity-versions.json").read_text(encoding="utf-8"))[
            "versions"
        ]
    ],
)
def test_optional_examples_complete_matrix_preserves_sources_defines_and_reference_graph(
    harness: CompileHarness,
    staged_tests: tuple[Path, Path],
    version: str,
) -> None:
    project, framework = staged_tests
    # Nested sources must be selected too, without changing any core assembly sources.
    nested = harness.repo / "CustomTools/RoslynRuntimeCompilation/Nested/Additional.cs"
    nested.parent.mkdir()
    nested.write_text("class Additional {}", encoding="utf-8")
    for platform in ("win", "osx"):
        (harness.output / platform).mkdir()
    result = harness.run(version, "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    core = (
        "UnityEngine.TestRunner",
        "UnityEditor.TestRunner",
        "MCPForUnity.Runtime",
        COROUTINES_ASSEMBLY,
        "MCPForUnity.Editor",
    )
    ui = ("UnityEngine.UI", "UnityEditor.UI") if version.startswith("7000.") else ()
    expected = (*ui, *core, *OPTIONAL_ASSEMBLIES, "TestAsmdef", "MCPForUnityTests.EditMode")
    calls = [Path(line).stem for line in harness.calls.read_text(encoding="utf-8").splitlines()]
    assert calls == list(expected) * 3
    for platform in ("win", "osx", "linux"):
        responses = {
            name: (harness.output / platform / f"{name}.rsp")
            .read_text(encoding="utf-8")
            .splitlines()
            for name in expected
        }
        editor_defines = {
            line for line in responses["MCPForUnity.Editor"] if line.startswith("-define:")
        }
        for name in expected:
            lines = responses[name]
            assert ("-warnaserror+" in lines) == (name in OWNED_ASSEMBLIES)
            assert ("-define:USE_ROSLYN" in lines) == (name == OPTIONAL_ASSEMBLIES[1])
            roslyn_refs = {line for line in lines if f"/{ROSLYN_DIRECTORY}/" in line}
            if name in OPTIONAL_ASSEMBLIES:
                defines = {line for line in lines if line.startswith("-define:")}
                assert defines == editor_defines | (
                    {"-define:USE_ROSLYN"} if name.endswith("RoslynOn") else set()
                )
                assert {
                    Path(line.removeprefix("-r:").strip('"')).name for line in roslyn_refs
                } == set(ROSLYN_REFERENCES)
                for owned in (
                    "MCPForUnity.Runtime",
                    "MCPForUnity.Editor",
                    "UnityEngine.TestRunner",
                    "UnityEditor.TestRunner",
                ):
                    assert any(line.endswith(f'/{owned}.dll"') for line in lines)
                sources = {
                    Path(line.strip('"')).relative_to(harness.repo).as_posix()
                    for line in lines
                    if line.startswith('"')
                }
                assert sources == {
                    f"CustomTools/RoslynRuntimeCompilation/{source}"
                    for source in (
                        "ManageRuntimeCompilation.cs",
                        "RoslynRuntimeCompiler.cs",
                        "Nested/Additional.cs",
                    )
                }
                assert "-nowarn:CS1701,CS1702" in lines
                assert sum(line.startswith("-nowarn:") for line in lines) == 1
                assert "-warnaserror+" in lines
            else:
                assert not roslyn_refs
                assert not any("CustomTools/" in line for line in lines)


@pytest.mark.parametrize("name", ROSLYN_REFERENCES)
def test_optional_missing_required_roslyn_component_cannot_use_other_groups(
    harness: CompileHarness,
    name: str,
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
    harness: CompileHarness,
    name: str,
    failure: str,
) -> None:
    compiler = harness.data / "NetCoreRuntime/dotnet"
    replacement = f'case "$output" in *{name}.dll) {"exit 7" if failure == "exit_code" else ":"} ;; *) : > "$output" ;; esac'
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(': > "$output"', replacement),
        encoding="utf-8",
    )
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
    for path in (
        "CustomTools/RoslynRuntimeCompilation/**/*.cs",
        "tools/tests/test_compile_check*.py",
        "tools/tests/compile_check_helpers.py",
        "tools/tests/test_unity_compile_cache.py",
        "tools/tests/test_unity_compile_cache_diagnostics.py",
        "tools/tests/test_unity_ci_packages.py",
        "tools/tests/test_unity_matrix_workflows.py",
    ):
        assert f"- {path}" in workflow
    assert "USE_ROSLYN" in workflow and "off" in workflow and "on" in workflow
