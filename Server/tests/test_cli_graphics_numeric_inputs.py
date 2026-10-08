"""Graphics CSV input errors must be named and rejected before dispatch."""

import copy
import importlib
import json
import socket

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def dispatch(monkeypatch):
    """Capture synchronous admission/encoding with network and credential reads denied."""

    def denied(*_args, **_kwargs):
        raise AssertionError("Network or credential access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("socketpair", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    state = {"attempts": [], "encoded": [], "fail_at": None}

    def capture(tool, params, _config):
        state["attempts"].append((tool, copy.deepcopy(params)))
        try:
            payload = httpx.Request(
                "POST", "http://127.0.0.1/api/command", json={"type": tool, "params": params}
            ).read()
        except ValueError as exc:
            raise connection.UnityConnectionError(str(exc)) from exc
        state["encoded"].append(json.loads(payload))
        if len(state["attempts"]) == state["fail_at"]:
            raise connection.UnityCommandError(
                {
                    "success": False,
                    "error": "Controlled rejection",
                    "data": {"zero": 0, "enabled": False, "reference": None},
                }
            )
        return {
            "success": True,
            "data": {"instanceID": -123, "zero": 0, "enabled": False, "reference": None},
        }

    monkeypatch.setattr(importlib.import_module("cli.commands.graphics"), "run_command", capture)
    return importlib.import_module("cli.main").cli, state


COLORS = (
    ("skybox-set-ambient", "color", "color"),
    ("skybox-set-ambient", "equator-color", "equator_color"),
    ("skybox-set-ambient", "ground-color", "ground_color"),
    ("skybox-set-fog", "color", "fog_color"),
)


@pytest.mark.parametrize("command,flag,_key", COLORS)
@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        "x,0,0",
        "0,,0",
        "0",
        "0,0",
        "nan,0,0",
        "inf,0,0",
        "-inf,0,0",
        "1e100,0,0",
        "0,0,0,1e100",
        "0,0,0,1,nan",
    ],
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_invalid_color_is_named_and_never_dispatched(
    dispatch, command, flag, _key, bad, output_format
):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli, ["--format", output_format, "graphics", command, f"--{flag}", bad]
    )
    assert result.exit_code == 2, (result.stdout, result.stderr, state)
    assert result.stdout == "" and f"--{flag}" in result.stderr
    assert state["attempts"] == [] and state["encoded"] == []


@pytest.mark.parametrize("command,flag,key", COLORS)
@pytest.mark.parametrize(
    "value,expected",
    [
        ("0,0,0", [0, 0, 0]),
        ("0, 1e-2,-2", [0, 0.01, -2]),
        ("2,0,0,.5", [2, 0, 0, 0.5]),
        ("0,0,0,1,1e100", [0, 0, 0, 1, 1e100]),
        ("3.4028235e38,0,0", [3.4028235e38, 0, 0]),
        ("1e-100,0,0", [1e-100, 0, 0]),
    ],
)
def test_valid_colors_preserve_hdr_negative_alpha_rounding_and_extras(
    dispatch, command, flag, key, value, expected
):
    cli, state = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", "graphics", command, f"--{flag}", value])
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == "" and len(state["encoded"]) == 1
    assert state["attempts"] == [
        ("manage_graphics", {"action": command.replace("-", "_"), key: expected})
    ]


@pytest.mark.parametrize("command", ["skybox-set-ambient", "skybox-set-fog"])
def test_omitted_color_preserves_defaults(dispatch, command):
    cli, state = dispatch
    result = CliRunner().invoke(cli, ["--format", "json", "graphics", command])
    assert result.exit_code == 0 and result.stderr == ""
    assert state["attempts"] == [("manage_graphics", {"action": command.replace("-", "_")})]


@pytest.mark.parametrize(
    "bad", ["", " ", "x,0", "0,,1", "nan,0", "1.0,0", "1e0,0", "0,0", "-1,0", "1,2", "2147483648,0"]
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_invalid_order_is_named_and_never_dispatched(dispatch, bad, output_format):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli, ["--format", output_format, "graphics", "feature-reorder", "--order", bad]
    )
    assert result.exit_code == 2, (result.stdout, result.stderr, state)
    assert result.stdout == "" and "--order" in result.stderr
    assert state["attempts"] == [] and state["encoded"] == []


@pytest.mark.parametrize(
    "value,expected",
    [("0", [0]), ("2,0,1", [2, 0, 1]), (" 1, +0 ", [1, 0]), ("0,1,2,3", [0, 1, 2, 3])],
)
def test_complete_supplied_permutations_preserve_integer_csv_syntax(dispatch, value, expected):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli, ["--format", "json", "graphics", "feature-reorder", "--order", value]
    )
    assert result.exit_code == 0 and result.stderr == ""
    assert state["attempts"] == [
        ("manage_graphics", {"action": "feature_reorder", "order": expected})
    ]


@pytest.mark.parametrize(
    "command,args",
    [("skybox-set-ambient", ["--color", "0,0,0"]), ("feature-reorder", ["--order", "0"])],
)
def test_valid_csv_preserves_native_error_and_falsey_data(dispatch, command, args):
    cli, state = dispatch
    state["fail_at"] = 1
    result = CliRunner().invoke(cli, ["--format", "json", "graphics", command, *args])
    assert result.exit_code == 1 and result.stderr == ""
    assert len(state["attempts"]) == len(state["encoded"]) == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": "Controlled rejection",
        "data": {"zero": 0, "enabled": False, "reference": None},
    }
