"""Production Play Scenario responses must match the public SDK output schema."""

import pytest
from fastmcp import Client

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_http_request_deadline import request_server
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("failure", ["unavailable", "capacity"])
async def test_play_scenario_transport_refusal_preserves_sdk_recovery_hint(
    command_hub, monkeypatch, mode, failure
):
    # Given: the production tool and selected project lose transport admission.
    async with Client(request_server(), mode=mode) as client:
        await client.call_tool("select_fixture")
        if failure == "unavailable":
            await command_hub.registry.unregister("session")
        else:
            monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)

        # When: the real SDK validates the tool's structured refusal response.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "run", "name": "fixture"}
        )

        # Then: nullable envelope fields do not hide the machine-readable recovery action.
        response = result.structured_content
        assert response["success"] is False
        assert response["hint"] == "retry"
        assert response["data"]["reason"] == (
            "no_unity_session" if failure == "unavailable" else "command_capacity"
        )
        assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_play_scenario_sdk_preserves_valid_unity_dictionary_and_nested_nulls(
    command_hub, mode
):
    # Given: Unity returns a valid job result with meaningful nested null fields.
    payload = {
        "success": True,
        "message": "Scenario job queued",
        "data": {"job_id": "0123456789abcdef0123456789abcdef", "status": "queued", "error": None},
    }

    async def reply(message):
        command_hub.sent.append(message)
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(id=message["id"], result=payload),
        )

    command_hub.websocket.send_json = reply
    async with Client(request_server(), mode=mode) as client:
        await client.call_tool("select_fixture")

        # When: a real SDK client receives the production tool response.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "run", "name": "fixture"}
        )

        # Then: Unity dictionaries retain their original fields and null semantics.
        assert result.structured_content == payload
        assert len(command_hub.sent) == 1
