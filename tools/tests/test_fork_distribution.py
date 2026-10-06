"""Checked-in distribution metadata must select one immutable fork server."""

import json
from pathlib import Path
import re

from packaging.specifiers import SpecifierSet
from packaging.requirements import Requirement
import pytest
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_upm_and_bundle_use_the_same_commit_archive_without_git_checkout():
    package = json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))
    bundle = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    source = package["mcpServerSource"]
    assert re.fullmatch(
        r"https://github\.com/ykh09242/unity-mcp/archive/[0-9a-f]{40}\.zip#subdirectory=Server",
        source,
    )
    assert bundle["server"]["mcp_config"]["args"] == [
        "--python", ">=3.11", "--from", source, "mcp-for-unity",
    ]


def test_fork_package_has_distinct_identity_and_maintainer():
    package = json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))
    bundle = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert package["name"] == "com.ykh09242.unity-mcp"
    assert package["author"]["name"] == bundle["author"]["name"] == "ykh09242"
    assert bundle["repository"]["url"] == "https://github.com/ykh09242/unity-mcp"


def test_git_upm_subfolder_carries_the_original_mit_notice():
    original = (ROOT / "LICENSE").read_text(encoding="utf-8")
    bundled = (ROOT / "MCPForUnity/Documentation~/LICENSE.md").read_text(encoding="utf-8")
    assert bundled == original


@pytest.mark.parametrize("python_version,allowed", [
    ("3.10.20", False),
    ("3.11.0", True),
    ("3.14.8", True),
])
def test_published_metadata_requires_python311(python_version, allowed):
    project = tomllib.loads((ROOT / "Server/pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert (python_version in SpecifierSet(project["requires-python"])) is allowed
    assert "Programming Language :: Python :: 3.10" not in project["classifiers"]
    assert "Programming Language :: Python :: 3.11" in project["classifiers"]
    assert all(Requirement(requirement).name != "tomli" for requirement in project["dependencies"])
