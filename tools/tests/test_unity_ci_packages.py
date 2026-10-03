"""Hermetic contracts for CI package selection and isolated project staging."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import shutil
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("unity_ci_packages", ROOT / "tools" / "unity_ci_packages.py")
assert SPEC and SPEC.loader
packages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packages
SPEC.loader.exec_module(packages)


def make_package(root: Path, name: str, version: str, dependencies: dict[str, str] | None = None, unity: str = "2019.4") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "package.json").write_text(json.dumps({"name": name, "version": version, "unity": unity, "dependencies": dependencies or {}}), encoding="utf-8")
    return path


@pytest.fixture
def environment(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo, data, cache = (tmp_path / name for name in ("repo", "editor-data", "cache"))
    (repo / "MCPForUnity" / "Runtime").mkdir(parents=True)
    (repo / "MCPForUnity" / "Runtime" / "Fixture.cs").write_text("class Fixture {}", encoding="utf-8")
    (repo / "MCPForUnity" / "Runtime" / "Fixture.cs.meta").write_text("guid: preserved", encoding="utf-8")
    (repo / "MCPForUnity" / "package.json").write_text(json.dumps({"name": "com.coplaydev.unity-mcp", "dependencies": {"com.unity.test-framework": "1.4.6", "com.unity.ext.nunit": "2.0.5", "com.unity.nuget.newtonsoft-json": "3.2.2"}}), encoding="utf-8")
    project = repo / "TestProjects" / "UnityMCPTests"
    for name in ("Assets", "ProjectSettings", "Packages", "Library"):
        (project / name).mkdir(parents=True)
    (project / "Assets" / "Fixture.cs").write_text("class TestFixture {}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2021.3.45f2\n", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{"legacy.ide":"1.0.0"}}', encoding="utf-8")
    (project / "Packages" / "packages-lock.json").write_text("original lock", encoding="utf-8")
    (project / "Library" / "stale.dll").write_bytes(b"not copied")
    builtin = data / "Resources" / "PackageManager" / "BuiltInPackages"
    framework = make_package(builtin, "com.unity.test-framework", "1.6.0", {"com.unity.ext.nunit": "2.0.3", "com.unity.modules.imgui": "1.0.0"}, unity="6000.0")
    for assembly in ("UnityEngine.TestRunner", "UnityEditor.TestRunner"):
        (framework / assembly).mkdir()
        (framework / assembly / "Fixture.cs").write_text("class VendorFixture {}", encoding="utf-8")
    nunit = make_package(builtin, "com.unity.ext.nunit", "2.0.5")
    (nunit / "net40" / "unity-custom").mkdir(parents=True)
    (nunit / "net40" / "unity-custom" / "nunit.framework.dll").write_bytes(b"selected nunit")
    make_package(builtin, "com.unity.ugui", "2.0.0", {"com.unity.modules.ui": "1.0.0"})
    make_package(builtin, "com.unity.modules.ui", "1.0.0")
    make_package(builtin, "com.unity.modules.imgui", "1.0.0")
    json_package = make_package(cache, "com.unity.nuget.newtonsoft-json@3.2.2", "3.2.2")
    metadata = json.loads((json_package / "package.json").read_text(encoding="utf-8"))
    metadata["name"] = "com.unity.nuget.newtonsoft-json"
    (json_package / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    (json_package / "Runtime").mkdir()
    (json_package / "Runtime" / "Newtonsoft.Json.dll").write_bytes(b"selected json")
    profiles = repo / "profiles.json"
    profiles.write_text(json.dumps({"schemaVersion": 1, "requiredModules": [], "profiles": {
        "modern": {"unityMajors": [6000], "packages": {
            "com.unity.test-framework": {"source": "editor"}, "com.unity.ext.nunit": {"source": "editor"},
            "com.unity.ugui": {"source": "editor"}, "com.unity.nuget.newtonsoft-json": {"source": "registry", "version": "3.2.2"}}}}}), encoding="utf-8")
    return repo, data, cache, profiles


def prepare(environment: tuple[Path, Path, Path, Path], version: str = "6000.0.69f1") -> packages.Preparation:
    repo, data, cache, profiles = environment
    return packages.prepare(version, data, repo / ".unity-ci" / version, repo=repo, profiles_path=profiles, registry_cache=cache)


def test_modern_profile_uses_editor_framework_and_preserves_originals(environment: tuple[Path, Path, Path, Path]) -> None:
    repo, _, _, _ = environment
    originals = {path: path.read_bytes() for path in (repo / "MCPForUnity" / "package.json", repo / "TestProjects" / "UnityMCPTests" / "Packages" / "manifest.json", repo / "TestProjects" / "UnityMCPTests" / "Packages" / "packages-lock.json")}
    result = prepare(environment)
    project = repo / result.project_path
    framework = repo / result.test_framework_source
    assert json.loads((framework / "package.json").read_text(encoding="utf-8"))["version"] == "1.6.0"
    assert (repo / result.refs / "nunit.framework.dll").read_bytes() == b"selected nunit"
    manifest = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))
    assert "legacy.ide" not in manifest["dependencies"]
    for name in ("com.unity.test-framework", "com.unity.ext.nunit", "com.unity.ugui", "com.unity.nuget.newtonsoft-json", "com.coplaydev.unity-mcp"):
        target = (project / "Packages" / manifest["dependencies"][name].removeprefix("file:")).resolve()
        assert target.is_dir()
    copied = json.loads((repo / ".unity-ci" / "6000.0.69f1" / "package" / "package.json").read_text(encoding="utf-8"))
    assert copied["dependencies"]["com.unity.test-framework"] == "1.6.0"
    assert (repo / ".unity-ci" / "6000.0.69f1" / "package" / "Runtime" / "Fixture.cs.meta").read_text(encoding="utf-8") == "guid: preserved"
    assert not (project / "Library").exists()
    assert not (project / "Packages" / "packages-lock.json").exists()
    assert (project / "ProjectSettings" / "ProjectVersion.txt").read_bytes() == (repo / "TestProjects" / "UnityMCPTests" / "ProjectSettings" / "ProjectVersion.txt").read_bytes()
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_missing_builtin_fails_without_registry_fallback(environment: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _, data, _, _ = environment
    (data / "Resources" / "PackageManager" / "BuiltInPackages" / "com.unity.test-framework" / "package.json").unlink()
    monkeypatch.setattr(packages, "fetch_registry_package", lambda *args: pytest.fail("unexpected registry fallback"))
    with pytest.raises(packages.PreparationError, match="com.unity.test-framework"):
        prepare(environment)


def test_package_minimum_unity_is_enforced(environment: tuple[Path, Path, Path, Path]) -> None:
    _, data, _, _ = environment
    metadata = data / "Resources" / "PackageManager" / "BuiltInPackages" / "com.unity.test-framework" / "package.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["unity"] = "6000.7"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(packages.PreparationError, match="requires Unity"):
        prepare(environment)


@pytest.mark.parametrize("selected", ["6000.7.0a6", "6000.7.0b2"])
def test_prerelease_cannot_satisfy_explicit_stable_minimum(environment: tuple[Path, Path, Path, Path], selected: str) -> None:
    _, data, _, _ = environment
    metadata = data / "Resources" / "PackageManager" / "BuiltInPackages" / "com.unity.test-framework" / "package.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value.update(unity="6000.7", unityRelease="0f1")
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(packages.PreparationError, match="requires Unity"):
        prepare(environment, selected)


def test_transitive_minimum_cannot_be_silently_downgraded(environment: tuple[Path, Path, Path, Path]) -> None:
    _, data, _, _ = environment
    metadata = data / "Resources" / "PackageManager" / "BuiltInPackages" / "com.unity.ext.nunit" / "package.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["version"] = "2.0.2"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(packages.PreparationError, match="requires com.unity.ext.nunit"):
        prepare(environment)


def test_missing_nunit_reports_bundled_layout_without_selecting_unverified_dll(environment: tuple[Path, Path, Path, Path]) -> None:
    repo, data, _, _ = environment
    builtin = data / "Resources/PackageManager/BuiltInPackages"
    nunit = builtin / "com.unity.ext.nunit"
    (nunit / "net40/unity-custom/nunit.framework.dll").unlink()
    alternative = nunit / "unverified-target/nunit.framework.dll"
    alternative.parent.mkdir()
    alternative.write_bytes(b"not an established Editor reference")
    (alternative.with_suffix(".dll.meta")).write_text("PluginImporter:\n  platformData:\n  - first:\n      Editor: Editor\n    second:\n      enabled: 0\n", encoding="utf-8")
    asmdef = builtin / "com.unity.test-framework/UnityEditor.TestRunner/UnityEditor.TestRunner.asmdef"
    asmdef.write_text(json.dumps({"name": "UnityEditor.TestRunner", "includePlatforms": ["Editor"], "precompiledReferences": ["nunit.framework.dll"]}), encoding="utf-8")
    with pytest.raises(packages.PreparationError) as caught:
        prepare(environment, "6000.6.4f1")
    message = str(caught.value)
    assert message.startswith("Required reference DLL missing: com.unity.ext.nunit/net40/unity-custom/nunit.framework.dll")
    diagnostic = json.loads(message.split("; diagnostic=", 1)[1])
    assert diagnostic["unityVersion"] == "6000.6.4f1"
    assert diagnostic["package"] == "com.unity.ext.nunit@2.0.5"
    assert diagnostic["dllPaths"] == ["unverified-target/nunit.framework.dll"]
    assert "enabled: 0" in diagnostic["pluginMetadata"][0]["content"]
    assert diagnostic["referencingAssemblies"] == [{"path": "com.unity.test-framework/UnityEditor.TestRunner/UnityEditor.TestRunner.asmdef", "name": "UnityEditor.TestRunner", "includePlatforms": ["Editor"], "precompiledReferences": ["nunit.framework.dll"]}]
    assert not (repo / ".unity-ci/6000.6.4f1").exists()


def test_missing_reference_diagnostic_is_bounded_and_single_line(environment: tuple[Path, Path, Path, Path]) -> None:
    _, data, _, _ = environment
    nunit = data / "Resources/PackageManager/BuiltInPackages/com.unity.ext.nunit"
    (nunit / "net40/unity-custom/nunit.framework.dll").unlink()
    for index in range(30):
        dll = nunit / f"candidate-{index:02}/nunit.framework.dll"
        dll.parent.mkdir()
        dll.write_bytes(b"unverified")
        dll.with_suffix(".dll.meta").write_text("PluginImporter:\n" + "x" * 8000, encoding="utf-8")
    with pytest.raises(packages.PreparationError) as caught:
        prepare(environment)
    message = str(caught.value)
    assert "\n" not in message and "\r" not in message
    diagnostic = json.loads(message.split("; diagnostic=", 1)[1])
    assert len(diagnostic["dllPaths"]) == 20
    assert diagnostic["dllPathsTruncated"] is True
    assert len(diagnostic["pluginMetadata"]) == 4
    assert all(len(item["content"]) <= 4096 and item["truncated"] for item in diagnostic["pluginMetadata"])
    assert len(message) < 20000


@pytest.mark.parametrize("image", ["unity-mcp-editor:6000.7.0b2", "unity-mcp-editor:6000.7.0b2-tests",
                                  "unityci/editor:ubuntu-6000.7.0b2-base-3@sha256:" + "a" * 64,
                                  "unityci/editor:ubuntu-6000.7.0b2-linux-il2cpp-3@sha256:" + "a" * 64])
def test_image_extraction_never_starts_editor_and_cleans_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, image: str) -> None:
    calls: list[list[str]] = []
    def run(command: list[str], **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="b" * 64 + "\n")
    monkeypatch.setattr(packages.subprocess, "run", run)
    packages.copy_image_packages(image, tmp_path / "editor", unity_version="6000.7.0b2")
    assert [call[1] for call in calls] == ["create", "cp", "rm"]
    assert calls[1][2].endswith(":/opt/unity/Editor/Data/Resources/PackageManager/BuiltInPackages")
    assert calls[2] == ["docker", "rm", "b" * 64]


def test_image_copy_failure_still_removes_stopped_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    def run(command: list[str], **kwargs):
        calls.append(command)
        if command[1] == "cp":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="c" * 64)
    monkeypatch.setattr(packages.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        packages.copy_image_packages("unity-mcp-editor:6000.7.0a6", tmp_path / "editor", unity_version="6000.7.0a6")
    assert calls[-1] == ["docker", "rm", "c" * 64]


@pytest.mark.parametrize("image", ["ubuntu:latest", "unityci/editor:latest", "unityci/editor:ubuntu-6000.7.0b2-base-3", "unity-mcp-editor:6000.7.0a6"])
def test_image_validation_rejects_mutable_or_mismatched_provider_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, image: str) -> None:
    monkeypatch.setattr(packages.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected Docker operation"))
    with pytest.raises(packages.PreparationError):
        packages.copy_image_packages(image, tmp_path / "editor", unity_version="6000.7.0b2")


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE), ("package/link", tarfile.SYMTYPE), ("package/hardlink", tarfile.LNKTYPE), ("package/device", tarfile.CHRTYPE)])
def test_registry_extraction_rejects_unsafe_members(tmp_path: Path, name: str, kind: bytes) -> None:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        archive.addfile(member)
    with pytest.raises(packages.PreparationError, match="Unsafe"):
        packages.extract_package(content.getvalue(), tmp_path / "destination")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("version,profile,framework", [
    ("2021.3.45f2", "legacy-lts", "1.4.6"), ("2022.3.76f1", "legacy-lts", "1.4.6"),
    ("6000.0.84f1", "unity-six", "1.6.0"), ("6000.3.25f1", "unity-six", "1.6.0"),
    ("6000.6.4f1", "unity-six", "1.6.0"), ("6000.7.0b2", "unity-six", "1.6.0"),
    ("6000.7.0a6", "unity-six", "1.6.0"),
])
def test_real_profile_matrix_selects_verified_legacy_or_editor_sources(
    environment: tuple[Path, Path, Path, Path], version: str, profile: str, framework: str,
) -> None:
    repo, data, cache, _ = environment
    builtin = data / "Resources/PackageManager/BuiltInPackages"
    config = json.loads(packages.PROFILES.read_text(encoding="utf-8"))
    for name in config["requiredModules"]:
        if not (builtin / name).exists():
            make_package(builtin, name, "1.0.0")
    for name, selected in (("com.unity.test-framework", "1.4.6"), ("com.unity.ext.nunit", "2.0.5")):
        target = cache / f"{name}@{selected}"
        shutil.copytree(builtin / name, target)
        metadata = json.loads((target / "package.json").read_text(encoding="utf-8"))
        metadata.update(version=selected, unity="2019.4")
        (target / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    result = packages.prepare(version, data, repo / ".unity-ci" / version, repo=repo, registry_cache=cache)
    report = json.loads((repo / result.resolution_report).read_text(encoding="utf-8"))
    assert report["profile"] == profile
    resolved = {item["name"]: item for item in report["packages"]}
    assert resolved["com.unity.test-framework"]["version"] == framework
    assert resolved["com.unity.test-framework"]["source"] == ("editor" if profile == "unity-six" else "registry-cache")
    assert {"com.unity.ugui", "com.unity.modules.ai"} <= resolved.keys()
    assert not {"com.unity.ai.navigation", "com.unity.textmeshpro", "com.unity.timeline"} & resolved.keys()


def test_unknown_transitive_dependency_fails_closed(environment: tuple[Path, Path, Path, Path]) -> None:
    repo, data, _, _ = environment
    metadata = data / "Resources/PackageManager/BuiltInPackages/com.unity.test-framework/package.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["dependencies"]["com.unity.unreviewed"] = "1.0.0"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(packages.PreparationError, match="Unprofiled transitive"):
        prepare(environment)
    assert not (repo / ".unity-ci/6000.0.69f1").exists()


def test_registry_archive_integrity_failure_does_not_extract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {"versions": {"3.2.2": {"name": "com.unity.nuget.newtonsoft-json", "version": "3.2.2",
                "dist": {"tarball": "https://packages.unity.com/package.tgz", "shasum": "a" * 40}}}}
    responses = iter((json.dumps(metadata).encode(), b"wrong archive"))
    monkeypatch.setattr(packages, "urlopen", lambda *args, **kwargs: io.BytesIO(next(responses)))
    target = tmp_path / "package"
    with pytest.raises(packages.PreparationError, match="integrity mismatch"):
        packages.fetch_registry_package("com.unity.nuget.newtonsoft-json", "3.2.2", target)
    assert not target.exists()


def test_main_exports_all_outputs_only_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", ["unity_ci_packages.py", "prepare", "--unity-version", "6000.7.0b2",
                                      "--unity-data", str(tmp_path), "--output", ".unity-ci/profile"])
    expected = packages.Preparation("a/refs", "a/framework", "a/project", "a/resolved-packages.json")
    monkeypatch.setattr(packages, "prepare", lambda *args, **kwargs: expected)
    assert packages.main() == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()) == {
        "refs": expected.refs, "test_framework_source": expected.test_framework_source,
        "project_path": expected.project_path, "resolution_report": expected.resolution_report}
    def fail(*args, **kwargs):
        raise packages.PreparationError("missing bundle")
    monkeypatch.setattr(packages, "prepare", fail)
    previous = output.read_bytes()
    assert packages.main() == 1
    assert output.read_bytes() == previous


def test_family_only_minimum_accepts_prerelease_editor(environment: tuple[Path, Path, Path, Path]) -> None:
    _, data, _, _ = environment
    metadata = data / "Resources/PackageManager/BuiltInPackages/com.unity.test-framework/package.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["unity"] = "6000.7"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    assert prepare(environment, "6000.7.0a6").project_path.endswith("/project")
