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
