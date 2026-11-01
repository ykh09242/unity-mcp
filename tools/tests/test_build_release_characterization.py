"""Exercise production release helpers using disposable files and a fake packer.

Component orchestration and immutable README pins are covered by test_update_versions;
Python artifact selection and publisher invocation are covered by test_pypi_publish.
Placeholder checklists and copied filesystem/regex logic are replaced by production
calls here. No packer, Unity or server is launched.
"""

import asyncio
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tools import generate_mcpb as bundle
from tools import prepare_unity_asset_store_release as asset_store
from tools import stress_mcp as stress
from tools import update_versions as versions


@pytest.fixture
def version_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, "REPO_ROOT", tmp_path)
    for attribute, relative, content in (
        ("PACKAGE_JSON", "MCPForUnity/package.json", '{"version":"1.0.0","keep":true}\n'),
        ("MANIFEST_JSON", "manifest.json", '{"version":"1.0.0","keep":true}\n'),
        ("PYPROJECT_TOML", "Server/pyproject.toml", '[project]\nversion = "1.0.0"\n'),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(versions, attribute, path)
    return tmp_path


def test_load_package_version_reads_production_metadata(version_repo):
    assert versions.load_package_version() == "1.0.0"


@pytest.mark.parametrize(
    "content,error",
    [(None, FileNotFoundError), ("{}", ValueError), ("{malformed", json.JSONDecodeError)],
)
def test_load_package_version_rejects_missing_or_invalid_metadata(version_repo, content, error):
    path = versions.PACKAGE_JSON
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(error):
        versions.load_package_version()


@pytest.mark.parametrize(
    "attribute,update",
    [
        ("PACKAGE_JSON", versions.update_package_json),
        ("MANIFEST_JSON", versions.update_manifest_json),
        ("PYPROJECT_TOML", versions.update_pyproject_toml),
    ],
)
def test_version_helpers_preview_write_and_preserve_unrelated_data(version_repo, attribute, update):
    path = getattr(versions, attribute)
    before = path.read_bytes()
    assert update("2.3.4", dry_run=True) is True
    assert path.read_bytes() == before
    assert update("2.3.4") is True
    if path.suffix == ".json":
        assert json.loads(path.read_text(encoding="utf-8")) == {"version": "2.3.4", "keep": True}
    else:
        assert path.read_text(encoding="utf-8") == '[project]\nversion = "2.3.4"\n'
    after = path.read_bytes()
    assert update("2.3.4") is False
    assert path.read_bytes() == after


@pytest.fixture
def bundle_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "REPO_ROOT", tmp_path)
    template = tmp_path / "manifest.json"
    template.write_text('{"version":"old","icon":"old.png","name":"fixture"}', encoding="utf-8")
    monkeypatch.setattr(bundle, "MANIFEST_TEMPLATE", template)
    for name in ("LICENSE", "README.md", "icon.png"):
        (tmp_path / name).write_bytes(f"owned {name}".encode())
    return tmp_path


def test_manifest_injects_version_and_icon_without_changing_template(bundle_repo):
    before = bundle.MANIFEST_TEMPLATE.read_bytes()
    assert bundle.create_manifest("2.3.4", "icon.png") == {
        "version": "2.3.4",
        "icon": "icon.png",
        "name": "fixture",
    }
    assert bundle.MANIFEST_TEMPLATE.read_bytes() == before


@pytest.mark.parametrize("mode", ["missing", "invalid"])
def test_manifest_rejects_missing_or_invalid_template(bundle_repo, mode):
    if mode == "missing":
        bundle.MANIFEST_TEMPLATE.unlink()
    else:
        bundle.MANIFEST_TEMPLATE.write_text("{invalid", encoding="utf-8")
    with pytest.raises(FileNotFoundError if mode == "missing" else json.JSONDecodeError):
        bundle.create_manifest("2.3.4", "icon.png")


def test_bundle_stages_manifest_icon_license_and_readme_before_pack(bundle_repo, monkeypatch):
    stages = []

    def pack(command, *, cwd, capture_output, text, check):
        stages.append(cwd)
        assert command[:4] == ["npx", "@anthropic-ai/mcpb", "pack", "."]
        assert capture_output and text and check
        manifest = json.loads((cwd / "manifest.json").read_text(encoding="utf-8"))
        assert manifest == {"version": "2.3.4", "icon": "icon.png", "name": "fixture"}
        for name in ("LICENSE", "README.md", "icon.png"):
            assert (cwd / name).read_bytes() == (bundle_repo / name).read_bytes()
        Path(command[-1]).write_bytes(b"packed bundle")
        return subprocess.CompletedProcess(command, 0, stdout="packed")

    monkeypatch.setattr(bundle.subprocess, "run", pack)
    output = bundle_repo / "output.mcpb"
    assert bundle.generate_mcpb("2.3.4", output, bundle_repo / "icon.png") == output
    assert output.read_bytes() == b"packed bundle"
    assert len(stages) == 1 and not stages[0].exists()


@pytest.mark.parametrize("failure", ["packer", "missing-npx", "missing-output", "empty-output"])
def test_bundle_failure_preserves_existing_output_and_removes_stage(
    bundle_repo, monkeypatch, failure
):
    stages = []

    def pack(command, *, cwd, **_kwargs):
        stages.append(cwd)
        if failure == "packer":
            raise subprocess.CalledProcessError(1, command, stderr="fixture pack failed")
        if failure == "missing-npx":
            raise FileNotFoundError("fixture npx unavailable")
        if failure == "empty-output":
            Path(command[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, stdout="packed")

    monkeypatch.setattr(bundle.subprocess, "run", pack)
    output = bundle_repo / "output.mcpb"
    output.write_bytes(b"previous bundle")
    error = {"packer": subprocess.CalledProcessError, "missing-npx": FileNotFoundError}.get(
        failure, RuntimeError
    )
    with pytest.raises(error):
        bundle.generate_mcpb("2.3.4", output, bundle_repo / "icon.png")
    assert output.read_bytes() == b"previous bundle"
    assert len(stages) == 1 and not stages[0].exists()


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("dry_run", [False, True])
def test_asset_store_replacement_requires_exactly_one_match(tmp_path, count, dry_run):
    path = tmp_path / "Helper.cs"
    before = "target\n" * count
    path.write_text(before, encoding="utf-8")
    if count == 1:
        asset_store.replace_once(path, r"^target$", "replacement", dry_run=dry_run)
        assert path.read_text(encoding="utf-8") == (before if dry_run else "replacement\n")
    else:
        with pytest.raises(RuntimeError, match="expected 1 replacement"):
            asset_store.replace_once(path, r"^target$", "replacement", dry_run=dry_run)
        assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("dry_run", [False, True])
def test_asset_store_legacy_line_removal_is_optional_but_unambiguous(tmp_path, count, dry_run):
    path = tmp_path / "Setup.cs"
    before = "  [InitializeOnLoad]\n" * count + "class Setup {}\n"
    path.write_text(before, encoding="utf-8")
    if count == 2:
        with pytest.raises(RuntimeError, match="expected at most 1 line"):
            asset_store.remove_optional_line_exact(path, "[InitializeOnLoad]", dry_run=dry_run)
        expected = before
    else:
        asset_store.remove_optional_line_exact(path, "[InitializeOnLoad]", dry_run=dry_run)
        expected = before if dry_run else "class Setup {}\n"
    assert path.read_text(encoding="utf-8") == expected


@pytest.fixture
def asset_project(tmp_path):
    source = tmp_path / "repo/MCPForUnity"
    files = {
        "Editor/Setup/SetupWindowService.cs": "[InitializeOnLoad]\nclass Setup {}\n",
        "Editor/MenuItems/MCPForUnityMenu.cs": "class Menu {}\n",
        "Editor/Helpers/HttpEndpointUtility.cs": 'private const string DefaultRemoteBaseUrl = "";\n',
        "Editor/Windows/Components/Connection/McpConnectionSection.cs": (
            "transportDropdown.Init(TransportProtocol.HTTPLocal);\n"
            'scope = MCPServiceLocator.Server.IsLocalUrl() ? "local" : "remote";\n'
        ),
    }
    for relative, text in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    project = tmp_path / "project"
    destination = project / "Assets/MCPForUnity"
    destination.mkdir(parents=True)
    (destination / "existing.txt").write_bytes(b"previous package")
    return source, project, files


@pytest.mark.parametrize("dry_run", [False, True])
def test_asset_store_cli_stages_edits_preserves_source_and_backs_up_previous_package(
    asset_project, monkeypatch, dry_run
):
    source, project, files = asset_project
    url = "https://fixture.invalid/mcp"
    arguments = [
        "prepare",
        "--repo-root",
        str(source.parent),
        "--asset-project",
        str(project),
        "--remote-url",
        url,
        "--backup",
    ]
    monkeypatch.setattr(sys, "argv", arguments + (["--dry-run"] if dry_run else []))
    assert asset_store.main() == 0
    assert {
        relative: (source / relative).read_text(encoding="utf-8") for relative in files
    } == files
    destination = project / "Assets/MCPForUnity"
    if dry_run:
        assert (destination / "existing.txt").read_bytes() == b"previous package"
        assert not (project / "AssetStoreBackups").exists()
    else:
        assert not (destination / "existing.txt").exists()
        assert (
            "[InitializeOnLoad]"
            not in (destination / "Editor/Setup/SetupWindowService.cs").read_text()
        )
        assert (
            json.dumps(url) in (destination / "Editor/Helpers/HttpEndpointUtility.cs").read_text()
        )
        connection = (
            destination / "Editor/Windows/Components/Connection/McpConnectionSection.cs"
        ).read_text()
        assert "TransportProtocol.HTTPRemote" in connection and 'scope = "remote";' in connection
        backups = list((project / "AssetStoreBackups").glob("*/existing.txt"))
        assert len(backups) == 1 and backups[0].read_bytes() == b"previous package"
    assert not list(project.glob("mcpforunity_assetstore_*"))


def test_asset_store_missing_source_is_rejected_before_destination_changes(asset_project):
    source, project, _ = asset_project
    (source / "Editor/Helpers/HttpEndpointUtility.cs").unlink()
    with pytest.raises(RuntimeError, match="Expected file not found"):
        asset_store.apply_asset_store_edits(source, "https://fixture.invalid", dry_run=True)
    assert (project / "Assets/MCPForUnity/existing.txt").read_bytes() == b"previous package"


def test_stress_discovery_reads_only_disposable_status_files(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    assert stress.discover_port(None) == 6400
    (tmp_path / "unity-mcp-status-fixture.json").write_text(
        json.dumps({"unity_port": 6421, "project_path": "/owned/project"}), encoding="utf-8"
    )
    assert stress.discover_port("/owned/project") == 6421
    assert stress.discover_port("/other/project") == 6400


@pytest.mark.asyncio
async def test_stress_real_frame_reader_and_writer_preserve_framing():
    writes = []

    class Writer:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            pass

    await stress.write_frame(Writer(), stress.make_ping_frame())
    assert writes == [struct.pack(">Q", 4), b"ping"]
    reader = asyncio.StreamReader()
    reader.feed_data(b"".join(writes))
    reader.feed_eof()
    assert await stress.read_frame(reader) == b"ping"
    with pytest.raises(ConnectionError, match="closed while reading"):
        await stress.read_exact(reader, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [0, 64 * 1024 * 1024 + 1])
async def test_stress_rejects_invalid_frame_length(length):
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">Q", length))
    reader.feed_eof()
    with pytest.raises(ValueError, match="Invalid frame length"):
        await stress.read_frame(reader)


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [False, True])
async def test_stress_handshake_validates_actual_wire_line(valid):
    reader = asyncio.StreamReader()
    reader.feed_data(b"WELCOME UNITY-MCP 1 FRAMING=1\n" if valid else b"invalid\n")
    reader.feed_eof()
    if valid:
        await stress.do_handshake(reader)
    else:
        with pytest.raises(ConnectionError, match="Unexpected handshake"):
            await stress.do_handshake(reader)
