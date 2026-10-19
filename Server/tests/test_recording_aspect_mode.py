"""Exercise aspect policy through the actual FastMCP schema and transport boundary."""

import importlib

import anyio
import pytest
from fastmcp import Client, FastMCP


@pytest.mark.parametrize("aspect_mode", [None, "stretch", "fit", "crop"])
def test_recording_aspect_mode_routes_when_schema_accepts(monkeypatch, aspect_mode):
    # Given a real FastMCP tool with only the external Unity transport replaced.
    module = importlib.import_module("services.tools.manage_recording")
    calls = []

    async def instance(ctx):
        return "Project@aspect-fixture"

    async def send(sender, unity_instance, command, params):
        calls.append(params)
        return {"success": True, "data": {"job_id": "aspect"}}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("recording-aspect")
    server.tool(name="manage_recording")(module.manage_recording)

    async def exercise():
        async with Client(server) as client:
            tool = next(
                item for item in await client.list_tools() if item.name == "manage_recording"
            )
            assert tool.input_schema["properties"]["aspect_mode"]["enum"] == [
                "stretch",
                "fit",
                "crop",
            ]
            payload = {"action": "start"}
            if aspect_mode is not None:
                payload["aspect_mode"] = aspect_mode
            # When a caller requests an aspect policy.
            result = await client.call_tool("manage_recording", payload)
            # Then the requested policy crosses the Unity boundary, preserving legacy omission.
            assert result.data["success"] is True
            assert len(calls) == 1
            if aspect_mode in (None, "stretch"):
                assert "aspect_mode" not in calls[0]
            else:
                assert calls[0]["aspect_mode"] == aspect_mode
            invalid = await client.call_tool(
                "manage_recording", {"action": "start", "aspect_mode": "zoom"}, raise_on_error=False
            )
            assert invalid.is_error
            assert len(calls) == 1

    anyio.run(exercise)
