"""Recording schema, routing and failure contracts, with no live Unity or socket server."""

import importlib

import anyio
import pytest
from fastmcp import Client, FastMCP

from services.registry import get_registered_tools


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_recording_registered_schema_and_routed_jobs(monkeypatch, mode):
    module = importlib.import_module("services.tools.manage_recording")
    calls = []
    responses = [
        {
            "success": True,
            "data": {"job_id": "fixture", "status": "recording", "frames_recorded": 0},
        },
        {
            "success": True,
            "data": {
                "job_id": "fixture",
                "status": "completed",
                "frames_recorded": 2,
                "output_path": "E:/Project/Captures/Recordings/fixture.mp4",
                "variable_frame_rate": True,
            },
        },
        {"success": False, "error": "Unknown recording job", "data": {"diagnostic": "preserved"}},
        {"success": True, "data": {"supported": False, "reason": "Linux unsupported"}},
    ]

    async def instance(ctx):
        return "Project@recording-fixture"

    async def send(sender, unity_instance, command, params):
        calls.append((unity_instance, command, params))
        return responses[len(calls) - 1]

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    metadata = next(tool for tool in get_registered_tools() if tool["name"] == "manage_recording")
    server = FastMCP("recording-contract")
    server.tool(name="manage_recording", **metadata["kwargs"])(module.manage_recording)

    async def exercise():
        async with Client(server, mode=mode) as client:
            tool = next(
                item for item in await client.list_tools() if item.name == "manage_recording"
            )
            properties = tool.input_schema["properties"]
            assert properties["action"]["enum"] == ["capabilities", "start", "status", "stop"]
            assert properties["fps"]["minimum"] == 1
            assert properties["fps"]["maximum"] == 30
            assert properties["duration_seconds"]["maximum"] == 60
            assert properties["width"]["maximum"] == 1920
            assert "job_id" in properties
            payloads = [
                {
                    "action": "start",
                    "capture_source": "scene_view",
                    "duration_seconds": 1.5,
                    "width": 640,
                    "height": 480,
                    "fps": 10,
                    "file_name": "fixture.mp4",
                    "output_folder": "Captures/Recordings/Fixture",
                },
                {"action": "status", "job_id": "fixture"},
                {"action": "stop", "job_id": "missing"},
                {"action": "capabilities"},
            ]
            for index, payload in enumerate(payloads):
                result = await client.call_tool("manage_recording", payload)
                assert result.data == responses[index]
                assert calls[index] == ("Project@recording-fixture", "manage_recording", payload)
            assert len(calls) == 4  # start returns immediately and does not transparently poll.

    anyio.run(exercise)


def test_recording_rejects_invalid_request_before_transport(monkeypatch):
    module = importlib.import_module("services.tools.manage_recording")
    calls = []

    async def instance(ctx):
        return "Project@recording-fixture"

    async def send(*args, **kwargs):
        calls.append(args)
        return {"success": True}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("recording-validation")
    server.tool(name="manage_recording")(module.manage_recording)

    async def exercise():
        async with Client(server) as client:
            schema_invalid = (
                [
                    {"action": "start", "duration_seconds": value}
                    for value in (0, 61, "2", True, float("nan"), float("inf"))
                ]
                + [{"action": "start", "fps": value} for value in (0, 31, 1.5, "15", True)]
                + [{"action": "start", "width": value} for value in (0, 1922, 640.0, True)]
                + [{"action": "start", "capture_source": "camera"}, {"action": "erase"}]
            )
            for payload in schema_invalid:
                result = await client.call_tool("manage_recording", payload, raise_on_error=False)
                assert result.is_error, payload
            contract_invalid = [
                {"action": "start", "width": 641},
                {
                    "action": "start",
                    "width": 1920,
                    "height": 1920,
                    "fps": 30,
                    "duration_seconds": 60,
                },
                {"action": "start", "output_folder": "Captures/Recordings/../Recordings"},
                {"action": "start", "file_name": "../fixture.mp4"},
                {"action": "status"},
                {"action": "stop", "job_id": " "},
            ]
            for payload in contract_invalid:
                result = await client.call_tool("manage_recording", payload)
                assert result.data["success"] is False, payload
                assert result.data["error"]
            assert calls == []

    anyio.run(exercise)
