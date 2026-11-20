"""License-free wiring smoke check for the client configuration Install Skills shortcut."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_install_skills_defaults_to_installed_package_revision() -> None:
    source = (
        ROOT / "MCPForUnity/Editor/Windows/Components/ClientConfig/McpClientConfigSection.cs"
    ).read_text(encoding="utf-8")
    method = source.split("private void OnInstallSkillsClicked()", 1)[1].split(
        "private void OnBrowseClaudeClicked()", 1
    )[0]
    assert "SkillSyncSource.CaptureInstalled()" in method
    assert "GetGitUpdateBranch" not in method
    assert "IsPreReleaseVersion" not in method
    assert re.search(r"SkillSyncService\.SyncSourceAsync\(\s*source\s*,\s*installPath\s*,", method)
