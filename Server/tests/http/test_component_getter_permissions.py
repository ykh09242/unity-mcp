"""Scoped component inspection admits user getters at the URI and native boundaries."""

import json

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from services.operation_permissions import (
    OperationPermissionError,
    Permission,
    required_permissions,
)
from services.resources import register_all_resources
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.unity_instance_middleware import UnityInstanceMiddleware


ROUTES = [
    ("components", "get_gameobject_components"),
    ("component/Probe", "get_gameobject_component"),
    ("component/Probe?component_index=1", "get_gameobject_component_selected"),
]
GETTER_GRANTS = {Permission.READ, Permission.EDIT, Permission.RUN}


@pytest.mark.parametrize("command", [command for _, command in ROUTES])
@pytest.mark.parametrize("batch", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
def test_native_getter_flags_and_batch_children(command, batch):
    def permissions(params):
        if batch:
            return required_permissions(
                batch, {"commands": [{"tool": command, "params": params}]}, native=True
            )
        return required_permissions(command, params, native=True)

    assert permissions({}) == {Permission.READ}
    for key in ("evaluate_getters", "evaluateGetters"):
        for value in (False, "false"):
            assert permissions({key: value}) == {Permission.READ}
        for value in (True, "true"):
            assert permissions({key: value}) == GETTER_GRANTS
        for invalid in (None, 1, 0, "", "True", "yes", [], {}):
            with pytest.raises(OperationPermissionError):
                permissions({key: invalid})
    for second in (True, False):
        with pytest.raises(OperationPermissionError):
            permissions({"evaluate_getters": True, "evaluateGetters": second})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_grants,plugin_grants",
    [(["read"], GETTER_GRANTS), (GETTER_GRANTS, ["read"]), (GETTER_GRANTS, GETTER_GRANTS)],
)
async def test_public_getter_resource_checks_both_authorities_before_socket_io(
    command_hub, monkeypatch, request_grants, plugin_grants
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    grants = {
        "request": {"unity_mcp_permissions": list(request_grants)},
        "plugin": {"unity_mcp_permissions": list(plugin_grants)},
    }
    server = FastMCP("component-getter-grants")
    server.add_middleware(UnityInstanceMiddleware())
    register_all_resources(server)
    async with permission_service(monkeypatch, grants) as fixture:
        request = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)

        with bind_credential_lease(request):
            async with Client(server) as client:
                for route, command in ROUTES:
                    base = "mcpforunity://scene/gameobject/42/" + route
                    separator = "&" if "?" in base else "?"
                    meta = {"unity_instance": "Fixture@hash"}
                    for query in (
                        "",
                        "evaluate_getters=false",
                        "evaluate_getters=true",
                        "evaluate_getters=",
                        "evaluate_getters=1",
                        "evaluate_getters=false&%65valuate_getters=true",
                    ):
                        before = len(command_hub.sent)
                        uri = base + (separator + query if query else "")
                        evaluate = query == "evaluate_getters=true"
                        if query not in {"", "evaluate_getters=false", "evaluate_getters=true"} or (
                            evaluate and request_grants != GETTER_GRANTS
                        ):
                            # The protocol masks middleware exceptions. A refusal must send nothing.
                            with pytest.raises(MCPError):
                                await client.read_resource(uri, meta=meta)
                            assert len(command_hub.sent) == before
                            continue
                        response = json.loads((await client.read_resource(uri, meta=meta))[0].text)
                        if evaluate and plugin_grants != GETTER_GRANTS:
                            assert response["error"] == "permission_denied"
                            assert len(command_hub.sent) == before
                        else:
                            assert response["success"] is True, response
                            [sent] = command_hub.sent[before:]
                            assert sent["name"] == command
                            assert sent["params"].get("evaluateGetters", False) is evaluate
