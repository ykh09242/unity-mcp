"""Checked-in distribution metadata must select one immutable fork server."""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_upm_and_bundle_use_the_same_immutable_server():
    package = json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))
    bundle = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    source = package["mcpServerSource"]
    assert re.fullmatch(
        r"git\+https://github\.com/ykh09242/unity-mcp\.git@[0-9a-f]{40}#subdirectory=Server",
        source,
    )
    assert bundle["server"]["mcp_config"]["args"] == ["--from", source, "mcp-for-unity"]


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
