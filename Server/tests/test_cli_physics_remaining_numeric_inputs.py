"""Remaining physics CSV paths provide named errors before dispatch."""

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

    monkeypatch.setattr(importlib.import_module("cli.commands.physics"), "run_command", capture)
    return importlib.import_module("cli.main").cli, calls, raw


CASES = [
    (["raycast-all", "--origin", "0,0,0", "--direction", "0,0,0"], "origin"),
    (["raycast-all", "--origin", "0,0,0", "--direction", "0,0,0"], "direction"),
    (["overlap", "--shape", "sphere", "--position", "0,0,0", "--size", "0"], "position"),
    (["overlap", "--shape", "sphere", "--position", "0,0,0", "--size", "0"], "size"),
    (
        [
            "shapecast",
            "--shape",
            "sphere",
            "--origin",
            "0,0,0",
            "--direction",
            "0,0,0",
            "--size",
            "0",
        ],
        "origin",
    ),
    (
        [
            "shapecast",
            "--shape",
            "sphere",
            "--origin",
            "0,0,0",
            "--direction",
            "0,0,0",
            "--size",
            "0",
        ],
        "direction",
    ),
    (
        [
            "shapecast",
            "--shape",
            "sphere",
            "--origin",
            "0,0,0",
            "--direction",
            "0,0,0",
            "--size",
            "0",
        ],
        "size",
    ),
    (
        [
            "apply-force",
            "--target",
            "Owned",
            "--force",
            "0,0,0",
            "--torque",
            "0",
            "--position",
            "0,0,0",
        ],
        "force",
    ),
    (
        [
            "apply-force",
            "--target",
            "Owned",
            "--force",
            "0,0,0",
            "--torque",
            "0",
            "--position",
            "0,0,0",
        ],
        "torque",
    ),
    (
        [
            "apply-force",
            "--target",
            "Owned",
            "--force",
            "0,0,0",
            "--torque",
            "0",
            "--position",
            "0,0,0",
        ],
        "position",
    ),
]


@pytest.mark.parametrize("base,field", CASES)
@pytest.mark.parametrize("value", ["", "x,0,0", "0,,0", "nan,0,0", "inf,0,0"])
def test_remaining_numeric_csv_has_named_error_without_dispatch(dispatch, base, field, value):
    cli, calls, _raw = dispatch
    args = list(base)
    args[args.index("--" + field) + 1] = value
    result = CliRunner().invoke(cli, ["--format", "json", "physics", *args])
    assert result.exit_code == 2 and "--" + field in result.stderr
    assert result.stdout == "" and calls == []


@pytest.mark.parametrize("base,field", CASES)
def test_valid_defaults_and_native_error_document_unchanged(dispatch, base, field):
    cli, calls, raw = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", "physics", *base])
    assert result.exit_code == 1 and json.loads(result.stdout) == raw
    assert len(calls) == 1 and calls[0][0] == "manage_physics"


QUERY_FIELDS = [case for case in CASES if case[0][0] != "apply-force" and case[1] != "size"]


@pytest.mark.parametrize("base,field", QUERY_FIELDS)
@pytest.mark.parametrize("dimension,value", [("3d", "0,0"), ("3d", "0"), ("2d", "0")])
def test_query_siblings_enforce_actual_receiver_minimum(dispatch, base, field, dimension, value):
    cli, calls, _raw = dispatch
    args = list(base) + ["--dimension", dimension]
    args[args.index("--" + field) + 1] = value
    result = CliRunner().invoke(cli, ["--format", "json", "physics", *args])
    assert result.exit_code == 2 and "--" + field in result.stderr
    assert calls == []


@pytest.mark.parametrize("base,field", QUERY_FIELDS)
@pytest.mark.parametrize(
    "dimension,value,expected",
    [
        ("2d", "0,1", [0.0, 1.0]),
        ("2D", "0,1,1e100", [0.0, 1.0, 1e100]),
        ("3d", "0,1,2,1e100", [0.0, 1.0, 2.0, 1e100]),
        ("3d", "3.4028235e38,0,0", [3.4028235e38, 0.0, 0.0]),
        ("3d", "1e-100,0,0", [1e-100, 0.0, 0.0]),
    ],
)
def test_query_sibling_dimensions_extras_and_float_compatibility(
    dispatch, base, field, dimension, value, expected
):
    cli, calls, raw = dispatch
    args = list(base) + ["--dimension", dimension]
    args[args.index("--" + field) + 1] = value
    result = CliRunner().invoke(cli, ["--format", "json", "physics", *args])
    assert result.exit_code == 1 and json.loads(result.stdout) == raw
    assert len(calls) == 1 and calls[0][1][field] == expected
    assert calls[0][1]["dimension"] == dimension


@pytest.mark.parametrize("field", ["force", "torque", "position"])
@pytest.mark.parametrize(
    "value,expected", [("0", [0.0]), ("0,1", [0.0, 1.0]), ("1e100,0,0", [1e100, 0.0, 0.0])]
)
def test_force_auto_dimension_and_native_validation_retained(dispatch, field, value, expected):
    cli, calls, raw = dispatch
    args = ["--format", "json", "physics", "apply-force", "--target", "Owned", "--force", "0,0"]
    if field == "force":
        args[-1] = value
    else:
        args += ["--" + field, value]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1 and json.loads(result.stdout) == raw
    assert len(calls) == 1 and calls[0][1][field] == expected
    assert "dimension" not in calls[0][1]


@pytest.mark.parametrize("command", ["overlap", "shapecast"])
@pytest.mark.parametrize(
    "value,expected",
    [("0", 0.0), ("-1", -1.0), ("1e100", 1e100), ("0,1", [0.0, 1.0]), ("0,1,2", [0.0, 1.0, 2.0])],
)
def test_size_scalar_vector_and_native_range_policy_preserved(dispatch, command, value, expected):
    cli, calls, raw = dispatch
    args = ["--format", "json", "physics", command, "--shape", "sphere", "--size", value]
    args += (
        ["--position", "0,0,0"]
        if command == "overlap"
        else ["--origin", "0,0,0", "--direction", "0,0,0"]
    )
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1 and json.loads(result.stdout) == raw
    assert len(calls) == 1 and calls[0][1]["size"] == expected
