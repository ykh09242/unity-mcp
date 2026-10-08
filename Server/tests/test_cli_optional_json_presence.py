"""Explicit optional JSON values must be parsed before CLI command dispatch."""

import copy
import importlib
import socket

import httpx
import pytest
from click.testing import CliRunner


CASES = (
    (("asset", "create", "Assets/Owned.mat", "Material"), "properties", "{}"),
    (("material", "create", "Assets/Owned.mat"), "properties", "{}"),
    (("component", "add", "Owned", "Fixture"), "properties", "{}"),
    (("camera", "set-body", "Owned"), "props", "{}"),
    (("camera", "set-aim", "Owned"), "props", "{}"),
    (("camera", "add-extension", "Owned", "CinemachineImpulseListener"), "props", "{}"),
    (
        ("animation", "controller", "add-transition", "Assets/Owned.controller", "Idle", "Walk"),
        "conditions",
        "[]",
    ),
    (("probuilder", "subdivide", "Owned"), "faces", "[]"),
    (("probuilder", "select-faces", "Owned"), "grow-from", "[]"),
    (("prefab", "modify", "Assets/Owned.prefab"), "create-child", "{}"),
    (("texture", "sprite", "Assets/Owned.png"), "pivot", "[]"),
    (("texture", "sprite", "Assets/Owned.png"), "color", "#000000"),
    (("texture", "create", "Assets/Owned.png"), "color", "#000000"),
    (("texture", "create", "Assets/Owned.png"), "palette", "[]"),
    (("texture", "create", "Assets/Owned.png"), "import-settings", "{}"),
)


@pytest.fixture
def dispatch(monkeypatch):
    """Capture synchronous command admission with every application network path denied."""

    def denied(*_args, **_kwargs):
        raise AssertionError("Application network and token access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("socketpair", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    calls = []

    def capture(tool, params, _config):
        calls.append((tool, copy.deepcopy(params)))
        return {"success": True}

    for module in {command[0] for command, _, _ in CASES}:
        monkeypatch.setattr(
            importlib.import_module(f"cli.commands.{module}"), "run_command", capture
        )
    return importlib.import_module("cli.main").cli, calls


@pytest.mark.parametrize("command,flag,_valid", CASES)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_explicit_empty_json_rejects_without_dispatch(
    dispatch, command, flag, _valid, output_format
):
    cli, calls = dispatch
    result = CliRunner().invoke(cli, ["--format", output_format, *command, f"--{flag}", ""])
    assert result.exit_code != 0, (result.stdout, result.stderr, calls)
    assert result.stdout == ""
    assert "Invalid" in result.stderr or "Error" in result.stderr
    assert calls == []


@pytest.mark.parametrize("command,flag,_valid", CASES)
def test_whitespace_json_rejects_without_dispatch(dispatch, command, flag, _valid):
    cli, calls = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", *command, f"--{flag}", " "])
    assert result.exit_code != 0, (result.stdout, result.stderr, calls)
    assert result.stdout == "" and result.stderr
    assert calls == []


@pytest.mark.parametrize("command,_flag,_valid", CASES)
def test_omitted_json_preserves_operation_defaults(dispatch, command, _flag, _valid):
    cli, calls = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", *command])
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == "" and len(calls) == 1


@pytest.mark.parametrize("command,flag,valid", CASES)
def test_valid_empty_containers_and_color_remain_supported(dispatch, command, flag, valid):
    cli, calls = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", *command, f"--{flag}", valid])
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == "" and len(calls) == 1
    params = calls[0][1]
    if flag == "faces":
        assert params["properties"]["faceIndices"] == []
    if flag == "grow-from":
        assert params["properties"]["growFrom"] == []
    if flag == "conditions":
        assert params["properties"]["conditions"] == []
    if flag == "pivot":
        assert params["spriteSettings"]["pivot"] == []
    if flag == "create-child":
        assert params["createChild"] == {}
    if flag == "palette":
        assert params["palette"] == []
    if flag == "import-settings":
        assert params["importSettings"] == {}
    if flag == "properties":
        assert params["properties"] == {}
    if flag == "color":
        assert params["fillColor"] == [0, 0, 0, 255]
