"""Compiler selection, basic references and stream handling."""

import os

from pathlib import Path

import shutil

import pytest

from .compile_check_helpers import (
    COROUTINES_ASSEMBLY,
    CompileHarness,
    harness,
    modern_sdk,
    staged_tests,
)


@pytest.mark.parametrize(
    "version,profile",
    [
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
        ("7000.0.0a7", "7000.0"),
    ],
)
def test_matrix_compiles_all_platforms_with_selected_explicit_profile(
    harness: CompileHarness,
    version: str,
    profile: str,
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
            rsp = (harness.output / platform / f"MCPForUnity.{assembly}.rsp").read_text(
                encoding="utf-8"
            )
            assert '/Managed/Selected.dll"' in rsp
            assert f"-define:UNITY_EDITOR_{platform.upper()}" in rsp
        coroutine_rsp = (harness.output / platform / f"{COROUTINES_ASSEMBLY}.rsp").read_text(
            encoding="utf-8"
        )
        assert '/Managed/Selected.dll"' in coroutine_rsp
        assert f"-define:UNITY_EDITOR_{platform.upper()}" in coroutine_rsp
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == (
        21 if version.startswith("7000.") else 15
    )


@pytest.mark.parametrize("version", ["6000.7.0b2", "6000.7.0a6", "6000.8.0a1"])
def test_prerelease_and_future_minor_defines_remain_numeric(
    harness: CompileHarness, version: str
) -> None:
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    major, minor, _ = version.split(".")
    rsp = (harness.output / "linux" / "MCPForUnity.Runtime.rsp").read_text(encoding="utf-8")
    defines = {
        line.removeprefix("-define:") for line in rsp.splitlines() if line.startswith("-define:")
    }
    assert {
        f"UNITY_{major}_{minor}",
        f"UNITY_{major}_{minor}_0",
        f"UNITY_{major}_{minor}_OR_NEWER",
    } <= defines
    assert "UNITY_6000_6_OR_NEWER" in defines
    assert not any("0a" in flag or "0b" in flag for flag in defines)


@pytest.mark.skipif(
    os.name != "nt", reason="Git Bash on Windows resolves the dotnet.exe executable suffix"
)
def test_windows_compiler_layout_uses_bundled_runtime(harness: CompileHarness) -> None:
    shim = harness.data / "NetCoreRuntime" / "dotnet"
    shim.rename(shim.with_suffix(".exe"))
    result = harness.run("2021.3.45f2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 5


def test_missing_compiler_reports_distribution_candidates_without_selecting_them(
    harness: CompileHarness,
) -> None:
    (harness.data / "DotNetSdkRoslyn" / "csc.dll").unlink()
    candidate = (
        harness.data
        / "Tools"
        / "Scripting"
        / "DotNetSdk"
        / "sdk"
        / "9.0.100"
        / "Roslyn"
        / "bincore"
        / "csc.dll"
    )
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.touch()
    result = harness.run("6000.0.84f1")
    assert result.returncode == 2
    assert "Compiler distribution candidates (diagnostic only):" in result.stderr
    assert "Tools/Scripting/DotNetSdk/sdk/9.0.100/Roslyn/bincore/csc.dll" in result.stderr
    assert "NetCoreRuntime/dotnet" in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b2", "6000.7.0a6", "7000.0.0a7"])
def test_modern_sdk_uses_one_coherent_bundled_toolchain(
    harness: CompileHarness, version: str
) -> None:
    modern_sdk(harness)
    (harness.data / "DotNetSdkRoslyn" / "csc.dll").unlink()
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Compiler      : " in result.stdout
    assert "Tools/Scripting/DotNetSdk/sdk/9.0.100/Roslyn/bincore/csc.dll" in result.stdout
    assert "Runtime       : " in result.stdout
    assert "Tools/Scripting/DotNetSdk/dotnet" in result.stdout
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == (
        7 if version.startswith("7000.") else 5
    )


@pytest.mark.parametrize(
    "failure",
    [
        "absent",
        "compiler",
        "runtime",
        "two_roots",
        "two_versions",
        "partial_version",
        "two_runtimes",
    ],
)
def test_modern_sdk_rejects_missing_or_ambiguous_components(
    harness: CompileHarness, failure: str
) -> None:
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
    assert len(harness.calls.read_text(encoding="utf-8").splitlines()) == 5


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


@pytest.mark.parametrize(
    "version",
    [
        "2021.3.45f2",
        "2022.3.62f1",
        "6000.3.25f1",
        "6000.6.4f1",
        "6000.7.0b2",
        "6000.7.0a6",
        "7000.0.0a7",
    ],
)
def test_missing_selected_legacy_manifest_does_not_fall_back(
    harness: CompileHarness, version: str
) -> None:
    major, minor, _ = version.split(".")
    (harness.repo / "tools" / "compile-refs" / f"{major}.{minor}" / "Runtime.txt").unlink()
    result = harness.run(version)
    assert result.returncode != 0
    assert "reference manifest not found:" in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize(
    "version,expected",
    [
        ("2021.3.45f2", set()),
        ("2022.3.62f1", set()),
        ("2023.1.20f1", {"UNITY_2023_1_OR_NEWER"}),
        ("2023.2.20f1", {"UNITY_2023_1_OR_NEWER", "UNITY_2023_2_OR_NEWER"}),
        ("6000.0.75f1", {"UNITY_2023_1_OR_NEWER", "UNITY_2023_2_OR_NEWER"}),
    ],
)
def test_historical_version_defines_select_the_correct_api_branches(
    harness: CompileHarness, version: str, expected: set[str]
) -> None:
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    for assembly in ("Runtime", "Editor"):
        rsp = (harness.output / "linux" / f"MCPForUnity.{assembly}.rsp").read_text(encoding="utf-8")
        flags = {
            line.removeprefix("-define:")
            for line in rsp.splitlines()
            if line.startswith("-define:UNITY_2023_") and line.endswith("_OR_NEWER")
        }
        assert flags == expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("2021.3.45f2", set()),
        ("2022.3.62f1", set()),
        ("6000.0.84f1", set()),
        ("6000.3.14f1", set()),
        ("6000.6.4f1", {"UNITY_6000_6_OR_NEWER"}),
        ("6000.7.0b3", {"UNITY_6000_6_OR_NEWER", "UNITY_6000_7_OR_NEWER"}),
        (
            "7000.0.0a7",
            {"UNITY_6000_6_OR_NEWER", "UNITY_6000_7_OR_NEWER", "UNITY_7000_0_OR_NEWER"},
        ),
    ],
)
def test_editmode_compiler_receives_modern_compatibility_guards_without_relaxing_warnings(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str, expected: set[str]
) -> None:
    project, framework = staged_tests
    result = harness.run(version, test_project=project, framework=framework)

    assert result.returncode == 0, result.stdout + result.stderr
    response = (harness.output / "linux/MCPForUnityTests.EditMode.rsp").read_text(encoding="utf-8")
    guards = {"UNITY_6000_6_OR_NEWER", "UNITY_6000_7_OR_NEWER", "UNITY_7000_0_OR_NEWER"}
    defines = {line.removeprefix("-define:") for line in response.splitlines()}
    assert defines & guards == expected
    assert "-warnaserror+" in response.splitlines()
    assert "CS0436" not in response and "CS0618" not in response


@pytest.mark.parametrize(
    "reference", ["DATA/Managed/Missing.dll", "EXTRA/Missing.dll", "LIBCACHE/Missing.dll"]
)
@pytest.mark.parametrize(
    "version,profile", [("6000.0.75f1", ""), ("2021.3.45f2", "2021.3"), ("2022.3.62f1", "2022.3")]
)
def test_missing_required_reference_fails_before_invoking_compiler(
    harness: CompileHarness, reference: str, version: str, profile: str
) -> None:
    manifest = harness.repo / "tools" / "compile-refs" / profile / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + reference + "\n", encoding="utf-8")
    result = harness.run(version)
    assert result.returncode != 0
    assert "::error::" in result.stderr + result.stdout
    assert reference in result.stderr + result.stdout
    assert not harness.calls.exists()


def test_existing_reference_is_preserved_in_both_compilations(harness: CompileHarness) -> None:
    (harness.repo / "MCPForUnity/Runtime/Extra source.cs").write_text(
        "class ExtraFixture {}\n", encoding="utf-8"
    )
    result = harness.run("6000.0.75f1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" not in result.stderr + result.stdout
    for assembly in ("Runtime", "Editor"):
        rsp = (harness.output / "linux" / f"MCPForUnity.{assembly}.rsp").read_text(encoding="utf-8")
        assert '/Managed/Core.dll"' in rsp
        assert '/Fixture.cs"' in rsp
    runtime = (harness.output / "linux/MCPForUnity.Runtime.rsp").read_text(encoding="utf-8")
    assert '/Extra source.cs"' in runtime
    for response_path in harness.calls.read_text(encoding="utf-8").splitlines():
        response = Path(response_path)
        lines = response.read_text(encoding="utf-8").splitlines()
        sources = sum(line.startswith('"') for line in lines)
        references = sum(line.startswith("-r:") for line in lines)
        assert f"--- {response.stem} [linux] : {sources} sources, {references} refs ---" in (
            result.stdout
        )


@pytest.mark.parametrize("entry", ["UNKNOWN/Core.dll", "LIBCACHE/Missing after valid.dll"])
def test_unresolved_reference_cannot_reuse_previous_manifest_entry(
    harness: CompileHarness, entry: str
) -> None:
    manifest = harness.repo / "tools/compile-refs/2021.3/Runtime.txt"
    manifest.write_text(f"DATA/Managed/Core.dll\n{entry}\n", encoding="utf-8")
    result = harness.run("2021.3.45f2")
    assert result.returncode != 0
    assert f"required reference not found: {entry}" in result.stderr
    assert not harness.calls.exists()


def test_caller_resolved_references_preserve_crlf_spaces_and_platform_outputs(
    harness: CompileHarness,
) -> None:
    template = (
        harness.data / "Resources/PackageManager/ProjectTemplates/libcache/owned/ScriptAssemblies"
    )
    template.mkdir(parents=True)
    references = (
        ("DATA/Managed/Extra reference.dll", harness.data / "Managed/Extra reference.dll"),
        ("EXTRA/Extra library.dll", harness.extra / "Extra library.dll"),
        ("LIBCACHE/Template library.dll", template / "Template library.dll"),
    )
    for _, path in references:
        path.touch()
    profile = harness.repo / "tools/compile-refs/2021.3"
    content = "DATA/Managed/Core.dll\r\n" + "".join(f"{entry}\r\n" for entry, _ in references)
    (profile / "Runtime.txt").write_bytes(content.encode())
    (profile / "Editor.txt").write_bytes(
        (content + "COMPILED/MCPForUnity.Runtime.dll\r\n").encode()
    )
    (harness.repo / "tools/compile-defines.txt").write_bytes(b"UNITY_EDITOR\r\nOWNED_FLAG\r\n")
    result = harness.run("2021.3.45f2", "win osx linux")
    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        for assembly in ("Runtime", "Editor"):
            response = (harness.output / platform / f"MCPForUnity.{assembly}.rsp").read_text()
            assert "-define:OWNED_FLAG\n" in response
            for _, path in references:
                assert f'-r:"{path.as_posix()}"' in response
            if assembly == "Editor":
                assert (
                    f'-r:"{harness.output.as_posix()}/{platform}/MCPForUnity.Runtime.dll"'
                    in response
                )
    assert len(harness.calls.read_text().splitlines()) == 15


def test_source_inventory_is_refreshed_between_invocations_with_nested_ownership(
    harness: CompileHarness,
) -> None:
    assert harness.run("2021.3.45f2").returncode == 0
    source = harness.repo / "MCPForUnity/Runtime"
    (source / "Fixture.cs").unlink()
    (source / "Added source.cs").write_text("class Added {}", encoding="utf-8")
    nested = source / "Nested assembly"
    nested.mkdir()
    (nested / "Other.asmdef").write_text('{"name":"Other"}', encoding="utf-8")
    (nested / "Excluded.cs").write_text("#error Not owned", encoding="utf-8")
    result = harness.run("2021.3.45f2")
    assert result.returncode == 0, result.stdout + result.stderr
    response = (harness.output / "linux/MCPForUnity.Runtime.rsp").read_text()
    assert f'"{source.as_posix()}/Added source.cs"' in response
    assert "Fixture.cs" not in response and "Excluded.cs" not in response
    assert len(harness.calls.read_text().splitlines()) == 10


@pytest.mark.parametrize("disappearing", ["reference", "source"])
def test_disappearing_compile_inputs_fail_closed_after_first_platform(
    harness: CompileHarness, disappearing: str
) -> None:
    source = harness.repo / "MCPForUnity/Runtime/Extra source.cs"
    source.write_text("class Additional {}", encoding="utf-8")
    target = (
        "$UNITY_DATA/Managed/Core.dll"
        if disappearing == "reference"
        else "$REPO/MCPForUnity/Runtime/Extra source.cs"
    )
    compiler = harness.data / "NetCoreRuntime/dotnet"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            ': > "$output"',
            f': > "$output"; if [[ "$output" == */win/MCPForUnity.Runtime.dll ]]; then rm "{target}"; fi',
        ),
        encoding="utf-8",
    )
    result = harness.run("2021.3.45f2", "win linux")
    assert result.returncode != 0
    expected = (
        "required reference not found:"
        if disappearing == "reference"
        else "assembly source not found:"
    )
    assert expected in result.stderr
    assert not (harness.output / "linux/MCPForUnity.Runtime.dll").exists()
    assert len(harness.calls.read_text().splitlines()) == (1 if disappearing == "reference" else 5)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_compiler_stream_preserves_diagnostics_and_exit_status(harness, exit_code):
    compiler = harness.data / "NetCoreRuntime/dotnet"
    original = compiler.read_text(encoding="utf-8")
    diagnostics = (
        "printf '%s\\n' 'Microsoft (R) fixture compiler' 'Copyright fixture' '' "
        "'Fixture.cs(1,1): warning CS1234: retained warning'\n"
        "printf '%s' 'Fixture.cs(2,1): error CS9999: retained final diagnostic' >&2\n"
        f"if [ {exit_code} -ne 0 ]; then exit {exit_code}; fi\n"
    )
    compiler.write_text(original.replace("set -eu\n", "set -eu\n" + diagnostics), encoding="utf-8")
    result = harness.run("6000.0.84f1")
    assert result.returncode == (0 if exit_code == 0 else 1)
    assert "warning CS1234: retained warning" in result.stdout
    assert "error CS9999: retained final diagnostic" in result.stdout
    assert "Microsoft (R) fixture compiler" not in result.stdout
    assert "Copyright fixture" not in result.stdout
    assert ("compile check passed" in result.stdout) == (exit_code == 0)
