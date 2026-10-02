import logging
from unittest.mock import AsyncMock, Mock

import pytest

from core.logging_decorator import log_execution
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from transport.plugin_hub import PluginHub

SENTINEL = "PRIVATE_CODE_CREDENTIAL_IMAGE_SENTINEL"


@pytest.fixture(autouse=True)
def capture_server_logs(monkeypatch):
    # main installs file handlers and disables propagation. Capture records even
    # when startup tests have already imported main earlier in the same process.
    monkeypatch.setattr(logging.getLogger("mcp-for-unity-server"), "propagate", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_decorators_do_not_log_arguments_results_or_exceptions(caplog, level):
    caplog.set_level(level, logger="mcp-for-unity-server")

    @log_execution("safe_tool", "Tool")
    def sync(value, fail=False):
        if fail:
            raise ValueError(value)
        return value

    @log_execution("safe_resource", "Resource")
    async def asynchronous(value, fail=False):
        return sync(value, fail)

    assert sync(SENTINEL) == SENTINEL
    assert await asynchronous(SENTINEL) == SENTINEL
    for call in (sync, asynchronous):
        with pytest.raises(ValueError, match=SENTINEL):
            result = call(SENTINEL, fail=True)
            if call == asynchronous:
                await result
    assert SENTINEL not in caplog.text
    assert "completed" in caplog.text and "ValueError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("polling", [False, True])
async def test_custom_tool_payloads_stay_out_of_logs(monkeypatch, caplog, polling):
    caplog.set_level(logging.DEBUG, logger="mcp-for-unity-server")
    service = CustomToolService(Mock())
    definition = ToolDefinitionModel(name="safe_tool", description="Test", requires_polling=polling)
    monkeypatch.setattr(service, "get_tool_definition", AsyncMock(return_value=definition))
    response = {"success": True, "data": {"status": "complete", "contents": SENTINEL}}
    monkeypatch.setattr("services.custom_tool_service.send_with_unity_instance", AsyncMock(return_value=response))
    result = await service.execute_tool("project", "safe_tool", "instance", {"code": SENTINEL})
    assert result.success
    assert SENTINEL not in caplog.text
    assert "Custom tool completed" in caplog.text


@pytest.mark.asyncio
async def test_invalid_plugin_messages_do_not_log_payloads(caplog):
    caplog.set_level(logging.DEBUG, logger="mcp-for-unity-server")
    hub = PluginHub.__new__(PluginHub)
    for payload in ([SENTINEL], {"type": "unknown", "content": SENTINEL},
                    {"type": "command_result", "id": [], "result": SENTINEL}):
        await hub.on_receive(AsyncMock(), payload)
    assert SENTINEL not in caplog.text
    assert caplog.records
