"""Build configuration authority is checked before public or native work starts."""

from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from core.config import config
from models.models import ToolDefinitionModel
from services.credential_lease import bind_credential_lease
from services.operation_permissions import (
    OperationPermissionError,
    Permission,
    authorize_remote_operation,
    required_permissions,
)
from tests.http.test_remote_permissions import permission_service
from tests.http.test_remote_catalog_gating import RequesterMiddleware
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from services.tools.manage_build import manage_build
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


COMPILING_WRITES = [
    {"action": "settings", "property": "defines", "value": "FIXTURE"},
    {"action": "settings", "property": "DEFINES", "value": ""},
    {"action": "settings", "property": "scripting_backend", "value": "il2cpp"},
    {"action": "settings", "property": "architecture", "value": "arm64"},
    {"action": "settings", "property": "future_setting", "value": "fixture"},
    {"action": "settings", "property": {"invalid": True}, "value": "fixture"},
    {"action": "platform", "target": "android"},
    {"action": "platform", "target": "linux64", "subtarget": "server"},
    {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": True},
    {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": " TRUE "},
    {"action": "batch", "targets": ["android"]},
    {"action": "build", "target": "android", "scripting_backend": "mono"},
    {"action": "build", "target": "android", "scriptingBackend": "mono"},
    {"action": "build_from_plan", "target": "android", "scripting_backend": "mono"},
]

QUERY_OPERATIONS = [
    {"action": "settings", "property": "defines"},
    {"action": "settings", "property": "defines", "value": None},
    {"action": "settings", "property": "scripting_backend", "value": None},
    {"action": "platform"},
    {"action": "platform", "target": None},
    {"action": "platform", "target": "", "subtarget": "server"},
    {"action": "platform", "subtarget": "server"},
    {"action": "profiles"},
    {"action": "profiles", "profile": "", "activate": True},
    {"action": "profiles", "profile": "Assets/Fixture.asset"},
    {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": None},
    {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": False},
    {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": " FALSE "},
]


@pytest.mark.parametrize("params", COMPILING_WRITES)
def test_compiling_build_writes_require_edit_and_run_on_every_route(params):
    for native in (False, True):
        assert required_permissions("manage_build", params, native=native) == {
            Permission.EDIT,
            Permission.RUN,
        }
    for outer in ("batch_execute", "batch_validate", "batch_apply_preview"):
        assert required_permissions(
            outer, {"commands": [{"tool": "manage_build", "params": params}]}
        ) == {Permission.EDIT, Permission.RUN}


@pytest.mark.parametrize("params", QUERY_OPERATIONS)
def test_build_queries_require_only_read_on_every_route(params):
    for native in (False, True):
        assert required_permissions("manage_build", params, native=native) == {Permission.READ}
    for outer in ("batch_execute", "batch_validate", "batch_apply_preview"):
        assert required_permissions(
            outer, {"commands": [{"tool": "manage_build", "params": params}]}
        ) == {Permission.READ}


@pytest.mark.parametrize("property_name", ["product_name", "company_name", "version", "bundle_id"])
def test_build_metadata_writes_retain_edit_only_authority(property_name):
    for native in (False, True):
        assert required_permissions(
            "manage_build",
            {"action": "settings", "property": property_name, "value": ""},
            native=native,
        ) == {Permission.EDIT}


@pytest.mark.parametrize("activate", [0, 1, "0", "1", "", [], {}])
def test_malformed_profile_activation_cannot_be_classified_as_a_query(activate):
    for native in (False, True):
        with pytest.raises(OperationPermissionError):
            required_permissions(
                "manage_build",
                {"action": "profiles", "profile": "Assets/Fixture.asset", "activate": activate},
                native=native,
            )


@pytest.mark.parametrize(
    "backend_params",
    [
        {},
        {"scripting_backend": None},
        {"scripting_backend": ""},
        {"scripting_backend": None, "scriptingBackend": "mono"},
    ],
)
@pytest.mark.parametrize("action", ["build", "build_from_plan"])
def test_build_without_persistent_backend_keeps_run_authority(action, backend_params):
    for native in (False, True):
        assert required_permissions(
            "manage_build", {"action": action, **backend_params}, native=native
        ) == {Permission.RUN}


@pytest.mark.asyncio
@pytest.mark.parametrize("params", COMPILING_WRITES)
@pytest.mark.parametrize(
    "route", ["public", "native", "batch_execute", "batch_validate", "batch_apply_preview"]
)
@pytest.mark.parametrize(
    "request_grants,plugin_grants,allowed",
    [
        (["edit"], ["edit", "run"], False),
        (["edit", "run"], ["edit"], False),
        (["run"], ["edit", "run"], False),
        (["edit", "run"], ["run"], False),
        (["edit", "run"], ["edit", "run"], True),
    ],
)
async def test_build_authority_intersects_both_leases_before_work(
    command_hub, monkeypatch, params, route, request_grants, plugin_grants, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": {"unity_mcp_permissions": request_grants},
        "plugin": {"unity_mcp_permissions": plugin_grants},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)

        async def get_state(_key):
            return "session"

        with bind_credential_lease(requester):
            if route == "public":
                ctx = SimpleNamespace(get_state=get_state)
                if allowed:
                    await authorize_remote_operation("manage_build", params, ctx=ctx)
                else:
                    with pytest.raises(OperationPermissionError):
                        await authorize_remote_operation("manage_build", params, ctx=ctx)
                assert command_hub.sent == []
            else:
                command = "manage_build" if route == "native" else route
                arguments = (
                    params
                    if route == "native"
                    else {"commands": [{"tool": "manage_build", "params": params}]}
                )
                result = await PluginHub.send_command("session", command, arguments)
                assert result["success"] is allowed
                assert len(command_hub.sent) == int(allowed)
                if not allowed:
                    assert result["error"] == "permission_denied"
        assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("params", QUERY_OPERATIONS)
async def test_read_only_build_leases_can_dispatch_true_native_queries(
    command_hub, monkeypatch, params
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {side: {"unity_mcp_permissions": ["read"]} for side in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_build", params)
        assert result["success"] is True
        assert len(command_hub.sent) == 1
        assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_complete_build_authority_still_requires_selected_tool_consent(
    command_hub, monkeypatch, enabled
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    if enabled:
        await command_hub.registry.register_tools_for_session(
            "session", [ToolDefinitionModel(name="manage_build")]
        )
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools",
        lambda: [{"name": "manage_build", "unity_target": "manage_build"}],
    )
    claims = {
        side: {"unity_mcp_permissions": ["read", "edit", "run"]} for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        server = FastMCP("build-consent")
        server.add_middleware(RequesterMiddleware(requester))

        @server.tool(name="manage_build")
        async def build_settings(ctx: Context, action: str, property: str, value: str) -> dict:
            return await manage_build(ctx, action=action, property=property, value=value)

        async with Client(server) as client:
            arguments = {
                "action": "settings",
                "property": "defines",
                "value": "FIXTURE",
                "unity_instance": "Fixture@hash",
            }
            if enabled:
                result = await client.call_tool("manage_build", arguments)
                assert result.data["success"] is True
            else:
                with pytest.raises((ToolError, MCPError)):
                    await client.call_tool("manage_build", arguments)
        assert len(command_hub.sent) == int(enabled)
        assert PluginHub._pending == {}
