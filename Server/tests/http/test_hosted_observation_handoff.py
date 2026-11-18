"""Selection and the optional observation must use the requested Editor's grants."""

from copy import copy

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from services.registry import get_registered_tools
from services.tools import register_all_tools
import services.tools.set_active_instance as selection
from tests.http.test_remote_permissions import permission_service
from tests.integration.test_helpers import DummyContext
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_registered_handoff_admits_requested_editor_after_old_grant_revocation(
    command_hub, monkeypatch, mode
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    entries = [
        entry for entry in get_registered_tools() if entry["func"] is selection.set_active_instance
    ]
    assert len(entries) == 1
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", entries)
    monkeypatch.setattr("services.tools.discover_modules", lambda *_: [])
    server = FastMCP("hosted-handoff")
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(selection, "get_unity_instance_middleware", lambda: middleware)
    server.add_middleware(middleware)
    register_all_tools(server)

    old = command_hub.websocket
    old.state.user_id = "alice"
    new = copy(old)
    new.state = copy(old.state)
    new.state.plugin_session_id = "new"
    new.state.plugin_generation = "new-generation"
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Old", "aaaa", "6000", user_id="alice")
    await command_hub.registry.register("new", "New", "bbbb", "6000", user_id="alice")
    PluginHub._connections["new"] = new
    reads = []

    async def reply(message):
        reads.append(message["name"])
        await command_hub.endpoint._handle_command_result(
            new,
            CommandResultMessage(
                id=message["id"], result={"success": True, "data": {"observed_at_unix_ms": 42}}
            ),
        )

    new.send_json = reply
    params = {"instance": "New@bbbb", "include_context": True}
    claims = {key: {"unity_mcp_permissions": ["read"]} for key in ("request", "old", "new")}
    async with permission_service(monkeypatch, claims) as auth:
        for socket, key in ((old, "old"), (new, "new")):
            setattr(
                socket.state, AUTHENTICATED_LEASE_STATE, (await auth.service.validate(key)).lease
            )
        requester = (await auth.service.validate("request")).lease
        with bind_credential_lease(requester):
            async with Client(server, mode=mode) as client:
                if mode == "legacy":
                    selected = await client.call_tool(
                        "set_active_instance", {"instance": "Old@aaaa"}
                    )
                    assert selected.structured_content["success"]
                claims["old"] = {"unity_mcp_permissions": []}
                await auth.service.invalidate_cache("old")
                setattr(
                    old.state, AUTHENTICATED_LEASE_STATE, (await auth.service.validate("old")).lease
                )
                observed = await client.call_tool("set_active_instance", params)
                data = observed.structured_content["data"]
                assert data["handoff"]["instance"] == "New@bbbb"
                assert data["handoff"]["observed_at_unix_ms"] == 42
                assert data["handoff"]["generation"] == "new-generation"
                if mode == "2026-07-28":
                    assert data["selection_persisted"] is False
                assert reads == ["get_editor_state"] and not command_hub.sent

                # A registered callback invoked with a previous transient route must
                # also recheck the principal and use the selected target for its read.
                wrapped = (await server.get_tool("set_active_instance")).fn
                ctx = DummyContext()
                await ctx.set_state("user_id", "alice", serializable=False)
                await ctx.set_state("unity_session_id", "session", serializable=False)
                result = await wrapped(ctx=ctx, **params)
                assert result["data"]["handoff"]["generation"] == "new-generation"

                claims["new"] = {"unity_mcp_permissions": []}
                await auth.service.invalidate_cache("new")
                setattr(
                    new.state, AUTHENTICATED_LEASE_STATE, (await auth.service.validate("new")).lease
                )
                with pytest.raises(MCPError):
                    await client.call_tool("set_active_instance", params)
                refused = await wrapped(ctx=ctx, **params)
                assert refused["data"]["context"]["reason"] == "OperationPermissionError"
                assert refused["data"]["handoff"]["status"] == "unavailable"
                assert reads == ["get_editor_state", "get_editor_state"] and not command_hub.sent
