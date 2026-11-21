"""A fresh interpreter loads command implementations only for the selected path."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = """
import atexit
import json
import sys

from cli.main import cli

@atexit.register
def show_command_imports():
    names = sorted(name for name in sys.modules if name.startswith('cli.commands.'))
    print(json.dumps(names), file=sys.stderr)

cli(prog_name='unity-mcp')
"""


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
    # Given: the command starts without any modules cached from other tests.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("_UNITY_MCP_COMPLETE", None)
    if completion is not None:
        env.update(_UNITY_MCP_COMPLETE="fish_complete", COMP_WORDS=completion, COMP_CWORD="ve")

    # When: Click runs the actual help, suggestion, or completion path.
    proc = subprocess.run(
        [sys.executable, "-c", PROBE, *args],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    # Then: the requested output and exit contract survive, with no unrelated imports.
    diagnostics, _, loaded = proc.stderr.rstrip().rpartition("\n")
    assert proc.returncode == exit_code, (proc.stdout, diagnostics)
    assert expected_text in proc.stdout + diagnostics
    assert json.loads(loaded) == modules
