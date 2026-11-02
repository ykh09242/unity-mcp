"""Renderer selection is distinct from feature index across Python and CLI routes."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize("action", ["feature_list", "pipeline_get_diagnostics"])
def test_python_forwards_renderer_selection(monkeypatch, action):
    graphics = importlib.import_module("services.tools.manage_graphics")
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        graphics, "get_unity_instance_from_context", AsyncMock(return_value="Editor")
    )
    monkeypatch.setattr(graphics, "send_with_unity_instance", send)
    result = asyncio.run(
        graphics.manage_graphics(SimpleNamespace(), action=action, renderer_index=0, index=3)
    )
    assert result["success"] is True
    assert send.call_args.args[-1] == {"action": action, "renderer_index": 0, "index": 3}


@pytest.mark.parametrize(
    "command,args,action",
    [
        ("feature-list", [], "feature_list"),
        ("feature-add", ["--type", "TestFeature"], "feature_add"),
        ("feature-remove", ["--index", "2"], "feature_remove"),
        ("feature-configure", ["--index", "2", "--prop", "enabled", "true"], "feature_configure"),
        ("feature-toggle", ["--index", "2"], "feature_toggle"),
        ("feature-reorder", ["--order", "0"], "feature_reorder"),
        ("pipeline-diagnostics", [], "pipeline_get_diagnostics"),
    ],
)
def test_cli_forwards_renderer_index(monkeypatch, command, args, action):
    graphics = importlib.import_module("cli.commands.graphics")
    sent = []
    monkeypatch.setattr(
        graphics,
        "run_command",
        lambda tool, params, config: sent.append(params) or {"success": True},
    )
    result = CliRunner().invoke(graphics.graphics, [command, *args, "--renderer-index", "0"])
    assert result.exit_code == 0, result.output
    assert sent[0]["action"] == action
    assert sent[0]["renderer_index"] == 0
    if "--index" in args:
        assert sent[0]["index"] == 2


def test_cli_forwards_explicit_renderer_target(monkeypatch):
    graphics = importlib.import_module("cli.commands.graphics")
    sent = []
    monkeypatch.setattr(
        graphics,
        "run_command",
        lambda tool, params, config: sent.append(params) or {"success": True},
    )
    result = CliRunner().invoke(
        graphics.graphics, ["feature-list", "--target", "Assets/Second.asset"]
    )
    assert result.exit_code == 0, result.output
    assert sent == [{"action": "feature_list", "target": "Assets/Second.asset"}]
