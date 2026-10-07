"""Guard the Install Skills call site against stable-version branch guessing."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_install_skills_reuses_the_fork_update_channel() -> None:
    source = (
        ROOT / "MCPForUnity/Editor/Windows/Components/ClientConfig/McpClientConfigSection.cs"
    ).read_text(encoding="utf-8")
    method = source.split("private void OnInstallSkillsClicked()", 1)[1].split(
        "private void OnBrowseClaudeClicked()", 1
    )[0]
    assert re.search(
        r"string branch\s*=\s*MCPServiceLocator\.Updates\.GetGitUpdateBranch\(\s*AssetPathUtility\.GetPackageVersion\(\)\s*\);",
        method,
    )
    assert "IsPreReleaseVersion" not in method
    assert re.search(r"SkillSyncService\.SyncAsync\(\s*installPath\s*,\s*branch\s*,", method)
