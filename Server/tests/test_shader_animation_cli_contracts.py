"""Shader/animation contracts through actual Click and central HTTP handling."""

import builtins
import importlib
import json
import os
import subprocess
import sys
import textwrap

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


SHADER = "Assets/Shaders/Fixture.shader"
CONTROLLER = "Assets/Animation/Fixture.controller"
CLIP = "Assets/Animation/Fixture.anim"
COMMANDS = [
    ["shader", "read", SHADER],
    ["shader", "create", "Fixture", "--contents", "Shader text"],
    ["shader", "update", SHADER, "--contents", "Shader text"],
    ["shader", "delete", SHADER, "--force"],
    ["animation", "animator", "play", "Player", "Walk"],
    ["animation", "clip", "create", CLIP],
    ["animation", "clip", "create-preset", CLIP, "bounce"],
    ["animation", "clip", "add-event", CLIP, "--function", "Tick", "--time", "0"],
    ["animation", "clip", "remove-event", CLIP, "--event-index", "0"],
    ["animation", "controller", "create", CONTROLLER],
    ["animation", "controller", "assign", CONTROLLER, "Player"],
    ["animation", "controller", "add-layer", CONTROLLER, "Upper"],
    ["animation", "controller", "remove-layer", CONTROLLER, "--layer-index", "0"],
    ["animation", "controller", "set-layer-weight", CONTROLLER, "0", "--layer-index", "0"],
    [
        "animation",
        "controller",
        "create-blend-tree-1d",
        CONTROLLER,
        "Walk",
        "--blend-param",
        "Speed",
    ],
    [
        "animation",
        "controller",
        "create-blend-tree-2d",
        CONTROLLER,
        "Walk",
        "--blend-param-x",
        "X",
        "--blend-param-y",
        "Y",
    ],
    [
        "animation",
        "controller",
        "add-blend-tree-child",
        CONTROLLER,
        "Walk",
        "--clip-path",
        CLIP,
        "--threshold",
        "0",
        "--position",
        "0",
        "0",
    ],
]


@pytest.fixture
def controlled_http(monkeypatch):
    expected = {
        "success": True,
        "message": "Operation completed",
        "data": {"contents": "Shader text", "count": 0, "enabled": False, "reference": None},
    }
    raw = dict(expected)
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=raw)

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return expected, raw, requests


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("wrapped", [False, True])
def test_successful_json_is_one_response_document(controlled_http, command, wrapped):
    expected, raw, requests = controlled_http
    if wrapped:
        raw.clear()
        raw.update({"status": "success", "result": expected})
    result = CliRunner().invoke(
        cli, ["--format", "json", "--instance", "Project@fixture", *command]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert len(requests) == 1 and requests[0]["unity_instance"] == "Project@fixture"


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("wrapped", [False, True])
def test_native_failure_retains_document_and_nonzero_exit(controlled_http, command, wrapped):
    _, raw, requests = controlled_http
    expected = {
        "success": False,
        "error": "Native operation rejected",
        "data": {"count": 0, "enabled": False, "reference": None},
    }
    raw.clear()
    raw.update({"status": "success", "result": expected} if wrapped else expected)
    result = CliRunner().invoke(cli, ["--format", "json", *command])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == expected
    assert len(requests) == 1


@pytest.mark.parametrize("suffix", [".hlsl", ".cs", ".txt", ".shader.bak"])
@pytest.mark.parametrize("action", ["read", "update", "delete"])
def test_explicit_non_shader_suffix_never_targets_shader_sibling(controlled_http, suffix, action):
    _, _, requests = controlled_http
    command = ["shader", action, "Assets/Shaders/Fixture" + suffix]
    command += (
        ["--contents", "Changed"]
        if action == "update"
        else ["--force"]
        if action == "delete"
        else []
    )
    result = CliRunner().invoke(cli, command)
    assert result.exit_code != 0, (result.output, requests)
    assert requests == [], "unsupported explicit path was rewritten to a .shader request"


@pytest.mark.parametrize("action", ["create", "update"])
def test_explicit_empty_shader_contents_do_not_read_stdin(controlled_http, action):
    _, _, requests = controlled_http
    command = ["shader", action, "Fixture" if action == "create" else SHADER, "--contents", ""]
    result = CliRunner().invoke(cli, command, input="Unexpected stdin replacement")
    assert result.exit_code == 0, result.output
    assert requests[0]["params"]["contents"] == ""


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("default_encoding", ["utf-8", "cp1252", "cp949"])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_shader_file_unicode_is_independent_of_locale(
    controlled_http, monkeypatch, tmp_path, action, default_encoding, bom
):
    _, _, requests = controlled_http
    shader_commands = importlib.import_module("cli.commands.shader")
    contents = 'Shader "Custom/Fixture" { /* caf\u00e9 \ud55c\uae00 \U0001f600 */ }\n'
    source = tmp_path / "unicode.shader"
    source.write_bytes(bom + contents.encode("utf-8"))

    def locale_open(path, mode="r", **kwargs):
        kwargs.setdefault("encoding", default_encoding)
        return builtins.open(path, mode, **kwargs)

    monkeypatch.setattr(shader_commands, "open", locale_open, raising=False)
    command = ["shader", action, "Fixture" if action == "create" else SHADER, "--file", str(source)]
    result = CliRunner().invoke(cli, command)
    assert result.exit_code == 0, (result.output, result.exception)
    assert requests[0]["params"]["contents"] == contents


@pytest.mark.parametrize("action", ["create", "update"])
def test_shader_file_invalid_utf8_never_sends_contents(controlled_http, tmp_path, action):
    _, _, requests = controlled_http
    source = tmp_path / "invalid.shader"
    source.write_bytes(b"\xef\xbb\xbf\xff")
    command = ["shader", action, "Fixture" if action == "create" else SHADER, "--file", str(source)]
    result = CliRunner().invoke(cli, command)
    assert result.exit_code != 0, result.output
    assert requests == []


@pytest.mark.parametrize(
    "path", ["Assets/Shaders/Fixture", SHADER, "Assets/Shaders/Fixture.SHADER"]
)
def test_shader_supported_path_forms_keep_name_and_directory(controlled_http, path):
    _, _, requests = controlled_http
    result = CliRunner().invoke(cli, ["shader", "read", path])
    assert result.exit_code == 0, result.output
    assert requests[0]["params"] == {"action": "read", "name": "Fixture", "path": "Assets/Shaders"}


@pytest.mark.parametrize(
    "command,notice",
    [
        (["shader", "read", SHADER], "Shader text"),
        (["shader", "create", "Fixture", "--contents", "Shader text"], "Created shader:"),
        (
            [
                "animation",
                "controller",
                "create-blend-tree-1d",
                CONTROLLER,
                "Walk",
                "--blend-param",
                "Speed",
            ],
            "Created 1D blend tree",
        ),
    ],
)
def test_text_output_keeps_readable_contents_and_notices(controlled_http, command, notice):
    result = CliRunner().invoke(cli, command)
    assert result.exit_code == 0, result.output
    assert notice in result.stdout


@pytest.mark.parametrize(
    "command,properties",
    [
        (["animator", "set-enabled", "0", "false"], {"enabled": False}),
        (["animator", "set-speed", "0", "0"], {"speed": 0.0}),
        (
            ["controller", "add-state", CONTROLLER, "Walk", "--no-default", "--speed", "0"],
            {"stateName": "Walk", "isDefault": False, "speed": 0.0, "layerIndex": 0},
        ),
        (
            [
                "controller",
                "add-blend-tree-child",
                CONTROLLER,
                "Walk",
                "--clip-path",
                CLIP,
                "--threshold",
                "0",
                "--position",
                "0",
                "0",
            ],
            {"stateName": "Walk", "layerIndex": 0, "threshold": 0.0, "position": [0.0, 0.0]},
        ),
        (
            [
                "controller",
                "add-parameter",
                CONTROLLER,
                "Enabled",
                "--type",
                "bool",
                "--default-value",
                "false",
            ],
            {"parameterName": "Enabled", "parameterType": "bool", "defaultValue": False},
        ),
    ],
)
def test_animation_wire_keeps_false_zero_and_clip_routing(controlled_http, command, properties):
    _, _, requests = controlled_http
    result = CliRunner().invoke(cli, ["animation", *command])
    assert result.exit_code == 0, result.output
    assert requests[0]["params"]["properties"] == properties
    if "--clip-path" in command:
        assert requests[0]["params"]["clipPath"] == CLIP


def test_json_and_rejected_shader_paths_in_fresh_process(tmp_path):
    code = textwrap.dedent("""
        import json
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        requests = []
        raw = {"success": True, "data": {"contents": "Shader text", "count": 0, "enabled": False}}
        client_type = httpx.AsyncClient
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=raw)
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        commands = [
            ["shader", "read", "Assets/Shaders/Fixture.shader"],
            ["shader", "update", "Assets/Shaders/Fixture.shader", "--contents", "Changed"],
            ["shader", "delete", "Assets/Shaders/Fixture.shader", "--force"],
            ["animation", "controller", "create-blend-tree-1d", "Assets/Fixture.controller", "Walk", "--blend-param", "Speed"],
            ["animation", "controller", "create-blend-tree-2d", "Assets/Fixture.controller", "Walk", "--blend-param-x", "X", "--blend-param-y", "Y"],
            ["animation", "controller", "add-blend-tree-child", "Assets/Fixture.controller", "Walk", "--clip-path", "Assets/Fixture.anim", "--threshold", "0"],
        ]
        runner = CliRunner()
        for failure in (False, True):
            expected = {"success": False, "error": "Rejected", "data": {"count": 0, "enabled": False}} if failure else dict(raw)
            raw.clear()
            raw.update({"status": "success", "result": expected} if failure else expected)
            for command in commands:
                result = runner.invoke(cli, ["--format", "json", *command])
                assert result.exit_code == (1 if failure else 0), result.output
                assert json.loads(result.stdout) == expected, command
        for action in ("read", "update", "delete"):
            command = ["shader", action, "Assets/Shaders/Fixture.hlsl"]
            command += ["--contents", "Changed"] if action == "update" else ["--force"] if action == "delete" else []
            result = runner.invoke(cli, command)
            assert result.exit_code != 0, result.output
        assert len(requests) == 12
        print("fresh CLI/HTTP: 12 JSON scenarios and 3 unsupported-path rejections passed")
    """)
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12 JSON scenarios and 3 unsupported-path rejections passed" in result.stdout
