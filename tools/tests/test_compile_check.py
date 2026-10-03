"""Exercise the real compile harness with a hermetic reference tree and compiler shim."""

from dataclasses import dataclass
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

    def run(self, version: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, UNITY_DATA=self.data.as_posix(), REPO=self.repo.as_posix(),
                   UNITY_VERSION=version, EXTRA_REFS=self.extra.as_posix(),
                   TEST_FRAMEWORK_SOURCE="", PLATFORMS="linux", OUT=self.output.as_posix(),
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
def test_missing_required_reference_fails_before_invoking_compiler(harness: CompileHarness, reference: str) -> None:
    manifest = harness.repo / "tools" / "compile-refs" / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + reference + "\n", encoding="utf-8")
    result = harness.run("6000.0.75f1")
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
