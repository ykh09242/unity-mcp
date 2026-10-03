"""Hermetic contracts for the public compiler-input cache."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest


TOOLS = Path(__file__).resolve().parents[1]
for name in ("unity_ci", "unity_compile_cache"):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
cache = sys.modules["unity_compile_cache"]
unity_ci = sys.modules["unity_ci"]
VERSION = "6000.3.25f1"
BASE = "unityci/base:ubuntu-3.2.2@sha256:" + "b" * 64
IMAGE = f"unityci/editor:ubuntu-{VERSION}-base-3@sha256:" + "a" * 64


@pytest.fixture
def environment(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cache, "ROOT", repo)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    row = unity_ci.Version(VERSION, "lts", IMAGE, None, None, ())
    manifest = unity_ci.Manifest(VERSION, BASE, (row,))
    image_data = tmp_path / "public-image-data"
    files = {
        "Managed/UnityEngine.dll": b"unity refs",
        "NetStandard/ref/2.1.0/netstandard.dll": b"netstandard refs",
        "UnityReferenceAssemblies/unity-4.8-api/mscorlib.dll": b"bcl refs",
        "DotNetSdkRoslyn/csc.dll": b"compiler",
        "DotNetSdkRoslyn/csc.runtimeconfig.json": b"{}",
        "NetCoreRuntime/dotnet": b"runtime binary",
        "NetCoreRuntime/shared/Microsoft.NETCore.App/6.0/libhostpolicy.so": b"runtime dependency",
        "Tools/Compilation/ApiUpdater/Mono.Cecil.dll": b"cecil",
        "Tools/ScriptUpdater/Mono.Cecil.Rocks.dll": b"legacy cecil",
    }
    for relative, content in files.items():
        target = image_data / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (image_data / "NetCoreRuntime/dotnet").chmod(0o755)
    for name in ("com.unity.modules.ui", "com.unity.modules.imgui", *cache.PACKAGES,
                 "com.unity.ide.rider", "com.unity.nuget.newtonsoft-json"):
        path = image_data / cache.BUILTINS / name
        path.mkdir(parents=True)
        (path / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
        (path / "Vendor.cs").write_text("class Vendor {}", encoding="utf-8")
    forbidden = image_data / "Resources/PackageManager/ProjectTemplates/libcache/Editor.dll"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"template not cached")
    (image_data.parent / "Unity").write_bytes(b"editor executable not cached")
    return repo, manifest, image_data


def install_docker(monkeypatch, image_data, failure=None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "run":
            assert "--network" in args and args[args.index("--network") + 1] == "none"
            assert "--entrypoint" in args and args[-1] == cache.INVENTORY
            assert not any(value.startswith("--volume") for value in args)
            directories = [value for value in cache.DIRECTORIES if (image_data / value).is_dir()]
            directories += [path.relative_to(image_data).as_posix() for path in image_data.rglob("DotNetSdk") if path.is_dir()]
            directories += [path.relative_to(image_data).as_posix() for path in (image_data / cache.BUILTINS).iterdir()
                            if path.name in cache.PACKAGES or path.name.startswith("com.unity.modules.")]
            return subprocess.CompletedProcess(args, 0, "\n".join(cache.IMAGE_DATA + "/" + value for value in directories))
        if args[1] == "create":
            return subprocess.CompletedProcess(args, 0, "c" * 64 + "\n")
        if args[1] == "cp":
            if failure:
                raise failure
            relative = args[2].split(cache.IMAGE_DATA + "/", 1)[1]
            shutil.copytree(image_data / relative, Path(args[3]), symlinks=True)
            return subprocess.CompletedProcess(args, 0)
        if args[1] == "rm":
            assert args == ["docker", "rm", "-f", "c" * 64]
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError(f"Unexpected Docker call: {args}")

    monkeypatch.setattr(cache.subprocess, "run", run)
    return calls


def populate(environment, monkeypatch):
    repo, manifest, image_data = environment
    calls = install_docker(monkeypatch, image_data)
    result = cache.prepare(manifest, VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    return repo / ".unity-ci-sdk" / VERSION, result, calls


def write_manifest(repo, manifest):
    path = repo / "versions.json"
    path.write_text(json.dumps({"defaultVersion": VERSION, "previewBaseImage": BASE,
        "versions": [{"id": VERSION, "channel": "lts", "role": "lts", "image": manifest.versions[0].image}]}), encoding="utf-8")
    return path


def test_population_is_narrow_preserves_structure_and_modes(environment, monkeypatch):
    directory, result, calls = populate(environment, monkeypatch)
    _, manifest, source = environment
    assert result == {"unity_data": f".unity-ci-sdk/{VERSION}/Data", "runtime_image": BASE, "populated": True}
    assert (directory / "Data/NetCoreRuntime/shared/Microsoft.NETCore.App/6.0/libhostpolicy.so").read_bytes() == b"runtime dependency"
    assert stat.S_IMODE((directory / "Data/NetCoreRuntime/dotnet").stat().st_mode) == stat.S_IMODE((source / "NetCoreRuntime/dotnet").stat().st_mode)
    builtin = directory / "Data" / cache.BUILTINS
    assert {path.name for path in builtin.iterdir()} == {"com.unity.modules.ui", "com.unity.modules.imgui", *cache.PACKAGES}
    assert not (directory / "Data/Resources/PackageManager/ProjectTemplates").exists()
    assert not (directory / "Unity").exists()
    assert not (directory / "Data/PlaybackEngines").exists()
    assert calls[-1][1] == "rm"
    cache._validate(directory, cache.identity(manifest, VERSION))


def test_valid_hit_calls_no_docker_or_image_preparation(environment, monkeypatch):
    directory, _, _ = populate(environment, monkeypatch)
    docker = Mock(side_effect=AssertionError("cache hit must call no Docker"))
    preparation = Mock(side_effect=AssertionError("cache hit must prepare no image"))
    monkeypatch.setattr(cache.subprocess, "run", docker)
    monkeypatch.setattr(cache.unity_ci, "prepare", preparation)
    assert cache.prepare(environment[1], VERSION, directory)["populated"] is False
    docker.assert_not_called()
    preparation.assert_not_called()


def test_key_ignores_repo_sources_profiles_and_unrelated_versions(environment):
    repo, manifest, _ = environment
    first = cache.identity(manifest, VERSION)["cache_key"]
    for relative in ("MCPForUnity/Runtime/Changed.cs", "tools/compile-refs/Editor.txt", "tools/unity-ci-packages.json"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("modified unrelated content", encoding="utf-8")
    unrelated = unity_ci.Version("2021.3.45f2", "lts", "unrelated image", None, None, ())
    changed = replace(manifest, default_version=unrelated.id, versions=(*manifest.versions, unrelated))
    assert cache.identity(changed, VERSION)["cache_key"] == first


def test_selected_editor_identity_and_extractor_change_key(environment, monkeypatch):
    _, manifest, _ = environment
    initial = cache.identity(manifest, VERSION)["cache_key"]
    changed = replace(manifest, versions=(replace(manifest.versions[0], image=IMAGE[:-64] + "d" * 64),))
    assert cache.identity(changed, VERSION)["cache_key"] != initial
    monkeypatch.setattr(cache, "SCHEMA", cache.SCHEMA + 1)
    assert cache.identity(manifest, VERSION)["cache_key"] != initial


def test_preview_identity_uses_only_verified_editor_archive(environment, monkeypatch):
    _, manifest, _ = environment
    version = "6000.7.0b2"
    download = unity_ci.EditorDownload("https://download.unity3d.com/download_unity/012345abcdef/LinuxEditorInstaller/Unity-6000.7.0b2.tar.xz", "c" * 32, 42)
    row = unity_ci.Version(version, "beta", None, download, None, ())
    manifest = replace(manifest, default_version=version, versions=(row,))
    initial = cache.identity(manifest, version)["cache_key"]
    module = unity_ci.TestModule("linux-server", download, "Editor/Data/PlaybackEngines/LinuxStandaloneSupport")
    assert cache.identity(replace(manifest, versions=(replace(row, test_modules=(module,)),)), version)["cache_key"] == initial
    for archive in (replace(download, md5="d" * 32), replace(download, size=43),
                    replace(download, url=download.url.replace("012345abcdef", "abcdef012345"))):
        assert cache.identity(replace(manifest, versions=(replace(row, download=archive),)), version)["cache_key"] != initial


@pytest.mark.parametrize("damage", ["partial", "corrupt", "extra", "source", "receipt", "mode"])
def test_invalid_cache_fails_closed_without_docker(environment, monkeypatch, damage):
    directory, _, _ = populate(environment, monkeypatch)
    manifest = environment[1]
    binary = directory / "Data/NetCoreRuntime/dotnet"
    if damage == "partial":
        binary.unlink()
    elif damage == "corrupt":
        binary.write_bytes(b"same-length!!!")
    elif damage == "extra":
        (directory / "Data/Editor.exe").write_bytes(b"forbidden")
    elif damage == "source":
        manifest = replace(manifest, versions=(replace(manifest.versions[0], image=IMAGE[:-64] + "d" * 64),))
    elif damage == "receipt":
        (directory / cache.RECEIPT).write_text("{}", encoding="utf-8")
    else:
        receipt = json.loads((directory / cache.RECEIPT).read_text(encoding="utf-8"))
        receipt["files"]["NetCoreRuntime/dotnet"]["mode"] ^= 0o111
        (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    docker = Mock(side_effect=AssertionError("invalid cache must not be used"))
    monkeypatch.setattr(cache.subprocess, "run", docker)
    with pytest.raises(ValueError, match="remove only this exact version directory"):
        cache.prepare(manifest, VERSION, directory)
    docker.assert_not_called()
    assert directory.exists()


@pytest.mark.parametrize("bad_path", ["outside", ".unity-ci-sdk/other", f".unity-ci-sdk/../.unity-ci-sdk/{VERSION}"])
def test_output_guard_rejects_other_paths_before_docker(environment, monkeypatch, bad_path):
    docker = Mock()
    monkeypatch.setattr(cache.subprocess, "run", docker)
    with pytest.raises(ValueError, match="exactly"):
        cache.prepare(environment[1], VERSION, Path(bad_path))
    docker.assert_not_called()


def test_failed_copy_removes_container_and_staging_without_receipt_or_outputs(environment, monkeypatch, capsys):
    repo, manifest, image_data = environment
    failure = subprocess.CalledProcessError(1, ["docker", "cp"])
    calls = install_docker(monkeypatch, image_data, failure)
    output = repo / "github-output"
    output.write_text("earlier=value\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    path = write_manifest(repo, manifest)
    assert cache.main(["prepare", VERSION, "--output", f".unity-ci-sdk/{VERSION}", "--manifest", str(path)]) == 1
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8") == "earlier=value\n"
    assert calls[-1][1] == "rm"
    assert list((repo / ".unity-ci-sdk").iterdir()) == []


def test_cli_publishes_only_after_atomic_success(environment, monkeypatch, capsys):
    repo, manifest, image_data = environment
    install_docker(monkeypatch, image_data)
    output = repo / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    path = write_manifest(repo, manifest)
    original_rename = Path.rename

    def rename(source, target):
        assert not output.exists()
        assert (source / cache.RECEIPT).is_file()
        assert not target.exists()
        return original_rename(source, target)

    monkeypatch.setattr(Path, "rename", rename)
    assert cache.main(["prepare", VERSION, "--output", f".unity-ci-sdk/{VERSION}", "--manifest", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["populated"] is True
    assert output.read_text(encoding="utf-8") == f"unity_data=.unity-ci-sdk/{VERSION}/Data\nruntime_image={BASE}\npopulated=true\n"


def test_identity_cli_needs_no_docker(environment, monkeypatch, capsys):
    repo, manifest, _ = environment
    path = write_manifest(repo, manifest)
    output = repo / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(cache.subprocess, "run", Mock(side_effect=AssertionError("no Docker identity")))
    assert cache.main(["identity", VERSION, "--manifest", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cache_path"] == f".unity-ci-sdk/{VERSION}"
    assert output.read_text(encoding="utf-8") == f"cache_key={result['cache_key']}\ncache_path={result['cache_path']}\n"


def test_nested_modern_sdk_preserves_relative_layout(environment, monkeypatch):
    repo, manifest, image_data = environment
    shutil.rmtree(image_data / "DotNetSdkRoslyn")
    shutil.rmtree(image_data / "NetCoreRuntime")
    sdk = image_data / "Tools/Scripting/DotNetSdk"
    compiler = sdk / "sdk/9.0.100/Roslyn/bincore/csc.dll"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"modern compiler")
    (sdk / "dotnet").write_bytes(b"modern runtime")
    (sdk / "dotnet").chmod(0o755)
    install_docker(monkeypatch, image_data)
    cache.prepare(manifest, VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    copied = repo / ".unity-ci-sdk" / VERSION / "Data/Tools/Scripting/DotNetSdk"
    assert (copied / "sdk/9.0.100/Roslyn/bincore/csc.dll").read_bytes() == b"modern compiler"
    assert (copied / "dotnet").read_bytes() == b"modern runtime"


@pytest.mark.parametrize("entry", ["/opt/unity/Editor/Data/Resources/secret/DotNetSdk",
    "/opt/unity/Editor/Data/../Unity", "/opt/unity/Editor/Data/PlaybackEngines/Linux",
    "/host/secrets", "/opt/unity/Editor/Data/Resources/PackageManager/BuiltInPackages/com.unity.ide.rider"])
def test_inventory_rejects_non_public_input_scope(environment, monkeypatch, entry):
    monkeypatch.setattr(cache.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, entry)))
    with pytest.raises(ValueError, match="Unexpected"):
        cache.prepare(environment[1], VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    assert list((environment[0] / ".unity-ci-sdk").iterdir()) == []


def test_missing_required_input_never_publishes_receipt(environment, monkeypatch):
    repo, manifest, image_data = environment
    shutil.rmtree(image_data / "NetStandard")
    calls = install_docker(monkeypatch, image_data)
    with pytest.raises(ValueError, match="Required compiler references missing"):
        cache.prepare(manifest, VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    assert calls[-1][1] == "rm"
    assert list((repo / ".unity-ci-sdk").iterdir()) == []


def test_linked_cache_output_is_rejected(environment, monkeypatch, tmp_path):
    repo, manifest, _ = environment
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / ".unity-ci-sdk").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit unprivileged symbolic links")
    docker = Mock()
    monkeypatch.setattr(cache.subprocess, "run", docker)
    with pytest.raises(ValueError, match="Linked cache output"):
        cache.prepare(manifest, VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    docker.assert_not_called()
    assert list(outside.iterdir()) == []


def test_linked_public_input_never_publishes_receipt(environment, monkeypatch, tmp_path):
    repo, manifest, image_data = environment
    outside = tmp_path / "external-file"
    outside.write_bytes(b"not a public compiler input")
    link = image_data / "Managed/External.dll"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit unprivileged symbolic links")
    calls = install_docker(monkeypatch, image_data)
    with pytest.raises(ValueError, match="Linked compiler input"):
        cache.prepare(manifest, VERSION, Path(f".unity-ci-sdk/{VERSION}"))
    assert calls[-1][1] == "rm"
    assert list((repo / ".unity-ci-sdk").iterdir()) == []


@pytest.mark.parametrize("directories", [{}, [None], ["../Editor"], ["Managed", "Managed"]])
def test_malformed_receipt_directories_fail_closed(environment, monkeypatch, directories):
    directory, _, _ = populate(environment, monkeypatch)
    receipt = json.loads((directory / cache.RECEIPT).read_text(encoding="utf-8"))
    receipt["directories"] = directories
    (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(cache.subprocess, "run", Mock(side_effect=AssertionError("corrupt cache must call no Docker")))
    with pytest.raises(ValueError, match="Invalid compiler cache"):
        cache.prepare(environment[1], VERSION, directory)


def test_failed_atomic_rename_never_publishes_outputs(environment, monkeypatch):
    repo, manifest, image_data = environment
    install_docker(monkeypatch, image_data)
    output = repo / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(Path, "rename", Mock(side_effect=OSError("atomic rename failed")))
    path = write_manifest(repo, manifest)
    assert cache.main(["prepare", VERSION, "--output", f".unity-ci-sdk/{VERSION}", "--manifest", str(path)]) == 1
    assert not output.exists()
    assert list((repo / ".unity-ci-sdk").iterdir()) == []


def test_cleanup_guard_preserves_unrelated_directory(environment, tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing cleanup"):
        cache._remove_staging(unrelated, environment[0] / ".unity-ci-sdk" / VERSION)
    assert (unrelated / "keep").read_text(encoding="utf-8") == "keep"
