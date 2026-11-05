"""Scene Save As overwrite consent reaches the native request only when explicit."""

import importlib
import json

import pytest
from click.testing import CliRunner

from cli.main import cli


@pytest.mark.parametrize("allow_overwrite", [False, True])
def test_scene_save_forwards_explicit_overwrite(monkeypatch, allow_overwrite):
    module = importlib.import_module("cli.commands.scene")
    requests = []

    def record(command, params, _config):
        requests.append((command, params))
        return {"success": True, "message": "Scene saved"}

    monkeypatch.setattr(module, "run_command", record)
    args = ["--format", "json", "scene", "save", "--path", "Assets/Scenes/Existing.unity"]
    if allow_overwrite:
        args.append("--allow-overwrite")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["success"] is True
    expected = {"action": "save", "path": "Assets/Scenes/Existing.unity"}
    if allow_overwrite:
        expected["allow_overwrite"] = True
    assert requests == [("manage_scene", expected)]
