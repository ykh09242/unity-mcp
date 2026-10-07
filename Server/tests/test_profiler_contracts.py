from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from cli.commands.profiler import profiler


@pytest.mark.parametrize("area", ["CPU", "CPU=", "CPU=invalid", "=true"])
def test_cli_invalid_profiler_area_is_rejected_before_transport(monkeypatch, area):
    monkeypatch.setattr(
        "cli.commands.profiler.get_config", Mock(return_value=SimpleNamespace(format="json"))
    )
    send = Mock(return_value={"success": True})
    monkeypatch.setattr("cli.commands.profiler.run_command", send)
    result = CliRunner().invoke(profiler, ["set-areas", "--area", area])
    assert result.exit_code != 0
    send.assert_not_called()


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("no", False)],
)
def test_cli_valid_profiler_area_boolean_controls(monkeypatch, value, expected):
    monkeypatch.setattr(
        "cli.commands.profiler.get_config", Mock(return_value=SimpleNamespace(format="json"))
    )
    send = Mock(return_value={"success": True})
    monkeypatch.setattr("cli.commands.profiler.run_command", send)
    result = CliRunner().invoke(profiler, ["set-areas", "--area", "CPU=" + value])
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1]["areas"] == {"CPU": expected}
