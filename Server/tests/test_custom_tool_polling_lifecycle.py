"""Actual installed FastMCP/MCP memory transport cancellation reaches polling."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP
from mcp import types

from core.config import config
from models.models import ToolDefinitionModel
import services.custom_tool_service as module
from services.custom_tool_service import CustomToolService

entrypoint = importlib.import_module("services.tools.execute_custom_tool")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("operation", ["notification", "abandon", "session_close"])
@pytest.mark.parametrize("phase", ["initial_dispatch", "sleep"])
async def test_sdk_memory_lifecycle_releases_polling_after_cancellation(
    monkeypatch, operation, phase, mode
):
    # Given: real FastMCP protocol routing over actual paired SDK memory streams.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    mcp = FastMCP("owned-polling-lifecycle-proof")
    service = CustomToolService(mcp)
    definition = ToolDefinitionModel(name="build", requires_polling=True, max_poll_seconds=600)
    monkeypatch.setattr(service, "get_tool_definition", AsyncMock(return_value=definition))
    monkeypatch.setattr(
        entrypoint, "resolve_project_id_for_unity_instance", lambda target: "owned-project"
    )
    entered = asyncio.Event()
    handler_done = asyncio.Event()
    blocked = asyncio.Event()
    seen_ids = []
    cancelled_phase = []
    active_at_phase_exit = []

    async def wait_in_phase():
        entered.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            cancelled_phase.append(phase)
            raise
        finally:
            # Reservation must remain held until the service itself exits.
            active_at_phase_exit.append(service._active_polls)

    async def dispatch(*args, **kwargs):
        if phase == "initial_dispatch":
            await wait_in_phase()
        return {"_mcp_status": "pending", "_mcp_poll_interval": 5}

    async def sleep(delay):
        await wait_in_phase()

    monkeypatch.setattr(module, "send_with_unity_instance", dispatch)
    if phase == "sleep":
        # Replace only the service's runtime reference, leaving the SDK untouched.
        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(
                sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError
            ),
        )

    @mcp.tool
    async def lifecycle_poll(ctx: Context):
        seen_ids.append(ctx.request_id)
        await ctx.set_state("unity_instance", "OwnedProject@owned-hash")
        try:
            return await entrypoint.execute_custom_tool(ctx, "build", {})
        finally:
            handler_done.set()

    client = Client(mcp, mode=mode)
    async with client:
        protocol_version = client.protocol_version
        assert protocol_version is not None
        call = asyncio.create_task(client.call_tool("lifecycle_poll"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            assert service._active_polls == 1
            assert service._polls_by_session == {(None, "owned-hash"): 1}
            assert service._polls_by_user == {None: 1}
            # When: cancellation is delivered by the actual protocol/client/session.
            if operation == "notification":
                await client.session.send_notification(
                    types.CancelledNotification(
                        params=types.CancelledNotificationParams(
                            requestId=seen_ids[0], reason="owned verification"
                        )
                    )
                )
            elif operation == "abandon":
                call.cancel()
            else:
                await client.close()
            # Then: SDK cancellation reaches the real MCP entrypoint and service.
            await asyncio.wait_for(handler_done.wait(), timeout=3)
            assert cancelled_phase == [phase]
            assert active_at_phase_exit == [1]
            assert service._active_polls == 0
            assert service._polls_by_session == service._polls_by_user == {}
            print(
                f"SDK lifecycle: mode={mode}, protocol={protocol_version}, operation={operation}, phase={phase}; cancelled and released"
            )
        finally:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
