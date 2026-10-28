"""Real SDK/Hub reads through a synthetic registered Editor reconnect.

The peer supplies native replies; no real Editor reload is claimed by this test.
Authentication, registration, public/native admission and response parsing are real.
"""

import asyncio
import importlib

from fastmcp import Client, FastMCP
import pytest

from services.registry import get_registered_tools
from services.registry.operation_context import scheduler
import services.tools as tools_package
from tests.http.test_plugin_read_sharing import Peer, peer  # noqa: F401 -- fixture
from tests.timing import watchdog_timeout
from transport import unity_transport
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_detailed_same_job_reads_reconnected_peer(peer, monkeypatch, mode):
    jobs = importlib.import_module("services.tools.run_tests")
    definitions = [item for item in get_registered_tools() if item["name"] == "get_test_job"]
    assert len(definitions) == 1
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setattr(tools_package, "discover_modules", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(tools_package, "get_registered_tools", lambda: definitions)

    async def selection(ctx):
        await ctx.set_state("user_id", "owned-user")
        await ctx.set_state("unity_session_id", "owned-session")
        return "Owned@ownedhash"

    monkeypatch.setattr(tools_package, "get_unity_instance_from_context", selection)
    monkeypatch.setattr(jobs, "get_unity_instance_from_context", selection)
    replacement = Peer(
        generation="reloaded-generation",
        plugin_lease=getattr(peer.state, AUTHENTICATED_LEASE_STATE),
        requester_lease=peer.requester_lease,
    )
    replacement.state.plugin_arbitration_generation = "c" * 32
    send = unity_transport.send_with_unity_instance
    sends = []

    async def reconnect_then_send(sender, instance, command, params, **kwargs):
        # Reload happens after public admission and before native admission.
        sends.append(command)
        PluginHub._connections["owned-session"] = replacement
        return await send(sender, instance, command, params, **kwargs)

    monkeypatch.setattr(unity_transport, "send_with_unity_instance", reconnect_then_send)
    server = FastMCP("job-reload-observation")
    tools_package.register_all_tools(server, project_scoped_tools=False)
    async with peer.requester_lease.own(), Client(server, mode=mode) as client:
        assert {t.name for t in await client.list_tools()} == {"get_test_job"}
        task = asyncio.create_task(
            client.call_tool(
                "get_test_job",
                {
                    "job_id": "saved-job",
                    "include_details": True,
                    "page_size": 100,
                    "cursor": "saved-cursor",
                },
                raise_on_error=False,
            )
        )
        command = await replacement.next_command()
        assert command["name"] == "get_test_job"
        assert command["params"] == {
            "job_id": "saved-job",
            "includeDetails": True,
            "page_size": 100,
            "cursor": "saved-cursor",
        }
        await replacement.reply(
            command,
            {
                "success": True,
                "data": {
                    "job_id": "saved-job",
                    "status": "succeeded",
                    "native_control": {"released": True},
                    "result_page": {"passed": 272, "next_cursor": "page-next"},
                },
            },
        )
        result = await asyncio.wait_for(task, watchdog_timeout())
    assert not result.is_error
    payload = result.structured_content
    assert payload["success"] is True
    data = payload["data"]
    assert data["job_id"] == "saved-job" and data["result_page"]["passed"] == 272
    assert data["native_cleanup_pending"] is False
    assert sends == ["get_test_job"]
    assert peer.counts() == {} and replacement.counts() == {"get_test_job": 1}
    assert scheduler().snapshot()["active"] == 0
