"""UI file/content and CLI identity contracts at real wrapper boundaries."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("contents", ["", ".label { color: red; }"])
def test_ui_file_contents_including_empty_are_encoded(monkeypatch, action, contents):
    import base64

    module = importlib.import_module("services.tools.manage_ui")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="instance")
    )
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "send_mutation", send)
    asyncio.run(
        module.manage_ui(
            SimpleNamespace(), action=action, path="Assets/UI/Empty.uss", contents=contents
        )
    )
    params = send.await_args.args[3]
    assert params["contentsEncoded"] is True
    assert base64.b64decode(params["encodedContents"]).decode("utf-8") == contents


@pytest.mark.parametrize(
    "command,args",
    [
        ("create-canvas", []),
        ("create-text", ["--parent", "Parent"]),
        ("create-button", ["--parent", "Parent"]),
        ("create-image", ["--parent", "Parent"]),
    ],
)
def test_ui_cli_configures_created_identity_instead_of_duplicate_name(monkeypatch, command, args):
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    config = SimpleNamespace(format="json")
    monkeypatch.setattr(ui_module, "get_config", lambda: config)
    calls = []

    async def send(command_type, params, *_args, **_kwargs):
        calls.append((command_type, dict(params)))
        if command_type == "manage_gameobject":
            return {
                "success": True,
                "data": {"instanceID": 202 if params["name"].endswith("_Label") else 101},
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(connection, "send_command", send)
    result = CliRunner().invoke(ui_module.ui, [command, "Duplicate", *args])
    assert result.exit_code == 0, result.output
    for command_type, params in calls:
        if command_type == "manage_components":
            expected_id = (
                202
                if command == "create-button" and params["componentType"] == "TextMeshProUGUI"
                else 101
            )
            assert params["target"] == expected_id, params
            assert params["searchMethod"] == "by_id", params
        elif params["name"].endswith("_Label"):
            assert params["parent"] == 101


@pytest.mark.parametrize(
    "command,args",
    [
        ("create-canvas", []),
        ("create-text", ["--parent", "Parent"]),
        ("create-button", ["--parent", "Parent"]),
        ("create-image", ["--parent", "Parent"]),
    ],
)
def test_ui_cli_actual_shared_runner_stops_on_component_failure(monkeypatch, command, args):
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    config = SimpleNamespace(format="json")
    monkeypatch.setattr(ui_module, "get_config", lambda: config)
    monkeypatch.setattr(connection, "get_config", lambda: config)
    calls = []

    async def send(command_type, params, *_args, **_kwargs):
        calls.append(command_type)
        if command_type == "manage_gameobject":
            return {"success": True, "data": {"instanceID": 101}}
        return {"success": False, "error": "component rejected", "data": {"componentAdded": True}}

    monkeypatch.setattr(connection, "send_command", send)
    result = CliRunner().invoke(ui_module.ui, [command, "Name", *args])
    assert result.exit_code == 1
    assert calls == ["manage_gameobject", "manage_components"]
    assert "Created " not in result.output


@pytest.mark.parametrize(
    "command,args",
    [
        ("create-canvas", []),
        ("create-text", ["--parent", "Parent"]),
        ("create-button", ["--parent", "Parent"]),
        ("create-image", ["--parent", "Parent"]),
    ],
)
def test_ui_cli_missing_created_id_stops_before_name_based_followups(monkeypatch, command, args):
    ui_module = importlib.import_module("cli.commands.ui")
    connection = importlib.import_module("cli.utils.connection")
    config = SimpleNamespace(format="json")
    monkeypatch.setattr(ui_module, "get_config", lambda: config)
    monkeypatch.setattr(connection, "get_config", lambda: config)
    calls = []

    async def send(command_type, params, *_args, **_kwargs):
        calls.append(command_type)
        return {"success": True, "data": {"name": "Duplicate"}}

    monkeypatch.setattr(connection, "send_command", send)
    result = CliRunner().invoke(ui_module.ui, [command, "Duplicate", *args])
    assert result.exit_code == 1
    assert calls == ["manage_gameobject"]
    assert "no valid instanceID" in result.output


@pytest.mark.parametrize("action", ["create", "update"])
def test_ui_omitted_contents_remains_omitted(monkeypatch, action):
    module = importlib.import_module("services.tools.manage_ui")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="instance")
    )
    send = AsyncMock(return_value={"success": False, "error": "contents required"})
    monkeypatch.setattr(module, "send_mutation", send)
    response = asyncio.run(
        module.manage_ui(SimpleNamespace(), action=action, path="Assets/UI/Empty.uss")
    )
    assert response["success"] is False
    assert "encodedContents" not in send.await_args.args[3]


def test_ui_read_empty_encoded_file_returns_empty_contents(monkeypatch):
    module = importlib.import_module("services.tools.manage_ui")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="instance")
    )
    monkeypatch.setattr(
        module,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"contentsEncoded": True, "encodedContents": ""},
            }
        ),
    )
    response = asyncio.run(
        module.manage_ui(SimpleNamespace(), action="read", path="Assets/UI/Empty.uss")
    )
    assert response["data"] == {"contents": ""}
