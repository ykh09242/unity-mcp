"""Real credential leases fence package and Animator admission before socket I/O."""

import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted_side", ["request", "plugin"])
@pytest.mark.parametrize(
    "command,params",
    [
        ("manage_packages", {"action": "add_package"}),
        ("manage_packages", {"action": "add_registry"}),
        ("manage_packages", {"action": "resolve_packages"}),
        ("manage_animation", {"action": "animator_set_parameter"}),
        (
            "batch_execute",
            {"commands": [{"tool": "manage_packages", "params": {"action": "embed_package"}}]},
        ),
    ],
)
async def test_edit_only_lease_cannot_dispatch_compile_or_persistent_runtime_operations(
    command_hub, monkeypatch, restricted_side, command, params
):
    # Given real requester/plugin leases, one edit-only and one with combined grants.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        side: {"unity_mcp_permissions": ["edit"] if side == restricted_side else ["edit", "run"]}
        for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # When native/batch admission bypasses public tool middleware.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", command, params)
        # Then neither request nor plugin authority can be elevated before socket I/O.
        assert result["error"] == "permission_denied"
        assert command_hub.sent == [] and PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,params,grants",
    [
        (
            "manage_packages",
            {"action": "add_package", "package": "com.fixture.tool"},
            ["edit", "run"],
        ),
        ("manage_animation", {"action": "animator_set_parameter", "runtime_only": True}, ["run"]),
        ("manage_packages", {"action": "status"}, ["read"]),
    ],
)
async def test_explicit_complete_authority_reaches_socket(
    command_hub, monkeypatch, command, params, grants
):
    # Given both authenticated leases with exactly the necessary authority.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {side: {"unity_mcp_permissions": grants} for side in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # When the complete operation reaches final admission.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", command, params)
        # Then useful authorized operations still dispatch exactly once.
        assert result["success"] is True
        assert len(command_hub.sent) == 1 and PluginHub._pending == {}
