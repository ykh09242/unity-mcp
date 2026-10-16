"""Tool inspection has one additive CLI contract and never invokes a tool."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize("command", ["describe", "schema"])
def test_tool_inspection_is_advertised_without_connection(command):
    from cli.main import cli

    result = CliRunner().invoke(cli, ["tool", command, "--help"])
    assert result.exit_code == 0, result.output
    assert "--protocol" in result.output
    assert "NAME" in result.output


@pytest.mark.parametrize("command", ["describe", "schema"])
@pytest.mark.parametrize("name", ["", "has space", "has/slash", "bad\nname", "x" * 129])
def test_invalid_name_refuses_before_auth_or_sdk(monkeypatch, command, name):
    from cli.main import cli
    import cli.utils.connection as connection

    monkeypatch.setattr(
        connection, "_auth_headers", lambda *_: pytest.fail("invalid name read auth")
    )
    result = CliRunner().invoke(cli, ["--format", "json", "tool", command, name])
    assert result.exit_code == 2, result.output


def test_schema_output_is_complete_json_even_with_table_preference(monkeypatch):
    from cli.main import cli
    import cli.utils.tool_description as inspection

    enum = [f"choice_{index}" for index in range(80)]
    payload = {
        "schema_version": "unity-mcp/tool-description@1",
        "name": "fixture",
        "exit_code": 0,
        "advertised": True,
        "tool": {
            "name": "fixture",
            "inputSchema": {"type": "object", "properties": {"choice": {"enum": enum}}},
        },
    }
    inspect = AsyncMock(return_value=SimpleNamespace(exit_code=0, model_dump=lambda **_: payload))
    monkeypatch.setattr(inspection, "inspect_tool", inspect)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "table",
            "--instance",
            "Fixture@aaaa1111",
            "tool",
            "schema",
            "fixture",
            "--protocol",
            "legacy",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert inspect.await_args.args[0].unity_instance == "Fixture@aaaa1111"
    assert inspect.await_args.args[1] == "fixture"
    assert inspect.await_args.kwargs == {"protocol": "legacy"}


@pytest.mark.parametrize("code", [0, 1, 3])
def test_describe_preserves_structured_receipt_and_exit(monkeypatch, code):
    from cli.main import cli
    import cli.utils.tool_description as inspection

    payload = {"name": "fixture", "advertised": None, "exit_code": code, "reason": "unknown"}
    monkeypatch.setattr(
        inspection,
        "inspect_tool",
        AsyncMock(return_value=SimpleNamespace(exit_code=code, model_dump=lambda **_: payload)),
    )
    result = CliRunner().invoke(cli, ["--format", "json", "tool", "describe", "fixture"])
    assert result.exit_code == code
    assert json.loads(result.stdout) == payload


@pytest.mark.parametrize("group", ["tool", "custom_tool"])
def test_existing_custom_list_alias_keeps_endpoint_contract(monkeypatch, group):
    from cli.main import cli
    import cli.commands.tool as command

    payload = {"success": True, "tools": [{"name": "old_custom"}]}
    calls = []
    monkeypatch.setattr(
        command, "run_list_custom_tools", lambda config: calls.append(config) or payload
    )
    result = CliRunner().invoke(cli, ["--format", "json", group, "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert len(calls) == 1
