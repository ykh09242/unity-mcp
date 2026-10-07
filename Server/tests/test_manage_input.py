"""Behavior and schema contracts for bounded Play Mode input simulation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from pydantic import TypeAdapter, ValidationError

from models.models import MCPResponse
from services.tools.manage_input import InputCommand, manage_input


@pytest.mark.asyncio
async def test_input_tool_registration_exposes_optional_response_schema():
    # Given: the production response annotation must work on Python 3.11 too.
    server = FastMCP("input-response-schema")
    # When: FastMCP builds the tool's Pydantic output schema during registration.
    server.tool(manage_input)
    tool = next(
        tool
        for tool in await server.list_tools(run_middleware=False)
        if tool.name == "manage_input"
    )
    # Then: partial success and capability-error responses remain valid objects.
    schema = tool.output_schema
    assert schema["type"] == "object"
    assert schema["properties"]["success"] == {"type": "boolean"}
    assert schema["properties"]["error"] == {"type": "string"}
    assert "data" in schema["properties"]
    assert not schema.get("required")


@pytest.fixture
def unity_route(monkeypatch):
    # Given: the current session selected one Unity editor.
    send = AsyncMock(return_value={"success": True, "data": {"queued": True}})
    monkeypatch.setattr(
        "services.tools.manage_input.get_unity_instance_from_context",
        AsyncMock(return_value="editor-A"),
    )
    monkeypatch.setattr("services.tools.manage_input.send_with_unity_instance", send)
    return send


@pytest.mark.asyncio
async def test_chord_key_hold_routes_bounded_command_to_selected_editor(unity_route):
    # When: a named key is pressed for several game updates.
    result = await manage_input(SimpleNamespace(), "key", key="LeftShift", frames=12)
    # Then: dispatch preserves duration and routing, with reload replay disabled.
    assert result["success"] is True
    args = unity_route.call_args.args
    assert args[1:3] == ("editor-A", "manage_input")
    assert args[3]["key"] == "LeftShift"
    assert args[3]["frames"] == 12
    assert unity_route.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
async def test_integer_click_target_keeps_integer_identity(unity_route):
    # Given: a scene object identified by a signed integer instance ID.
    target = -1234
    # When: the object receives a click request.
    await manage_input(SimpleNamespace(), "ui_click", target=target)
    # Then: no name conversion can accidentally resolve a different object.
    assert unity_route.call_args.args[3]["target"] == target
    assert "key" not in unity_route.call_args.args[3]


@pytest.mark.asyncio
async def test_touch_preserves_independent_contact_and_screen_coordinates(unity_route):
    # Given: contact 2 can overlap contact 1 on the virtual touchscreen.
    # When: contact 2 begins a bounded touch.
    await manage_input(SimpleNamespace(), "touch", touch_id=2, position=(100, 200), frames=30)
    # Then: Unity gets its stable contact ID, position, and independent duration.
    assert unity_route.call_args.args[3]["touch_id"] == 2
    assert unity_route.call_args.args[3]["position"] == [100.0, 200.0]
    assert unity_route.call_args.args[3]["frames"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("frames", [0, 601, True, 1.5, "5"])
async def test_invalid_duration_never_reaches_unity(unity_route, frames):
    # Given: an unbounded or coerced duration would alter input lifetime.
    # When: the client sends that duration.
    result = await manage_input(SimpleNamespace(), "key", key="Space", frames=frames)
    # Then: validation fails before selecting or dispatching input.
    assert result["success"] is False
    unity_route.assert_not_called()


@pytest.mark.parametrize(
    "parameters",
    [
        {"action": "ui_click"},
        {"action": "key", "key": "Space", "state": "move"},
        {"action": "mouse", "state": "move"},
        {"action": "touch", "position": [1, 2], "touch_id": 0},
        {"action": "touch", "position": [1, float("inf")]},
        {"action": "touch", "position": [1, True]},
        {"action": "touch", "position": [1, 2, 3]},
        {"action": "ui_click", "target": 2**31},
        {"action": "ui_click", "target": True},
    ],
)
def test_invalid_action_arguments_are_rejected(parameters):
    # Given: a malformed input command.
    # When/Then: boundary parsing rejects it.
    with pytest.raises(ValidationError):
        InputCommand.model_validate(parameters)


@pytest.mark.asyncio
async def test_structured_missing_capability_error_is_preserved(unity_route):
    # Given: this editor uses legacy input and cannot inject raw keys.
    failure = {
        "success": False,
        "error": "Input System required",
        "data": {"legacy_raw_input": False},
    }
    unity_route.return_value = failure
    # When: a valid raw input request reaches Unity.
    result = await manage_input(SimpleNamespace(), "key", key="Space")
    # Then: actionable capability details survive the Python facade.
    assert result == failure


def test_input_command_schema_exposes_strict_bounds_and_actions():
    # Given: the typed public command contract.
    # When: a client requests its JSON schema.
    schema = TypeAdapter(InputCommand).json_schema()
    # Then: callers can discover the accepted operations and hard resource limits.
    assert schema["properties"]["frames"]["maximum"] == 600
    assert schema["properties"]["touch_id"]["minimum"] == 1
    assert set(schema["properties"]["action"]["enum"]) == {
        "status",
        "ui_click",
        "key",
        "mouse",
        "touch",
        "release_all",
    }


@pytest.mark.asyncio
async def test_legacy_transport_failure_is_returned_as_public_mapping(unity_route):
    # Given: the legacy transport rejects a disconnected editor before dispatch.
    unity_route.return_value = MCPResponse(success=False, error="Editor unavailable", hint="retry")
    # When: status is requested through the same transport facade.
    result = await manage_input(SimpleNamespace(), "status")
    # Then: callers receive the usual structured mapping rather than a model instance.
    assert result["success"] is False
    assert result["error"] == "Editor unavailable"
    assert result["hint"] == "retry"
