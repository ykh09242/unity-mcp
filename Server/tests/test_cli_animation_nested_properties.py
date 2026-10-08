"""Animation raw validates inner containers while retaining native wire contracts."""

import copy
import importlib
import json
import socket

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def dispatch(monkeypatch):
    """Use registered Click with synchronous JSON encoding and all I/O denied."""

    def denied(*_args, **_kwargs):
        raise AssertionError("Network or token access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("socketpair", "create_connection", "create_server", "getaddrinfo"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    calls = []
    raw = {
        "success": False,
        "error": "owned native rejection",
        "data": {"false": False, "zero": 0, "null": None, "partial": [0]},
    }

    def capture(tool, params, _config):
        httpx.Request(
            "POST", "http://127.0.0.1/api/command", json={"type": tool, "params": params}
        ).read()
        calls.append((tool, copy.deepcopy(params)))
        raise connection.UnityCommandError(copy.deepcopy(raw))

    monkeypatch.setattr(importlib.import_module("cli.commands.animation"), "run_command", capture)
    return importlib.import_module("cli.main").cli, calls, raw


@pytest.mark.parametrize(
    "properties", [[], False, True, 0, 1, "owned", "[]", "null", "false", '{"broken":']
)
@pytest.mark.parametrize("flattened", [False, True])
def test_malformed_inner_properties_reject_before_dispatch(dispatch, properties, flattened):
    cli, calls, _raw = dispatch
    payload = {"properties": properties}
    if flattened:
        payload["stateName"] = "Walk"
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "animation",
            "raw",
            "animator_play",
            "Owned",
            "--params",
            json.dumps(payload),
        ],
    )
    assert result.exit_code != 0 and "properties" in result.output
    assert calls == []


@pytest.mark.parametrize(
    "properties",
    [
        None,
        {},
        '{ "speed":0,"enabled":false,"reference":null }',
        {"speed": 0, "enabled": False, "reference": None},
    ],
)
@pytest.mark.parametrize("flattened", [False, True])
def test_valid_representation_merge_and_native_response_preserved(dispatch, properties, flattened):
    cli, calls, raw = dispatch
    payload = {"properties": properties}
    if flattened:
        payload.update({"speed": 3, "stateName": "Walk"})
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "animation",
            "raw",
            "animator_play",
            "Owned",
            "--params",
            json.dumps(payload),
        ],
    )
    assert result.exit_code == 1 and json.loads(result.stdout) == raw
    wire = {"action": "animator_play", "target": "Owned"}
    if flattened:
        nested = json.loads(properties) if isinstance(properties, str) else properties
        wire["properties"] = {"speed": 3, "stateName": "Walk", **(nested or {})}
    elif properties is not None:
        wire["properties"] = properties
    assert calls == [("manage_animation", wire)]
