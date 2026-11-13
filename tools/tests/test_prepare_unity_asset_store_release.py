"""Validate packaged remote endpoint before any destination staging."""

from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/prepare_unity_asset_store_release.py"


@pytest.mark.parametrize(
    "url,accepted",
    [
        ("https://example.com/proxy/mcp?x=1", True),
        ("example.com/proxy", True),
        ("https://0xstudio.example", True),
        ("https://bücher.example", True),
        ("https://localhost:8080", True),
        ("https://127.0.0.1", True),
        ("https://[::1]", True),
        ("http://localhost:8080", False),
        ("http://example.com", False),
        ("https://0.0.0.0", False),
        ("https://[::]", False),
        ("https://[::1]junk", False),
        ("https://[::1]:443junk", False),
        ("https://0x0", False),
        ("https://0", False),
        ("https://foo..example", False),
        ("https://user:secret@example.com", False),
        ("https://example.com/#fragment", False),
        ("ftp://example.com", False),
        ("https://example.com:99999", False),
        ('https://example.com/";bad', False),
        ("https://example.com/\nattack", False),
    ],
)
def test_dry_run_validates_default_remote_policy_before_staging(tmp_path, url, accepted):
    project = tmp_path / "project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--asset-project",
            str(project),
            "--remote-url",
            url,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == accepted, result.stderr
    assert list(assets.iterdir()) == []
    assert list(project.iterdir()) == [assets]


def test_invalid_endpoint_fails_before_staging_without_dry_run(tmp_path):
    assets = tmp_path / "Assets"
    assets.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--asset-project",
            str(tmp_path),
            "--remote-url",
            "http://localhost",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == [assets]


@pytest.mark.parametrize("host", ["0xstudio.example", "bücher.example"])
def test_real_staging_emits_validated_scheme_less_default(tmp_path, host):
    repo = tmp_path / "repo"
    source = repo / "MCPForUnity"
    for relative in (
        "Editor/Setup/SetupWindowService.cs",
        "Editor/MenuItems/MCPForUnityMenu.cs",
        "Editor/Helpers/HttpEndpointUtility.cs",
        "Editor/Windows/Components/Connection/McpConnectionSection.cs",
    ):
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "MCPForUnity" / relative, destination)
    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--asset-project",
            str(project),
            "--remote-url",
            f"{host}/proxy",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    staged = project / "Assets/MCPForUnity/Editor/Helpers/HttpEndpointUtility.cs"
    expected = json.dumps(f"https://{host}/proxy")
    assert f"private const string DefaultRemoteBaseUrl = {expected};" in staged.read_text(
        encoding="utf-8"
    )
    assert 'private const string DefaultRemoteBaseUrl = "";' in (
        source / "Editor/Helpers/HttpEndpointUtility.cs"
    ).read_text(encoding="utf-8")
