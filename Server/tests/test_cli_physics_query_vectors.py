"""Physics query vectors reject bad CLI input before command dispatch."""

import copy
import importlib
import socket

import httpx
import pytest
from click.testing import CliRunner


QUERIES = (("raycast", "origin", "direction"), ("linecast", "start", "end"))


@pytest.fixture
def dispatch(monkeypatch):
    """Capture registered Click requests synchronously with network/token access denied."""

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

    monkeypatch.setattr(importlib.import_module("cli.commands.physics"), "run_command", capture)
    return importlib.import_module("cli.main").cli, calls


@pytest.mark.parametrize("query,first,second", QUERIES)
@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize(
    "value", ["", "x,0,0", "0,,0", "nan,0,0", "inf,0,0", "1e100,0,0", "-inf,0,0"]
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_bad_vector_has_named_diagnostic_and_no_dispatch(
    dispatch, query, first, second, which, value, output_format
):
    cli, calls = dispatch
    vectors = ["0,0,0", "1,0,0"]
    vectors[which] = value
    field = (first, second)[which]
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "physics",
            query,
            f"--{first}",
            vectors[0],
            f"--{second}",
            vectors[1],
        ],
    )
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, calls)
    assert result.stdout == ""
    assert f"--{field}" in result.stderr and "Invalid value" in result.stderr
    assert calls == []


@pytest.mark.parametrize("query,first,second", QUERIES)
@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("dimension,value", [("3d", "0,0"), ("3d", "0"), ("2d", "0")])
def test_short_vector_respects_receiver_dimension_minimum(
    dispatch, query, first, second, which, dimension, value
):
    cli, calls = dispatch
    vectors = ["0,0,0", "1,0,0"]
    vectors[which] = value
    field = (first, second)[which]
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "physics",
            query,
            f"--{first}",
            vectors[0],
            f"--{second}",
            vectors[1],
            "--dimension",
            dimension,
        ],
    )
    assert result.exit_code == 2, (result.stdout, result.stderr, calls)
    assert result.stdout == "" and f"--{field}" in result.stderr
    assert calls == []


@pytest.mark.parametrize("query,first,second", QUERIES)
@pytest.mark.parametrize(
    "dimension,vector,expected",
    [
        (None, "0, 1e-2,-2", [0.0, 0.01, -2.0]),
        ("2d", "0,1", [0.0, 1.0]),
        ("2D", "0,1,2,3", [0.0, 1.0, 2.0, 3.0]),
        ("3d", "0,1,2,1e100", [0.0, 1.0, 2.0, 1e100]),
        ("3d", "3.4028235e38,0,0", [3.4028235e38, 0.0, 0.0]),
        ("3d", "1e-100,0,0", [1e-100, 0.0, 0.0]),
        ("2d", "0,1,1e100", [0.0, 1.0, 1e100]),
    ],
)
def test_valid_vectors_preserve_two_d_defaults_and_extra_coordinates(
    dispatch, query, first, second, dimension, vector, expected
):
    cli, calls = dispatch
    args = ["--format", "json", "physics", query, f"--{first}", vector, f"--{second}", vector]
    if dimension is not None:
        args += ["--dimension", dimension]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == "" and len(calls) == 1
    tool, params = calls[0]
    assert tool == "manage_physics"
    assert params[first] == expected and params[second] == expected
    assert params["dimension"] == (dimension or "3d")
