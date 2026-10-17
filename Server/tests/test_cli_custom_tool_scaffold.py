"""The scaffold command is offline and preserves explicit identities."""

import json

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize("group", ["tool", "custom_tool"])
def test_scaffold_command_exists_without_connection(group):
    from cli.main import cli

    result = CliRunner().invoke(cli, [group, "scaffold", "--help"])
    assert result.exit_code == 0, result.output
    assert "--namespace" in result.output
    assert "--template" in result.output


@pytest.mark.parametrize("group", ["tool", "custom_tool"])
def test_scaffold_creates_offline_without_auth(tmp_path, monkeypatch, group):
    from cli.main import cli
    import cli.utils.connection as connection

    monkeypatch.setattr(connection, "_auth_headers", lambda *_: pytest.fail("read auth"))
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2021.3.0f1")
    (tmp_path / "Packages").mkdir()
    (tmp_path / "Packages/manifest.json").write_text("{}")
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            group,
            "scaffold",
            "InventoryTool",
            "--namespace",
            "My.Game",
            "--tool-name",
            "inventory.tool",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    assert receipt["created"] is True
    assert receipt["namespace"] == "My.Game"
    assert receipt["tool_name"] == "inventory.tool"
    assert receipt["compile_verified"] is False
    assert receipt["discovery_verified"] is False
    assert receipt["tool_name_uniqueness"] == "unknown"
    assert (tmp_path / receipt["destination"]).read_text().find("namespace My.Game") >= 0


def test_scaffold_invalid_request_has_structured_exit_two():
    from cli.main import cli

    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "tool",
            "scaffold",
            "Parameters",
            "--namespace",
            "My.Game",
            "--tool-name",
            "fixture",
        ],
    )
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["reason"] == "reserved_class"
