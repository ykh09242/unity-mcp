"""MCP filtering and restricted cleanup requests preserve explicit intent."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def transport(monkeypatch):
    def configure(name: str):
        module = importlib.import_module(f"services.tools.{name}")
        send = AsyncMock(return_value={"success": True, "data": {}})
        lookup = AsyncMock(return_value="Fixture@owned")
        monkeypatch.setattr(module, "send_with_unity_instance", send)
        monkeypatch.setattr(module, "get_unity_instance_from_context", lookup)
        return module, send, lookup

    return configure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,expected", [(False, False), (True, True), ("true", True), ("false", False)]
)
async def test_console_mcp_log_opt_in_reaches_editor(transport, value, expected):
    # Given an explicitly selected internal-log policy.
    module, send, _ = transport("read_console")
    # When reading a bounded page.
    await module.read_console(SimpleNamespace(), include_mcp_logs=value, page_size=2)
    # Then the Editor receives the opt-in without a second dispatch.
    send.assert_awaited_once()
    assert send.call_args.args[3].get("includeMcpLogs", False) is expected
    assert send.call_args.args[3]["pageSize"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, "yes", [], {}])
async def test_console_invalid_mcp_policy_stops_before_routing(transport, value):
    # Given an invalid boolean input.
    module, send, lookup = transport("read_console")
    # When the wrapper parses it.
    with pytest.raises(ValueError):
        await module.read_console(SimpleNamespace(), include_mcp_logs=value)
    # Then no Editor IO occurs.
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,expected", [(None, True), (True, True), (False, False), ("false", False)]
)
async def test_build_cleanup_defaults_to_preview_and_preserves_delete_intent(
    transport, value, expected
):
    # Given an explicit build-output selection.
    module, send, _ = transport("manage_build")
    # When cleanup is requested.
    await module.manage_build(
        SimpleNamespace(), action="clean_output", output_path="Builds/Fixture", dry_run=value
    )
    # Then the same instance receives exactly the bounded action and chosen mode.
    assert send.call_args.args[1:3] == ("Fixture@owned", "manage_build")
    assert send.call_args.args[3] == {
        "action": "clean_output",
        "output_path": "Builds/Fixture",
        "dry_run": expected,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "clean_output"},
        {"action": "clean_output", "output_path": " "},
        {"action": "clean_output", "output_path": "Builds/Fixture", "dry_run": "yes"},
        {"action": "build", "dry_run": False},
    ],
)
async def test_invalid_cleanup_request_stops_before_routing(transport, arguments):
    # Given missing selection or invalid cleanup intent.
    module, send, lookup = transport("manage_build")
    # When the wrapper validates the request.
    response = await module.manage_build(SimpleNamespace(), **arguments)
    # Then it fails locally, without running or cleaning a build.
    assert response["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()
