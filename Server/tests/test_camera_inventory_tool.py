"""Camera inventory has equivalent MCP tools and resource read paths."""

import json
from types import SimpleNamespace

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.resources import ResourceResult
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError

from core.config import config
from services.credential_lease import bind_credential_lease
from services.resources import register_all_resources
from services.resources import cameras
from services.tools import register_all_tools
from tests.http.test_project_capabilities import install_native_replies
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.models import CommandResultMessage
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.unity_instance_middleware import UnityInstanceMiddleware


TOOL = "inspect_scene_cameras"
URI = "mcpforunity://scene/cameras"
SELECT = {"unity_instance": "Fixture@hash"}
OBSERVATION = "io.github.ykh09242.unity-mcp/observation"
INVENTORY = {
    "unityCameras": [{"name": "Fixture Camera", "instanceID": 17, "hasBrain": True}],
    "cinemachineCameras": [{"name": "Fixture Virtual Camera", "instanceID": 19}],
    "cinemachineInstalled": True,
    "brain": {"gameObject": "Fixture Camera", "activeCameraName": "Fixture Virtual Camera"},
}


def inventory_server(monkeypatch, profile="core") -> FastMCP:
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    server = FastMCP("camera-inventory")
    server.add_middleware(UnityInstanceMiddleware())
    register_all_tools(server)
    register_all_resources(server)
    return server


async def read_inventory(client: Client) -> dict:
    return json.loads((await client.read_resource(URI, meta=SELECT))[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_default_tools_only_client_can_find_and_read_the_same_inventory(
    command_hub, monkeypatch, mode
):
    await install_native_replies(command_hub, {"get_cameras": INVENTORY})
    async with Client(inventory_server(monkeypatch), mode=mode, cache=False) as client:
        tools = {item.name: item for item in await client.list_tools()}
        assert not {"manage_cameras", "manage_camera"} & tools.keys()
        tool = tools[TOOL]
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert set(tool.input_schema["properties"]) == {"unity_instance"}
        resource = next(item for item in await client.list_resources() if str(item.uri) == URI)
        assert tool.meta[OBSERVATION] == resource.meta[OBSERVATION]

        query = {"required_values": ["unityCameras", "cinemachineCameras"]}
        both = (await client.call_tool("search_unity_tools", query)).structured_content
        (group,) = both["route_groups"]
        assert group["representative"]["tool"] == TOOL
        assert [route["uri"] for route in group["alternatives"]] == [URI]
        tools_only = await client.call_tool(
            "search_unity_tools",
            {**query, "route_surfaces": "tools", "host_tool_names": [TOOL]},
        )
        (group,) = tools_only.structured_content["route_groups"]
        assert [item.type for item in tools_only.content] == ["text"]
        route = group["representative"]
        assert (route["tool"], route["arguments"], group["alternatives"]) == (TOOL, {}, [])

        fixed = (await client.call_tool(TOOL, SELECT)).structured_content
        assert fixed == await read_inventory(client)
        assert fixed["success"] is True and fixed["data"] == INVENTORY
        assert [(sent["name"], sent["params"]) for sent in command_hub.sent] == [
            ("get_cameras", {}),
            ("get_cameras", {}),
        ]

        command_hub.sent.clear()
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, {**SELECT, "action": "ensure_brain"})
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_equivalent_camera_routes_prefer_host_confirmed_tools(command_hub, monkeypatch):
    server = inventory_server(monkeypatch, profile="full")
    focused = "manage_cameras"
    legacy = "manage_camera"
    evidence = {"candidates": [focused], "present": [focused], "complete": False}
    selectors = {"required_values": ["unityCameras"], "route_surfaces": "tools"}
    async with Client(server, mode="legacy", cache=False) as client:
        assert {TOOL, focused, legacy} <= {item.name for item in await client.list_tools()}
        for report, expected in (
            (None, [TOOL, focused, legacy]),
            ({**evidence, "present": []}, [TOOL, focused, legacy]),
            (evidence, [focused, TOOL, legacy]),
        ):
            args = {**selectors}
            if report is not None:
                args["host_tool_evidence"] = report
            result = (await client.call_tool("search_unity_tools", args)).structured_content
            (group,) = result["route_groups"]
            routes = [group["representative"], *group["alternatives"]]
            assert [route["tool"] for route in routes] == expected

        args = {**selectors, "host_tool_evidence": evidence}
        mixed = await client.call_tool("search_unity_tools", {**args, "route_surfaces": "both"})
        (group,) = mixed.structured_content["route_groups"]
        routes = [group["representative"], *group["alternatives"]]
        assert [route.get("tool") or route["uri"] for route in routes] == [
            focused,
            URI,
            TOOL,
            legacy,
        ]

        no_match = await client.call_tool(
            "search_unity_tools", {**args, "observation_source": "asset_file"}
        )
        (suggestion,) = no_match.structured_content["no_match"]["alternatives"]
        assert suggestion["tool"] == focused

        server.disable(names={focused}, components={"tool"})
        result = (await client.call_tool("search_unity_tools", args)).structured_content
        (group,) = result["route_groups"]
        routes = [group["representative"], *group["alternatives"]]
        assert [route["tool"] for route in routes] == [TOOL, legacy]
        assert focused not in result["host_inventory"]["statuses"]
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_stdio_fixed_read_uses_the_explicitly_selected_editor(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)

    async def discover(_middleware, _ctx):
        return [
            SimpleNamespace(id="Other@other", hash="other", name="Other", port=6400),
            SimpleNamespace(id="Fixture@hash", hash="hash", name="Fixture", port=6401),
        ]

    dispatches = []

    async def selected_editor(command, params, *, instance_id):
        dispatches.append((command, params, instance_id))
        return {
            "success": (command, params, instance_id) == ("get_cameras", {}, "Fixture@hash"),
            "data": INVENTORY,
        }

    monkeypatch.setattr(UnityInstanceMiddleware, "_discover_instances", discover)
    monkeypatch.setattr(cameras, "async_send_command_with_retry", selected_editor)
    async with Client(inventory_server(monkeypatch), mode="legacy", cache=False) as client:
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, {})
        assert dispatches == []
        fixed = (
            await client.call_tool(TOOL, SELECT, meta={"unity_instance": "Other@other"})
        ).structured_content
        assert dispatches == [("get_cameras", {}, "Fixture@hash")]
        assert fixed == await read_inventory(client)
        assert fixed["success"] is True and fixed["data"] == INVENTORY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_read", "plugin_read", "resource_policy"),
    [
        (True, True, "allow"),
        (False, True, "allow"),
        (True, False, "allow"),
        (True, True, "hidden"),
        (True, True, "denied"),
        (True, True, "read_denied"),
        (True, True, "redacted"),
        (True, True, "scalar"),
        (True, True, "retargeted"),
        (True, True, "native_disabled"),
    ],
)
async def test_hosted_inventory_respects_resource_policy_and_both_credentials(
    command_hub, monkeypatch, request_read, plugin_read, resource_policy
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    server = inventory_server(monkeypatch)

    class CameraResourcePolicy(Middleware):
        async def on_list_resources(self, context, call_next):
            return [
                resource
                for resource in await call_next(context)
                if resource_policy != "hidden" or str(resource.uri) != URI
            ]

        async def check_resource_access(self, context):
            if resource_policy == "denied" and str(context.message.uri) == URI:
                raise AuthorizationError("Camera resource denied by fixture")

        async def on_read_resource(self, context, call_next):
            if str(context.message.uri) != URI:
                return await call_next(context)
            await self.check_resource_access(context)
            if resource_policy == "read_denied":
                raise AuthorizationError("Camera inventory denied at read time")
            if resource_policy == "scalar":
                return ResourceResult("null")
            if resource_policy == "retargeted":
                await context.fastmcp_context.set_state(
                    "unity_instance", "Other@other", serializable=False
                )
            result = await call_next(context)
            if resource_policy == "redacted":
                (content,) = result.contents
                filtered = json.loads(content.content)
                filtered["data"]["unityCameras"] = []
                del filtered["data"]["brain"]
                filtered["policy"] = "redacted"
                return ResourceResult(json.dumps(filtered))
            return result

    server.add_middleware(CameraResourcePolicy())
    if resource_policy == "native_disabled":

        async def disabled(message):
            command_hub.sent.append(message)
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": False, "error": "Resource is disabled in Unity"},
                ),
            )

        command_hub.websocket.send_json = disabled
    else:
        await install_native_replies(command_hub, {"get_cameras": INVENTORY})

    claims = {
        "request": {"unity_mcp_permissions": ["read" if request_read else "edit"]},
        "plugin": {"unity_mcp_permissions": ["read" if plugin_read else "run"]},
    }
    app = server.http_app(stateless_http=True, json_response=True)
    wire_methods = []

    async def record_method(request):
        if request.method == "POST":
            wire_methods.append(json.loads(request.content)["method"])

    def client_factory(**kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            event_hooks={"request": [record_method]},
            **kwargs,
        )

    transport = StreamableHttpTransport("http://fixture/mcp", httpx_client_factory=client_factory)
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            async with (
                app.router.lifespan_context(app),
                Client(transport, mode="2026-07-28", cache=False) as client,
            ):
                if request_read:
                    names = {tool.name for tool in await client.list_tools()}
                    assert (TOOL in names) is (resource_policy not in {"hidden", "denied"})
                if not request_read or resource_policy in {"denied", "read_denied"}:
                    with pytest.raises(MCPError):
                        await read_inventory(client)
                elif resource_policy == "hidden":
                    assert URI not in {str(item.uri) for item in await client.list_resources()}

                if (
                    request_read
                    and plugin_read
                    and resource_policy in {"allow", "native_disabled", "redacted"}
                ):
                    wire_methods.clear()
                    fixed = (await client.call_tool(TOOL, SELECT)).structured_content
                    assert "tools/call" in wire_methods and "resources/read" not in wire_methods
                    assert [(sent["name"], sent["params"]) for sent in command_hub.sent] == [
                        ("get_cameras", {})
                    ]
                    assert fixed == await read_inventory(client)
                    assert fixed["success"] is (resource_policy != "native_disabled")
                    if resource_policy == "redacted":
                        assert fixed["policy"] == "redacted"
                        assert fixed["data"]["unityCameras"] == [] and "brain" not in fixed["data"]
                else:
                    with pytest.raises((ToolError, MCPError)):
                        await client.call_tool(TOOL, SELECT)
                    if not plugin_read:
                        assert (await read_inventory(client))["success"] is False
                    assert command_hub.sent == []
