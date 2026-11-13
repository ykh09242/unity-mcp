"""Scene move selectors preserve explicit instance IDs at the CLI boundary."""

import json

import pytest
from click.testing import CliRunner

from cli.commands import scene
from cli.main import cli


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--by-id", "123", "Level2"], 123),
        (["--by-id", "--", "-123", "Level2"], -123),
        (["--by-id", "2147483647", "Level2"], 2147483647),
        (["--by-id", "--", "-2147483648", "Level2"], -2147483648),
        (["--by-id", "0" * 5000 + "42", "Level2"], 42),
        (["123", "Level2"], "123"),
    ],
)
def test_move_to_dispatches_explicit_id_or_legacy_name(monkeypatch, args, expected):
    calls = []
    monkeypatch.setattr(
        scene,
        "run_command",
        lambda tool, params, config: calls.append((tool, params)) or {"success": True},
    )
    result = CliRunner().invoke(cli, ["--format", "json", "scene", "move-to", *args])
    assert result.exit_code == 0, result.output
    assert calls == [
        ("manage_scene", {"action": "move_to_scene", "target": expected, "sceneName": "Level2"})
    ]


@pytest.mark.parametrize(
    "target", ["missing", "1_0", "--1", "0", "2147483648", "-2147483649", "9" * 5000]
)
def test_move_to_invalid_id_fails_locally(monkeypatch, target):
    calls = []
    monkeypatch.setattr(scene, "run_command", lambda *args: calls.append(args))
    result = CliRunner().invoke(
        cli, ["--format", "json", "scene", "move-to", "--by-id", "--", target, "Level2"]
    )
    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout)["code"] == "invalid_scene_target_id"
    assert calls == []


def test_move_to_help_and_text_error(monkeypatch):
    monkeypatch.setattr(scene, "run_command", lambda *args: pytest.fail("must not dispatch"))
    runner = CliRunner()
    help_result = runner.invoke(cli, ["scene", "move-to", "--help"])
    assert help_result.exit_code == 0
    assert "--by-id" in help_result.output
    assert "--by-id -- -123" in help_result.output
    error = runner.invoke(cli, ["--format", "text", "scene", "move-to", "--by-id", "bad", "Level2"])
    assert error.exit_code == 1
    assert error.stdout == ""
    assert "nonzero signed 32-bit integer" in error.stderr
