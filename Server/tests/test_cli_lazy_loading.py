"""A fresh interpreter loads command implementations only for the selected path."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE_IMPORTS = """
import atexit
import json
import sys

from cli.main import cli

@atexit.register
def show_command_imports():
    names = sorted(name for name in sys.modules if name.startswith('cli.commands.'))
    print(json.dumps(names), file=sys.stderr)
"""

PROBE_REGISTRATION = """
import inspect
import click

assert 'vfx' in cli.commands
assert not any(name.startswith('cli.commands.') for name in sys.modules)

group = cli.commands['vfx']
assert isinstance(group, click.Group)
assert group is cli.get_command(click.Context(cli), 'vfx')
assert group is cli.commands.get('vfx')
command = group.commands['line'].commands['set-positions']
assert set(inspect.signature(command.callback).parameters) == {p.name for p in command.params}
"""


def run_probe(program, args=(), completion=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("_UNITY_MCP_COMPLETE", None)
    if completion is not None:
        env.update(_UNITY_MCP_COMPLETE="fish_complete", COMP_WORDS=completion, COMP_CWORD="ve")
    return subprocess.run(
        [sys.executable, "-c", PROBE_IMPORTS + program, *args],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "args,completion,exit_code,expected_text,modules",
    [
        (["--help"], None, 0, "verification-candidate", []),
        (["--version"], None, 0, "unity-mcp, version", []),
        (
            ["--no-project-context", "-f", "json", "raw", "manage_scene", "[]"],
            None,
            1,
            '"code": "invalid_json_params"',
            [],
        ),
        (["gameobjec"], None, 2, "Did you mean", []),
        ([], "unity-mcp ve", 0, "verification-evidence", []),
        (["vfx", "line", "--help"], None, 0, "set-positions", ["cli.commands.vfx"]),
        (
            ["custom_tool", "--help"],
            None,
            0,
            "Alias for tool management",
            ["cli.commands.custom_tool_scaffold", "cli.commands.tool"],
        ),
    ],
)
def test_command_imports_follow_the_selected_path(
    args, completion, exit_code, expected_text, modules
):
    # Run the selected path without any command implementations cached from other tests.
    proc = run_probe("cli(prog_name='unity-mcp')\n", args, completion)

    # Then: the requested output and exit contract survive, with no unrelated imports.
    diagnostics, _, loaded = proc.stderr.rstrip().rpartition("\n")
    assert proc.returncode == exit_code, (proc.stdout, diagnostics)
    assert expected_text in proc.stdout + diagnostics
    assert json.loads(loaded) == modules


def test_registered_command_lookup_resolves_only_the_inspected_group():
    # Run a consumer's Click command-tree inspection in a fresh interpreter.
    proc = run_probe(PROBE_REGISTRATION)

    diagnostics, _, loaded = proc.stderr.rstrip().rpartition("\n")
    assert proc.returncode == 0, (proc.stdout, diagnostics)
    assert json.loads(loaded) == ["cli.commands.vfx"]
