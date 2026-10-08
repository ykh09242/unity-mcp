"""Lighting input rejection must precede every create/configure command."""

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

    monkeypatch.setattr(importlib.import_module("cli.commands.lighting"), "run_command", capture)
    return importlib.import_module("cli.main").cli, state


LOCATIONS = tuple(
    (flag, size, index)
    for flag, size in (("position", 3), ("color", 3), ("intensity", 1))
    for index in range(size)
)


@pytest.mark.parametrize("flag,size,index", LOCATIONS)
@pytest.mark.parametrize("bad", ["", "oops", "nan", "inf", "-inf", "1e100"])
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_invalid_numeric_option_rejects_before_first_command(
    dispatch, flag, size, index, bad, output_format
):
    cli, state = dispatch
    values = ["0"] * size
    values[index] = bad
    result = CliRunner().invoke(
        cli, ["--format", output_format, "lighting", "create", "Owned", f"--{flag}", *values]
    )
    assert result.exit_code == 2, (result.stdout, result.stderr, state)
    assert result.stdout == "" and f"--{flag}" in result.stderr
    assert state["attempts"] == [] and state["encoded"] == []


VALID_OPTIONS = [
    (None, None, None),
    (["0", "-1", "2"], ["0", "0", "0"], "0"),
    (["-1", "2", "3"], ["2", "-0.5", "1"], "-2"),
    (["3.4028235e38", "0", "0"], ["0", "3.4028235e38", "0"], "3.4028235e38"),
    (["1e-100", "0", "0"], ["0", "1e-100", "0"], "1e-100"),
]


@pytest.mark.parametrize("position,color,intensity", VALID_OPTIONS)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_valid_values_preserve_defaults_and_configure_returned_identity(
    dispatch, position, color, intensity, output_format
):
    cli, state = dispatch
    args = ["--format", output_format, "lighting", "create", "Owned", "--type", "Spot"]
    for flag, values in (
        ("position", position),
        ("color", color),
        ("intensity", [intensity] if intensity is not None else None),
    ):
        if values is not None:
            args += [f"--{flag}", *values]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == ""
    calls = state["attempts"]
    assert len(calls) == len(state["encoded"]) == 3 + (color is not None) + (intensity is not None)
    assert calls[0] == (
        "manage_gameobject",
        {
            "action": "create",
            "name": "Owned",
            "position": [float(value) for value in position] if position else [0, 3, 0],
        },
    )
    assert calls[1][1]["componentType"] == "Light" and calls[1][1]["action"] == "add"
    assert calls[2][1]["property"] == "type" and calls[2][1]["value"] == "Spot"
    assert all(
        params["target"] == -123 and params["search_method"] == "by_id" for _, params in calls[1:]
    )
    if color is not None:
        assert calls[3][1]["value"] == {
            "r": float(color[0]),
            "g": float(color[1]),
            "b": float(color[2]),
            "a": 1,
        }
    if intensity is not None:
        assert calls[-1][1]["value"] == float(intensity)


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_valid_options_preserve_stop_on_native_rejection_and_falsey_payload(dispatch, stage):
    cli, state = dispatch
    state["fail_at"] = stage
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "lighting",
            "create",
            "Owned",
            "--color",
            "0",
            "0",
            "0",
            "--intensity",
            "0",
        ],
    )
    assert result.exit_code == 1 and result.stderr == ""
    assert len(state["attempts"]) == len(state["encoded"]) == stage
    assert json.loads(result.stdout) == {
        "success": False,
        "error": "Controlled rejection",
        "data": {"zero": 0, "enabled": False, "reference": None},
    }
