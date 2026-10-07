"""Readiness waits observe native jobs and preserve recoverable timeout information."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import json
import inspect

import anyio
import pytest
from click.testing import CliRunner
from pydantic import TypeAdapter, ValidationError

from cli.commands.editor import editor
from cli.utils.config import CLIConfig
from services.tools.manage_editor import manage_editor


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(
        "services.tools.manage_editor.get_unity_instance_from_context",
        AsyncMock(return_value="selected-editor"),
    )
    return SimpleNamespace()


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled", "timed_out"])
def test_play_wait_returns_only_after_terminal_native_status(context, monkeypatch, terminal):
    # Given a real readiness protocol at the transport seam, with one reload disconnect.
    requests = []

    async def send(send_fn, instance, tool, params, **kwargs):
        requests.append((instance, params.copy()))
        job_id = requests[0][1]["job_id"]
        if len(requests) == 2:
            raise ConnectionError("domain reload disconnected")
        return {
            "success": True,
            "data": {"job_id": job_id, "status": "running" if len(requests) == 1 else terminal},
        }

    monkeypatch.setattr("services.tools.manage_editor.send_with_unity_instance", send)
    monkeypatch.setattr("services.tools.manage_editor.anyio.sleep", AsyncMock())

    # When waiting for a first simulation frame.
    async def invoke():
        return await manage_editor(context, action="play", wait_until="first_frame")

    result = anyio.run(invoke)

    # Then only the first request mutates, and terminal failure is not readiness success.
    assert result["success"] is (terminal == "succeeded")
    assert result["data"]["status"] == terminal
    assert [item[1]["action"] for item in requests] == [
        "play",
        "get_play_mode_job",
        "get_play_mode_job",
    ]
    assert all(item[0] == "selected-editor" for item in requests)


def test_wait_deadline_cancels_stalled_transport_and_retains_job_id(context, monkeypatch):
    # Given a native job acknowledgement followed by a transport that never completes.
    started = []

    async def send(send_fn, instance, tool, params, **kwargs):
        if params["action"] == "play":
            started.append(params["job_id"])
            return {"success": True, "data": {"job_id": started[0], "status": "running"}}
        await anyio.sleep_forever()

    monkeypatch.setattr("services.tools.manage_editor.send_with_unity_instance", send)

    # When the bounded wait expires, even an in-flight read is cancelled.
    async def invoke():
        return await manage_editor(
            context, action="play", wait_until="scene_loaded", timeout_seconds=1
        )

    result = anyio.run(invoke)

    # Then the caller can recover by ID without assuming the native job's final result.
    assert result["success"] is False
    assert result["error"] == "play_readiness_timeout"
    assert result["data"]["job_id"] == started[0]
    assert result["data"]["status"] == "wait_timed_out"


def test_play_wait_fails_closed_on_wrong_job_response(context, monkeypatch):
    # Given a response belonging to another readiness request.
    monkeypatch.setattr(
        "services.tools.manage_editor.send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"job_id": "different-job", "status": "succeeded"},
            }
        ),
    )

    # When observing readiness.
    async def invoke():
        return await manage_editor(context, action="play", wait_until="scene_loaded")

    result = anyio.run(invoke)

    # Then another job cannot satisfy this request.
    assert result["success"] is False
    assert result["error"] == "play_readiness_job_mismatch"


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["game-view-size"], {"action": "get_game_view_size"}),
        (
            ["game-view-size", "--width", "1280", "--height", "720"],
            {"action": "set_game_view_size", "width": 1280, "height": 720},
        ),
        (
            ["game-view-size", "--aspect-ratio", "16:9"],
            {"action": "set_game_view_size", "aspect_ratio": "16:9"},
        ),
        (
            ["game-view-size", "--restore-token", "fixture"],
            {"action": "restore_game_view_size", "restore_token": "fixture"},
        ),
        (["play-status", "fixture"], {"action": "get_play_mode_job", "job_id": "fixture"}),
        (["cancel-play-wait", "fixture"], {"action": "cancel_play_mode_job", "job_id": "fixture"}),
    ],
)
def test_cli_routes_size_and_readiness_operations(arguments, expected):
    # Given the JSON CLI and a command boundary.
    response = {"success": True, "data": {"value": 7}}
    config = CLIConfig(format="json")
    with (
        patch("cli.commands.editor.get_config", return_value=config),
        patch("cli.commands.editor.run_command", return_value=response) as send,
    ):
        # When selecting one operation.
        result = CliRunner().invoke(editor, arguments)
    # Then the native command receives the selected operation and JSON stays one document.
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response
    send.assert_called_once_with("manage_editor", expected, config)


@pytest.mark.parametrize(
    "arguments",
    [
        ["game-view-size", "--width", "1920"],
        ["game-view-size", "--width", "8193", "--height", "720"],
        ["game-view-size", "--aspect-ratio", "16:9", "--preset", "Full HD"],
    ],
)
def test_cli_rejects_conflicting_or_invalid_size_before_dispatch(arguments):
    # Given a CLI transport that must not be called on invalid input.
    with patch("cli.commands.editor.run_command") as send:
        # When supplying invalid selectors.
        result = CliRunner().invoke(editor, arguments)
    # Then validation ends before the editor can be changed.
    assert result.exit_code == 2
    send.assert_not_called()


def test_cli_wait_polls_instead_of_replaying_play():
    # Given the native async job API exposed through the raw HTTP command endpoint.
    calls = []

    def send(command, params, config, **kwargs):
        calls.append(params.copy())
        return {
            "success": True,
            "data": {
                "job_id": calls[0]["job_id"],
                "status": "running" if len(calls) == 1 else "succeeded",
            },
        }

    with (
        patch("cli.commands.editor.get_config", return_value=CLIConfig(format="json")),
        patch("cli.commands.editor.run_command", side_effect=send),
        patch("cli.commands.editor.time.sleep"),
    ):
        # When the CLI waits for native readiness.
        result = CliRunner().invoke(editor, ["play", "--wait-until", "first_frame"])
    # Then readiness completes through a read-only second request.
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["status"] == "succeeded"
    assert [call["action"] for call in calls] == ["play", "get_play_mode_job"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 0),
        ("width", 8193),
        ("width", True),
        ("width", "1920"),
        ("height", -1),
        ("timeout_seconds", 0),
        ("timeout_seconds", 301),
        ("wait_until", "rendered"),
        ("aspect_ratio", "16/9"),
    ],
)
def test_public_schema_rejects_invalid_readiness_and_size_inputs(field, value):
    # Given the exact annotation consumed by FastMCP's public tool schema.
    adapter = TypeAdapter(inspect.signature(manage_editor).parameters[field].annotation)
    # When an invalid value crosses that boundary.
    with pytest.raises(ValidationError):
        adapter.validate_python(value)
    # Then the invalid request is rejected before invoking Unity.
