"""CLI creation requests and replay of explicitly supplied owned native exchanges."""

import importlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize(
    "command,args,expected",
    [
        (
            "create-canvas",
            ["--render-mode", "WorldSpace"],
            {"element_type": "canvas", "render_mode": "WorldSpace"},
        ),
        (
            "create-text",
            ["--parent", "Canvas", "--text", "", "--position", "12.5", "-7"],
            {"element_type": "text", "parent": "Canvas", "text": "", "position": [12.5, -7.0]},
        ),
        (
            "create-button",
            ["--parent", "Canvas", "--text", "Launch"],
            {"element_type": "button", "parent": "Canvas", "text": "Launch"},
        ),
        (
            "create-image",
            ["--parent", "Canvas", "--sprite", "Assets/Icon.asset"],
            {"element_type": "image", "parent": "Canvas", "sprite": "Assets/Icon.asset"},
        ),
    ],
)
def test_cli_submits_one_closed_creation_shape(monkeypatch, command, args, expected):
    # Given
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(ui_module, "get_config", lambda: SimpleNamespace(format="json"))
    calls = []

    async def send(command_type, params, *_args, **_kwargs):
        calls.append((command_type, params))
        return {"success": True, "message": "created", "data": {"instanceID": -124, "name": "Same"}}

    monkeypatch.setattr(connection, "send_command", send)
    # When
    result = CliRunner().invoke(ui_module.ui, [command, "Same", *args])
    # Then
    assert result.exit_code == 0, result.output
    assert calls == [("manage_ugui", {"action": "create_cli", "name": "Same", **expected})]
    assert json.loads(result.output)["data"]["instanceID"] == -124


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e100"])
def test_nonfinite_or_overflow_cli_position_never_sends(monkeypatch, value):
    # Given
    ui_module = importlib.import_module("cli.commands.ui")
    calls = []
    monkeypatch.setattr(ui_module, "run_command", lambda *args: calls.append(args))
    # When
    result = CliRunner().invoke(
        ui_module.ui, ["create-text", "Text", "--parent", "Canvas", "--position", value, "0"]
    )
    # Then
    assert result.exit_code == 2
    assert calls == []


def test_omitted_text_position_is_two_anchored_coordinates(monkeypatch):
    # Given
    ui_module = importlib.import_module("cli.commands.ui")
    monkeypatch.setattr(ui_module, "get_config", lambda: SimpleNamespace(format="json"))
    captured = []

    def send(command_type, params, _config):
        captured.append((command_type, params))
        return {"success": True, "data": {"instanceID": 321}}

    monkeypatch.setattr(ui_module, "run_command", send)
    # When
    result = CliRunner().invoke(ui_module.ui, ["create-text", "Text", "--parent", "Canvas"])
    # Then
    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0][1]["position"] == [0, 0]
    assert captured[0][1]["text"] == "New Text"


@pytest.mark.parametrize("identifier", [True, False, 0, 2**31, -(2**31) - 1, "not-an-id", 1.5])
def test_malformed_native_identity_cannot_print_creation_success(monkeypatch, identifier):
    # Given
    ui_module = importlib.import_module("cli.commands.ui")
    monkeypatch.setattr(ui_module, "get_config", lambda: SimpleNamespace(format="json"))
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "get_config", lambda: SimpleNamespace(format="json"))
    calls = []

    async def send(*args):
        calls.append(args)
        return {"success": True, "data": {"instanceID": identifier}}

    monkeypatch.setattr(connection, "send_command", send)
    # When
    result = CliRunner().invoke(ui_module.ui, ["create-canvas", "Canvas"])
    # Then
    assert result.exit_code == 1
    assert len(calls) == 1
    assert json.loads(result.output)["success"] is False


def test_transport_loss_does_not_retry_or_compensate(monkeypatch):
    # Given: the response may have been lost after native commit.
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(ui_module, "get_config", lambda: SimpleNamespace(format="json"))
    calls = []

    async def send(*args):
        calls.append(args)
        raise connection.UnityConnectionError("response lost")

    monkeypatch.setattr(connection, "send_command", send)
    # When
    result = CliRunner().invoke(ui_module.ui, ["create-canvas", "Canvas"])
    # Then
    assert result.exit_code == 1
    assert len(calls) == 1


def test_native_cli_creation_requires_edit_permission():
    # Given
    policy = importlib.import_module("services.operation_permissions")
    # When / Then: use the production policy for the native-only CLI action.
    assert policy.required_permissions(
        "manage_ugui", {"action": "create_cli"}, native=True
    ) == frozenset({policy.Permission.EDIT})


@pytest.mark.parametrize("case", ["canvas_overlay", "text_position", "button", "image_sprite"])
def test_cli_consumes_actual_owned_native_output_when_supplied(monkeypatch, case):
    # Given: a later QA extraction supplies exact TestContext UI17_NATIVE_EXCHANGE records.
    # No native interaction occurs in this replay. Missing evidence remains skipped/open.
    supplied = os.environ.get("UI17_NATIVE_EXCHANGES")
    if not supplied:
        pytest.skip("Actual owned native exchange input has not been supplied.")
    raw = Path(supplied).read_bytes()
    assert len(raw) <= 2 * 1024 * 1024
    records = json.loads(raw)
    assert set(records) == {"canvas_overlay", "text_position", "button", "image_sprite"}
    exchange = records[case]
    assert set(exchange) == {"request", "response"}
    request, native = exchange["request"], exchange["response"]
    assert request["action"] == "create_cli" and native["success"] is True
    assert type(native["data"]["instanceID"]) is int and native["data"]["instanceID"] != 0
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    normalize = importlib.import_module("models.unity_response").normalize_unity_response
    monkeypatch.setattr(ui_module, "get_config", lambda: SimpleNamespace(format="json"))
    calls = []

    async def send(command_type, params, *_args, **_kwargs):
        calls.append((command_type, params))
        return normalize({"status": "success", "result": native})

    monkeypatch.setattr(connection, "send_command", send)
    args = ["create-" + request["element_type"], request["name"]]
    for field in ("parent", "text", "sprite", "render_mode"):
        if field in request:
            args.extend(["--" + field.replace("_", "-"), str(request[field])])
    if "position" in request:
        assert len(request["position"]) == 2 and all(math.isfinite(v) for v in request["position"])
        args.extend(["--position", *(str(v) for v in request["position"])])
    # When
    result = CliRunner().invoke(ui_module.ui, args)
    # Then: retain every actual initial-root response field, including its native ID.
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == native
    assert len(calls) == 1 and calls[0][0] == "manage_ugui"
