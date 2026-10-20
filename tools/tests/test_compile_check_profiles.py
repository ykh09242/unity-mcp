"""Unity version reference profiles and required API modules."""

from pathlib import Path

import pytest

from .compile_check_helpers import (
    COROUTINES_ASSEMBLY,
    CompileHarness,
    OWNED_ASSEMBLIES,
    ROOT,
    STALE_REFERENCES,
    harness,
    staged_tests,
)


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_portable_manifests_do_not_require_removed_or_optional_modules(name: str) -> None:
    references = set(
        (ROOT / "tools" / "compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    )
    assert not references & STALE_REFERENCES


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity63_profile_removes_only_confirmed_absent_test_protocol_module(name: str) -> None:
    root = ROOT / "tools/compile-refs"
    default = set((root / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    selected = set((root / "6000.3" / f"{name}.txt").read_text(encoding="utf-8").splitlines())
    assert default - selected == {
        "DATA/Managed/UnityEngine/UnityEngine.UnityTestProtocolModule.dll"
    }
    assert selected - default == {
        entry
        for entry in ("DATA/Managed/UnityEngine/UnityEditor.MediaModule.dll",)
        if name == "Editor"
    }


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity66_profile_removes_only_compiler_proven_absent_modules(name: str) -> None:
    default = set(
        (ROOT / "tools/compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    )
    selected = set(
        (ROOT / "tools/compile-refs/6000.6" / f"{name}.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert default - selected == {
        f"DATA/Managed/UnityEngine/UnityEngine.{module}Module.dll"
        for module in ("SharedInternals", "UnityTestProtocol", "VR")
    }
    assert selected - default == {
        "DATA/Managed/UnityEngine/UnityEngine.MathematicsModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll",
    } | {
        entry
        for entry in ("DATA/Managed/UnityEngine/UnityEditor.MediaModule.dll",)
        if name == "Editor"
    }


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b3", "7000.0.0a7"])
def test_mathematics_module_resolves_from_real_profiles_for_all_consumers(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str
) -> None:
    project, framework = staged_tests
    family = ".".join(version.split(".")[:2])
    mathematics = "DATA/Managed/UnityEngine/UnityEngine.MathematicsModule.dll"
    roots = {
        "DATA": harness.data,
        "EXTRA": harness.extra,
        "LIBCACHE": harness.data
        / "Resources/PackageManager/ProjectTemplates/libcache/fixture/ScriptAssemblies",
    }
    for name in ("Runtime", "Editor"):
        profile = Path("tools/compile-refs") / family / f"{name}.txt"
        text = (ROOT / profile).read_text(encoding="utf-8")
        assert mathematics in text.splitlines()
        (harness.repo / profile).write_text(text, encoding="utf-8")
        for entry in text.splitlines():
            prefix, relative = entry.split("/", 1)
            if prefix == "COMPILED":
                continue
            reference = roots[prefix] / relative
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.touch()

    result = harness.run(version, "win osx linux", test_project=project, framework=framework)

    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        for assembly in (*OWNED_ASSEMBLIES, COROUTINES_ASSEMBLY):
            response = (harness.output / platform / f"{assembly}.rsp").read_text(encoding="utf-8")
            assert '/Managed/UnityEngine/UnityEngine.MathematicsModule.dll"' in response


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b3", "7000.0.0a7"])
def test_missing_mathematics_module_fails_before_invoking_compiler(
    harness: CompileHarness, version: str
) -> None:
    family = ".".join(version.split(".")[:2])
    entry = "DATA/Managed/UnityEngine/UnityEngine.MathematicsModule.dll"
    manifest = harness.repo / "tools/compile-refs" / family / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")

    result = harness.run(version)

    assert result.returncode != 0
    assert f"required reference not found: {entry}" in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version", ["6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_current_scripting_module_is_required_metadata(
    harness: CompileHarness, version: str
) -> None:
    entry = "DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll"
    family = ".".join(version.split(".")[:2])
    manifest = harness.repo / "tools/compile-refs" / family / "Runtime.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")
    result = harness.run(version)
    assert result.returncode != 0
    assert entry in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("version", ["6000.3.25f1", "6000.6.4f1", "6000.7.0b2", "6000.7.0a6"])
def test_split_media_module_resolves_from_real_editor_profile(
    harness: CompileHarness, staged_tests: tuple[Path, Path], version: str
) -> None:
    project, framework = staged_tests
    family = ".".join(version.split(".")[:2])
    profile = Path("tools/compile-refs") / family / "Editor.txt"
    entries = (ROOT / profile).read_text(encoding="utf-8").splitlines()
    media = "DATA/Managed/UnityEngine/UnityEditor.MediaModule.dll"
    assert media in entries
    (harness.repo / profile).write_text("\n".join(entries) + "\n", encoding="utf-8")
    roots = {
        "DATA": harness.data,
        "EXTRA": harness.extra,
        "LIBCACHE": harness.data
        / "Resources/PackageManager/ProjectTemplates/libcache/fixture/ScriptAssemblies",
    }
    for entry in entries:
        prefix, relative = entry.split("/", 1)
        target = roots[prefix] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    result = harness.run(version, "win osx linux", test_project=project, framework=framework)
    assert result.returncode == 0, result.stdout + result.stderr
    for platform in ("win", "osx", "linux"):
        for assembly in ("MCPForUnity.Editor", "MCPForUnityTests.EditMode"):
            rsp = (harness.output / platform / f"{assembly}.rsp").read_text(encoding="utf-8")
            assert '/Managed/UnityEngine/UnityEditor.MediaModule.dll"' in rsp
        runtime = (harness.output / platform / "MCPForUnity.Runtime.rsp").read_text(
            encoding="utf-8"
        )
        assert "UnityEditor.MediaModule.dll" not in runtime
    harness.calls.unlink()
    (harness.data / media.removeprefix("DATA/")).unlink()
    result = harness.run(version, framework=framework)
    assert result.returncode != 0
    assert f"required reference not found: {media}" in result.stderr
    assert not harness.calls.exists()


@pytest.mark.parametrize("name", ["Runtime", "Editor"])
def test_unity67_profile_removes_only_beta_compiler_proven_absent_modules(name: str) -> None:
    default = set(
        (ROOT / "tools/compile-refs" / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    )
    selected = set(
        (ROOT / "tools/compile-refs/6000.7" / f"{name}.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert default - selected == {
        f"DATA/Managed/UnityEngine/UnityEngine.{module}Module.dll"
        for module in ("AR", "SharedInternals", "Substance", "UnityTestProtocol", "VR")
    }
    assert selected - default == {
        "DATA/Managed/UnityEngine/UnityEngine.ManagedKernelModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.MathematicsModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.ScriptingModule.dll",
        "DATA/Managed/UnityEngine/UnityEngine.UICommonModule.dll",
    } | {
        entry
        for entry in ("DATA/Managed/UnityEngine/UnityEditor.MediaModule.dll",)
        if name == "Editor"
    }


@pytest.mark.parametrize("version", ["6000.7.0b2", "6000.7.0a6"])
@pytest.mark.parametrize("name", ["Runtime", "Editor"])
@pytest.mark.parametrize("module", ["UICommon", "ManagedKernel"])
def test_preview_split_api_module_is_required_metadata(
    harness: CompileHarness,
    version: str,
    name: str,
    module: str,
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


@pytest.mark.parametrize(
    "family,module",
    [
        ("2021.3", "UnityEngine.TextRenderingModule.dll"),
        ("2022.3", "UnityEngine.TextRenderingModule.dll"),
        ("2021.3", "UnityEngine.UnityAnalyticsCommonModule.dll"),
    ],
)
def test_legacy_profiles_include_compiler_proven_vendor_api_modules(
    family: str, module: str
) -> None:
    for name in ("Runtime", "Editor"):
        entries = (
            (ROOT / "tools/compile-refs" / family / f"{name}.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert f"DATA/Managed/UnityEngine/{module}" in entries


@pytest.mark.parametrize(
    "family,module",
    [
        ("2021.3", "UnityEngine.TextRenderingModule.dll"),
        ("2022.3", "UnityEngine.TextRenderingModule.dll"),
        ("2021.3", "UnityEngine.UnityAnalyticsCommonModule.dll"),
    ],
)
def test_legacy_vendor_api_module_metadata_is_required(
    harness: CompileHarness, family: str, module: str
) -> None:
    manifest = harness.repo / "tools/compile-refs" / family / "Runtime.txt"
    entry = f"DATA/Managed/UnityEngine/{module}"
    manifest.write_text(manifest.read_text(encoding="utf-8") + entry + "\n", encoding="utf-8")
    result = harness.run(f"{family}.0f1")
    assert result.returncode != 0
    assert entry in result.stderr
    assert not harness.calls.exists()
