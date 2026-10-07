"""Restricted cleanup is explicit through the public build CLI."""

import importlib

from click.testing import CliRunner
import pytest


@pytest.mark.parametrize(
    "arguments,dry_run",
    [
        (["clean-output", "Builds/Fixture"], True),
        (["clean-output", "Builds/Fixture", "--delete"], False),
    ],
)
def test_cleanup_cli_preserves_preview_and_explicit_delete(monkeypatch, arguments, dry_run):
    # Given the real Click command and a captured transport boundary.
    module = importlib.import_module("cli.commands.build")
    sent = []

    def send(command, params, config):
        sent.append((command, params))
        return {"success": True, "message": "fixture"}

    monkeypatch.setattr(module, "run_command", send)
    # When invoking the public CLI.
    result = CliRunner().invoke(module.build, arguments)
    # Then deletion is sent only with the explicit flag.
    assert result.exit_code == 0, result.output
    assert sent == [
        (
            "manage_build",
            {
                "action": "clean_output",
                "output_path": "Builds/Fixture",
                "dry_run": dry_run,
            },
        )
    ]


def test_cleanup_cli_requires_output_selection(monkeypatch):
    # Given a command without a selected output.
    module = importlib.import_module("cli.commands.build")
    sent = []
    monkeypatch.setattr(module, "run_command", lambda *args: sent.append(args))
    # When invocation is parsed.
    result = CliRunner().invoke(module.build, ["clean-output", "--delete"])
    # Then no cleanup reaches Unity.
    assert result.exit_code != 0
    assert sent == []
