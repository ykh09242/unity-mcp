"""Real stdin confirmation leaves one JSON result on stdout or refuses dispatch."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.mark.parametrize(
    "command",
    [
        ["asset", "delete", "Assets/Owned Asset.mat"],
        ["script", "delete", "Assets/Owned Script.cs"],
        ["gameobject", "delete", "Owned Object"],
        ["component", "remove", "Owned Object", "Rigidbody"],
        ["shader", "delete", "Assets/Owned Shader.shader"],
        ["texture", "delete", "Assets/Owned Texture.png"],
    ],
)
@pytest.mark.parametrize("answer", ["y\n", "n\n", "", "maybe\nyes\n", "force"])
def test_command_families_keep_confirmation_consent_and_streams(monkeypatch, command, answer):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    response = {"success": True, "data": {"deleted": True}}
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(connection, "send_command", send)
    forced = answer == "force"
    result = CliRunner().invoke(
        cli,
        ["--no-project-context", "--format", "json", *command, *(["--force"] if forced else [])],
        input="" if forced else answer,
    )
    assert ("?" in result.stderr) is not forced
    if forced or "y" in answer:
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == response
        send.assert_awaited_once()
    else:
        assert result.exit_code != 0
        assert result.stdout == ""
        send.assert_not_awaited()


@pytest.mark.parametrize("answer", ["y\n", "n\n"])
def test_real_stdin_keeps_json_stdout_and_refusal_dispatch_free(tmp_path, answer):
    dispatches = tmp_path / "dispatches.json"
    script = """
import json, sys
from pathlib import Path
from cli.main import cli
from cli.utils import connection
import httpx

def respond(request):
    Path(sys.argv[1]).write_text(json.dumps(json.loads(request.content)))
    return httpx.Response(200, json={"success": True, "data": {"deleted": True}})

real_client = httpx.AsyncClient
connection.httpx.AsyncClient = lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)
connection._auth_headers = lambda config: {}
cli.main(args=["--no-project-context", "--format", "json", *sys.argv[2:]])
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "DISABLE_TELEMETRY": "true",
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
    }
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        environment.pop("UNITY_MCP_" + name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(dispatches),
            "asset",
            "delete",
            "Assets/Owned Asset.mat",
        ],
        input=answer,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        timeout=25,
        check=False,
    )
    assert "?" in result.stderr
    if answer == "y\n":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"success": True, "data": {"deleted": True}}
        assert dispatches.exists()
    else:
        assert result.returncode != 0
        assert result.stdout == ""
        assert not dispatches.exists()


@pytest.mark.parametrize(
    "target,method,bulk",
    [
        ("Enemy", None, True),
        ("Enemy", "by_name", True),
        ("Root/Enemy", "by_path", True),
        ("Enemy", "by_tag", True),
        ("Enemies", "by_layer", True),
        ("-81840", "by_id", False),
        ("-81840", None, False),
        ("8_1840", None, True),
        ("１２", None, True),
        ("2147483648", None, True),
    ],
)
@pytest.mark.parametrize("answer", ["y\n", "n\n"])
def test_gameobject_delete_confirms_matching_scope_before_single_dispatch(
    monkeypatch, target, method, bulk, answer
):
    import importlib

    gameobject = importlib.import_module("cli.commands.gameobject")
    calls = []

    def send(command, params, config):
        calls.append((command, params))
        return {"success": True, "data": []}

    monkeypatch.setattr(gameobject, "run_command", send)
    options = ["--search-method", method] if method else []
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "text",
            "gameobject",
            "delete",
            *options,
            "--",
            target,
        ],
        input=answer,
    )
    if bulk:
        assert "all matching GameObjects" in result.stderr
        assert "inactive" in result.stderr
        assert "current editing scope" in result.stderr
    else:
        assert "exact instance ID" in result.stderr
        assert "all matching" not in result.stderr
    if answer == "y\n":
        assert result.exit_code == 0, result.output
        assert len(calls) == 1
        assert calls[0] == (
            "manage_gameobject",
            {"action": "delete", "target": target, **({"searchMethod": method} if method else {})},
        )
        assert ("Deleted all matching GameObjects" in result.stdout) is bulk
    else:
        assert result.exit_code != 0
        assert calls == []
