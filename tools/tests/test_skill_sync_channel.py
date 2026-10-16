"""Smoke-check package-pinned default selection at the Install Skills boundary."""

from pathlib import Path
import re


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


def test_skill_installer_menu_exposes_latest_without_changing_package_default() -> None:
    source = (ROOT / "MCPForUnity/Editor/Setup/McpForUnitySkillInstaller.cs").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r'\[MenuItem\(ProductInfo\.MenuRoot \+ "/Skill Installer", priority = 4\)\]\s*'
        r"public static void OpenWindow\(\)",
        source,
    )
    on_enable = source.split("private void OnEnable()", 1)[1].split("private void OnDisable()", 1)[
        0
    ]
    assert "_latest = false;" in on_enable
    assert '"Installed package", "Latest from branch"' in source
