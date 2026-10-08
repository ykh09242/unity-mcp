"""Public edge commands require arrays while preserving native element validation."""

import copy
import importlib
import json
import socket

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def dispatch(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Network and token access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("socketpair", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    state = {"calls": [], "encoded": [], "failure": None}

    def capture(tool, params, _config):
        state["calls"].append((tool, copy.deepcopy(params)))
        request = httpx.Request(
            "POST", "http://127.0.0.1/api/command", json={"type": tool, "params": params}
        )
        state["encoded"].append(json.loads(request.read()))
        if state["failure"] is not None:
            raise connection.UnityCommandError(state["failure"])
        return {"success": True, "data": {"zero": 0, "false": False, "null": None}}

    probuilder = importlib.import_module("cli.commands.probuilder")
    monkeypatch.setattr(probuilder, "run_command", capture)
    return importlib.import_module("cli.main").cli, state


@pytest.mark.parametrize("command", ["extrude-edges", "bevel-edges"])
@pytest.mark.parametrize("value", [{"a": 0, "b": 1}, True, 1, None, False, 0, {}, "owned"])
def test_nonarray_edges_render_contextual_error_without_dispatch(dispatch, command, value):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli, ["--format", "json", "probuilder", command, "Owned", "--edges", json.dumps(value)]
    )
    assert result.exit_code == 1, (result.exception, result.stdout, result.stderr, state)
    assert result.stdout == "" and "edges" in result.stderr and "array" in result.stderr
    assert isinstance(result.exception, SystemExit)
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("command", ["extrude-edges", "bevel-edges"])
@pytest.mark.parametrize("raw", ["", "["])
def test_malformed_edge_json_still_reports_before_dispatch(dispatch, command, raw):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli, ["--format", "json", "probuilder", command, "Owned", "--edges", raw]
    )
    assert result.exit_code == 1, (result.exception, result.output)
    assert result.stdout == "" and "Invalid JSON" in result.stderr and "edges" in result.stderr
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("command", ["extrude-edges", "bevel-edges"])
@pytest.mark.parametrize(
    "value,key",
    [
        ([], "edgeIndices"),
        ([0], "edgeIndices"),
        ([{"a": "0", "b": 1.0}], "edges"),
        ([None, False, "0", 1.0], "edgeIndices"),
        ([[0, 1]], "edgeIndices"),
        ([{"a": 0}], "edges"),
    ],
)
def test_edge_arrays_preserve_payload_selectors_zero_and_native_element_validation(
    dispatch, command, value, key
):
    cli, state = dispatch
    options = ["--distance", "0", "--no-group"] if command == "extrude-edges" else ["--amount", "0"]
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "probuilder",
            command,
            "Owned",
            "--edges",
            json.dumps(value),
            "--search-method",
            "by_name",
            *options,
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    properties = (
        {"distance": 0.0, "asGroup": False} if command == "extrude-edges" else {"amount": 0.0}
    )
    expected = {
        "action": command.replace("-", "_"),
        "target": "Owned",
        "searchMethod": "by_name",
        "properties": {**properties, key: value},
    }
    assert state["calls"] == [("manage_probuilder", expected)]
    assert state["encoded"] == [{"type": "manage_probuilder", "params": expected}]
    assert json.loads(result.stdout) == {
        "success": True,
        "data": {"zero": 0, "false": False, "null": None},
    }


@pytest.mark.parametrize("command", ["extrude-edges", "bevel-edges"])
def test_native_edge_failure_preserves_false_zero_null_and_partial_data(dispatch, command):
    cli, state = dispatch
    failure = {
        "success": False,
        "error": "Owned native edge validation",
        "data": {"zero": 0, "false": False, "null": None, "partial": [0]},
    }
    state["failure"] = failure
    result = CliRunner().invoke(
        cli, ["--format", "json", "probuilder", command, "Owned", "--edges", '[{"a":"0","b":1.0}]']
    )
    assert result.exit_code == 1, (result.exception, result.output)
    assert json.loads(result.stdout) == failure
    assert len(state["calls"]) == len(state["encoded"]) == 1
    assert state["calls"][0][1]["properties"]["edges"] == [{"a": "0", "b": 1.0}]
