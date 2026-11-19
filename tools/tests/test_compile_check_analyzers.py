"""Verify that the SDK analyzes the same first-party inputs as Unity's native compiler."""

from pathlib import Path
import shutil

import pytest

from .compile_check_helpers import (
    CompileHarness,
    ROOT,
    harness,
    input_system_sources,
    staged_tests,
    ugui_sources,
)


@pytest.fixture
def analyzer_sdk(harness: CompileHarness, monkeypatch: pytest.MonkeyPatch) -> Path:
    sdk = harness.repo / "analysis host/sdk/11.0.100"
    analyzers = sdk / "Sdks/Microsoft.NET.Sdk/analyzers"
    for path in (
        sdk / "Roslyn/bincore/csc.dll",
        analyzers / "Microsoft.CodeAnalysis.NetAnalyzers.dll",
        analyzers / "Microsoft.CodeAnalysis.CSharp.NetAnalyzers.dll",
        analyzers / "build/config/analysislevel_11_default.globalconfig",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    host = sdk.parents[1] / "dotnet"
    host.write_text(
        """#!/usr/bin/env bash
set -eu
rsp=${2#@}
printf '%s\\n' "$@" > "$rsp.analysis"
for option in "$@"; do
  case "$option" in -out:*) output=${option#-out:} ;; esac
done
printf 'analyzed\\n' > "$output"
case "$rsp" in *MCPForUnity.Editor.rsp) exit "${ANALYSIS_EXIT:-0}" ;; esac
""",
        encoding="utf-8",
    )
    host.chmod(0o755)
    unity = harness.repo / "locked Unity analyzer.dll"
    unity.write_bytes(b"fixture")
    config = Path("tools/compile-refs/unity-analyzers.globalconfig")
    shutil.copyfile(ROOT / config, harness.repo / config)
    monkeypatch.setenv("UNITY_ANALYZER_DLL", unity.as_posix())
    monkeypatch.setenv("UNITY_ANALYZER_SDK", sdk.as_posix())
    return sdk


@pytest.mark.parametrize("feature", ["ugui_sources", "input_system_sources"])
def test_sdk_checks_owned_editor_player_and_sample_assemblies(
    request: pytest.FixtureRequest, harness: CompileHarness, analyzer_sdk: Path, feature: str
) -> None:
    project, framework = request.getfixturevalue(feature)
    (project / "Assets/Default.cs").write_text("class Default {}", encoding="utf-8")
    full = feature == "input_system_sources"
    if full:
        for directory in ("Runtime", "Editor", "Tests/Editor"):
            path = Path("tools/fixtures/play_scenario_lifecycle/Assets") / directory
            shutil.copytree(ROOT / path, harness.repo / path)
        for path in ("tools/fixtures/play_scenario_mcp", "tools/unity-ci/upm-smoke"):
            shutil.copytree(ROOT / path, harness.repo / path)
    result = harness.run(
        "6000.0.84f1", test_project=project, framework=framework, input_system=full, ugui=not full
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for rsp in map(Path, harness.calls.read_text().splitlines()):
        unity_args = rsp.read_text(encoding="utf-8").splitlines()
        checked = rsp.with_suffix(".rsp.analysis")
        assert checked.exists() == ("-warnaserror+" in unity_args), rsp
        if checked.exists():
            args = checked.read_text(encoding="utf-8").splitlines()
            assert args[1] == "@" + rsp.as_posix()
            assert sum(arg.startswith("-analyzer:") for arg in args) == 3
            assert any(arg.endswith("/unity-analyzers.globalconfig") for arg in args)
            assert any(arg.endswith("/analysislevel_11_default.globalconfig") for arg in args)
            assert rsp.with_suffix(".dll").stat().st_size == 0
            assert (
                rsp.parent / "analyzed" / rsp.with_suffix(".dll").name
            ).read_text() == "analyzed\n"
    responses = harness.output / "linux"
    assert (responses / "player-scenarios/MCPForUnity.Runtime.rsp.analysis").is_file()
    default = (responses / "Assembly-CSharp.rsp").read_text()
    assert "/Assets/Default.cs" in default and "/TestAsmdef/" not in default
    if full:
        editor = (responses / "MCPForUnity.Editor.rsp").read_text()
        assert "-define:USE_ROSLYN" in editor and "-define:UNITY_VFX_GRAPH" in editor
        assert 'Microsoft.CodeAnalysis.CSharp.dll"' in editor
        assert "TransportScenarioTests.cs" in (responses / "LifecycleSample.Tests.rsp").read_text()
        assert (responses / "UntouchedUpmSmoke.Editor.Tests.rsp.analysis").is_file()
        assert "-unsafe+" in (responses / "MCPForUnity.Input.InputSystem.Editor.rsp").read_text()


def test_missing_sdk_configuration_or_a_failing_analyzer_fails_the_job(
    harness: CompileHarness, analyzer_sdk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANALYSIS_EXIT", "1")
    rejected = harness.run("2021.3.45f2")
    assert rejected.returncode != 0
    assert (harness.output / "linux/MCPForUnity.Editor.rsp.analysis").is_file()
    (
        analyzer_sdk
        / "Sdks/Microsoft.NET.Sdk/analyzers/build/config/analysislevel_11_default.globalconfig"
    ).unlink()
    missing = harness.run("2021.3.45f2")
    assert missing.returncode == 2
    assert "analyzer SDK input not found" in missing.stderr
