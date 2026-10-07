"""Offline regression tests for distribution helpers using synthetic trees."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


def load_helper(name):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle_fixture(tmp_path, monkeypatch):
    helper = load_helper("generate_mcpb")
    repo = tmp_path / "repo"
    repo.mkdir()
    template = repo / "manifest.json"
    template.write_text(json.dumps({"name": "example", "version": "0.0.0"}))
    (repo / "LICENSE").write_text("synthetic license")
    (repo / "README.md").write_text("synthetic readme")
    (repo / "excluded.txt").write_text("must not be bundled")
    icon = repo / "icon.png"
    icon.write_bytes(b"synthetic icon")
    monkeypatch.setattr(helper, "REPO_ROOT", repo)
    monkeypatch.setattr(helper, "MANIFEST_TEMPLATE", template)
    return helper, repo, icon, tmp_path / "example.mcpb"


def test_bundle_stages_selected_files_and_replaces_output(bundle_fixture, monkeypatch):
    helper, repo, icon, output = bundle_fixture
    output.write_bytes(b"old bundle")
    versions = []

    def pack(command, *, cwd, **kwargs):
        build = Path(cwd)
        manifest = json.loads((build / "manifest.json").read_text())
        versions.append(manifest["version"])
        assert manifest["icon"] == "icon.png"
        assert (build / manifest["icon"]).read_bytes() == icon.read_bytes()
        assert {path.name for path in build.iterdir()} == {
            "manifest.json",
            "icon.png",
            "LICENSE",
            "README.md",
        }
        Path(command[-1]).write_bytes(manifest["version"].encode())
        return SimpleNamespace(stdout="packed")

    monkeypatch.setattr(helper.subprocess, "run", pack)
    assert helper.generate_mcpb("1.2.3", output, icon) == output
    assert output.read_bytes() == b"1.2.3"
    helper.generate_mcpb("1.2.4", output, icon)
    assert output.read_bytes() == b"1.2.4"
    assert versions == ["1.2.3", "1.2.4"]
    assert json.loads((repo / "manifest.json").read_text())["version"] == "0.0.0"


def test_bundle_cli_requires_an_explicit_icon(bundle_fixture, monkeypatch):
    helper, _, icon, _ = bundle_fixture
    called = []
    monkeypatch.setattr(helper, "DEFAULT_ICON", icon, raising=False)
    monkeypatch.setattr(sys, "argv", ["generate_mcpb", "1.2.3"])
    monkeypatch.setattr(helper, "generate_mcpb", lambda *args: called.append(args))
    with pytest.raises(SystemExit) as error:
        helper.main()
    assert error.value.code == 2
    assert not called


@pytest.mark.parametrize("failure", ["no_output", "empty_output", "pack_error"])
def test_bundle_failure_preserves_existing_output(bundle_fixture, monkeypatch, failure):
    helper, _, icon, output = bundle_fixture
    output.write_bytes(b"old bundle")

    def pack(command, **kwargs):
        if failure != "no_output":
            Path(command[-1]).write_bytes(b"partial" if failure == "pack_error" else b"")
        if failure == "pack_error":
            raise subprocess.CalledProcessError(1, command, stderr="synthetic failure")
        return SimpleNamespace(stdout="packed")

    monkeypatch.setattr(helper.subprocess, "run", pack)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        helper.generate_mcpb("1.2.3", output, icon)
    assert output.read_bytes() == b"old bundle"


@pytest.mark.parametrize("reserved_name", ["manifest.json", "LICENSE", "README.md"])
def test_bundle_rejects_icon_filename_collisions(bundle_fixture, monkeypatch, reserved_name):
    helper, repo, _, output = bundle_fixture
    output.write_bytes(b"old bundle")
    called = []
    monkeypatch.setattr(helper.subprocess, "run", lambda *args, **kwargs: called.append(args))
    with pytest.raises(ValueError, match="Icon filename conflicts"):
        helper.generate_mcpb("1.2.3", output, repo / reserved_name)
    assert not called
    assert output.read_bytes() == b"old bundle"


@pytest.fixture
def asset_fixture(tmp_path):
    helper = load_helper("prepare_unity_asset_store_release")
    repo = tmp_path / "repo"
    source = repo / "MCPForUnity"
    files = {
        "Editor/Setup/SetupWindowService.cs": "[InitializeOnLoad]\nclass Setup {}\n",
        "Editor/MenuItems/MCPForUnityMenu.cs": "class Menu {}\n",
        "Editor/Helpers/HttpEndpointUtility.cs": 'private const string DefaultRemoteBaseUrl = "";\n',
        "Editor/Windows/Components/Connection/McpConnectionSection.cs": (
            "transportDropdown.Init(TransportProtocol.HTTPLocal);\n"
            'scope = MCPServiceLocator.Server.IsLocalUrl() ? "local" : "remote";\n'
        ),
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    project = tmp_path / "upload"
    destination = project / "Assets" / "MCPForUnity"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old package")
    return helper, repo, source, project, destination


def run_asset(helper, monkeypatch, repo, project, *args, url="https://example.test/mcp"):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare",
            "--repo-root",
            str(repo),
            "--asset-project",
            str(project),
            "--remote-url",
            url,
            *args,
        ],
    )
    return helper.main()


def snapshot(path):
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


@pytest.mark.parametrize("defect", ["missing_file", "missing_pattern", "duplicate_pattern"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_asset_validates_edit_preconditions(asset_fixture, monkeypatch, defect, dry_run):
    helper, repo, source, project, destination = asset_fixture
    target = source / "Editor/Helpers/HttpEndpointUtility.cs"
    if defect == "missing_file":
        target.unlink()
    elif defect == "missing_pattern":
        target.write_text("class Other {}\n")
    else:
        target.write_text(target.read_text() * 2)
    source_before = snapshot(source)
    destination_before = snapshot(destination)
    with pytest.raises(RuntimeError):
        run_asset(helper, monkeypatch, repo, project, *(["--dry-run"] if dry_run else []))
    assert snapshot(source) == source_before
    assert snapshot(destination) == destination_before


def test_asset_valid_dry_run_and_repeated_preparation(asset_fixture, monkeypatch):
    helper, repo, source, project, destination = asset_fixture
    source_before = snapshot(source)
    destination_before = snapshot(destination)
    assert run_asset(helper, monkeypatch, repo, project, "--dry-run") == 0
    assert snapshot(source) == source_before
    assert snapshot(destination) == destination_before
    assert run_asset(helper, monkeypatch, repo, project, "--backup") == 0
    prepared = snapshot(destination)
    assert "old.txt" not in prepared
    assert b"[InitializeOnLoad]" not in prepared["Editor/Setup/SetupWindowService.cs"]
    assert b"HTTPRemote" in prepared["Editor/Windows/Components/Connection/McpConnectionSection.cs"]
    backups = list((project / "AssetStoreBackups").iterdir())
    assert len(backups) == 1
    assert snapshot(backups[0]) == destination_before
    assert run_asset(helper, monkeypatch, repo, project) == 0
    assert snapshot(destination) == prepared
    assert snapshot(source) == source_before


def test_asset_dry_run_never_stages_or_writes(asset_fixture, monkeypatch):
    helper, repo, _, project, _ = asset_fixture

    def unexpected(*args, **kwargs):
        pytest.fail("dry-run attempted a filesystem mutation")

    monkeypatch.setattr(helper.shutil, "copytree", unexpected)
    monkeypatch.setattr(helper.tempfile, "mkdtemp", unexpected)
    monkeypatch.setattr(helper, "write_text", unexpected)
    assert run_asset(helper, monkeypatch, repo, project, "--dry-run", "--backup") == 0


def test_asset_url_is_a_literal_csharp_string(asset_fixture, monkeypatch):
    helper, repo, _, project, destination = asset_fixture
    url = 'https://example.test/mcp?label="test"&path=\\1'
    assert run_asset(helper, monkeypatch, repo, project, url=url) == 0
    content = (destination / "Editor/Helpers/HttpEndpointUtility.cs").read_text()
    assert content == f"private const string DefaultRemoteBaseUrl = {json.dumps(url)};\n"


def test_asset_copy_failure_preserves_existing_destination(asset_fixture, monkeypatch):
    helper, repo, source, project, destination = asset_fixture
    destination_before = snapshot(destination)
    original_copytree = helper.shutil.copytree

    def fail_staging_copy(src, dst, *args, **kwargs):
        if Path(src) == source:
            raise OSError("synthetic staging copy failure")
        return original_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(helper.shutil, "copytree", fail_staging_copy)
    with pytest.raises(OSError, match="synthetic staging copy failure"):
        run_asset(helper, monkeypatch, repo, project)
    assert snapshot(destination) == destination_before
    assert (source / "Editor/Helpers/HttpEndpointUtility.cs").is_file()


def test_asset_final_copy_failure_preserves_or_replaces_complete_package(
    asset_fixture, monkeypatch
):
    helper, repo, source, project, destination = asset_fixture
    source_before = snapshot(source)
    before = snapshot(destination)
    original_copytree = helper.shutil.copytree

    def fail_final_copy(src, dst, *args, **kwargs):
        if Path(dst) == destination:
            raise OSError("synthetic final copy failure")
        return original_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(helper.shutil, "copytree", fail_final_copy)
    try:
        run_asset(helper, monkeypatch, repo, project)
    except OSError:
        assert snapshot(destination) == before
    else:
        prepared = {
            name: content.replace(b"\r\n", b"\n") for name, content in snapshot(destination).items()
        }
        expected = {
            name: content.replace(b"\r\n", b"\n") for name, content in source_before.items()
        }
        expected["Editor/Setup/SetupWindowService.cs"] = b"class Setup {}\n"
        expected["Editor/Helpers/HttpEndpointUtility.cs"] = (
            b'private const string DefaultRemoteBaseUrl = "https://example.test/mcp";\n'
        )
        expected["Editor/Windows/Components/Connection/McpConnectionSection.cs"] = (
            b'transportDropdown.Init(TransportProtocol.HTTPRemote);\nscope = "remote";\n'
        )
        assert prepared == expected
    assert snapshot(source) == source_before


@pytest.mark.parametrize("has_previous", [True, False])
def test_asset_install_failure_restores_existing_destination(
    asset_fixture, monkeypatch, has_previous
):
    helper, repo, source, project, destination = asset_fixture
    if not has_previous:
        (destination / "old.txt").unlink()
        destination.rmdir()
    source_before = snapshot(source)
    destination_before = snapshot(destination)
    original_rename = Path.rename
    called = []

    def fail_install(path, target):
        if Path(target) == destination and path.name == "MCPForUnity":
            called.append(path)
            raise OSError("synthetic install failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(OSError, match="synthetic install failure"):
        run_asset(helper, monkeypatch, repo, project)
    assert len(called) == 1
    assert snapshot(destination) == destination_before
    assert snapshot(source) == source_before
    assert not list(project.glob("mcpforunity_assetstore_*"))
    assert destination.exists() == has_previous


def test_asset_restore_failure_retains_original_tree(asset_fixture, monkeypatch):
    helper, repo, _, project, destination = asset_fixture
    before = snapshot(destination)
    original_rename = Path.rename

    def fail_install_and_restore(path, target):
        if Path(target) == destination:
            raise OSError("synthetic rename failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_install_and_restore)
    with pytest.raises(RuntimeError, match="preserved at"):
        run_asset(helper, monkeypatch, repo, project)
    retained = list(project.glob("mcpforunity_assetstore_*/previous"))
    assert len(retained) == 1
    assert snapshot(retained[0]) == before


def test_asset_restore_interruption_retains_original_tree(asset_fixture, monkeypatch):
    helper, repo, _, project, destination = asset_fixture
    before = snapshot(destination)
    original_rename = Path.rename

    def interrupt_restore(path, target):
        if Path(target) == destination:
            if path.name == "previous":
                raise KeyboardInterrupt("synthetic restore interruption")
            raise OSError("synthetic install failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", interrupt_restore)
    with pytest.raises(KeyboardInterrupt, match="synthetic restore interruption"):
        run_asset(helper, monkeypatch, repo, project)
    retained = list(project.glob("mcpforunity_assetstore_*/previous"))
    assert len(retained) == 1
    assert snapshot(retained[0]) == before


def test_asset_backup_failure_preserves_destination(asset_fixture, monkeypatch):
    helper, repo, source, project, destination = asset_fixture
    before = snapshot(destination)
    source_before = snapshot(source)

    def fail_backup(*args):
        raise OSError("synthetic backup failure")

    monkeypatch.setattr(helper, "backup_dir", fail_backup)
    with pytest.raises(OSError, match="synthetic backup failure"):
        run_asset(helper, monkeypatch, repo, project, "--backup")
    assert snapshot(destination) == before
    assert snapshot(source) == source_before
    assert not list(project.glob("mcpforunity_assetstore_*"))


def test_asset_overlap_is_rejected_without_changes(asset_fixture, monkeypatch):
    helper, repo, source, _, _ = asset_fixture
    (source / "Assets").mkdir()
    before = snapshot(source)
    with pytest.raises(RuntimeError, match="must not overlap"):
        run_asset(helper, monkeypatch, repo, source, "--dry-run")
    assert snapshot(source) == before
