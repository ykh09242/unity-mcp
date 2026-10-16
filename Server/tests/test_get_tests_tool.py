"""Public SDK coverage for paginated test discovery without native enumeration."""

from tests.test_test_discovery_resource_contracts import _run_sdk_scenario


def test_get_tests_pages_filters_errors_and_validation(tmp_path):
    _run_sdk_scenario(
        """
        import anyio, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.tools as tools
        import services.tools.get_tests as discovery
        from services.registry import get_registered_tools
        import transport.unity_transport as transport

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        def payload(result):
            content = result.structured_content
            return content.get("result", content)

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            metadata = [t for t in get_registered_tools() if t["name"] == "get_tests"]
            assert len(metadata) == 1
            assert metadata[0]["group"] == "testing"
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            mcp = FastMCP("paginated-test-discovery")
            tools.register_all_tools(mcp)
            config.transport_mode = "http"
            discovery.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            boundary = AsyncMock()
            transport.PluginHub.send_command_for_instance = boundary
            rows = [{"name": f"Example{i}", "full_name": f"Fixture.Example{i}",
                "mode": "PlayMode", "path": f"Assembly/Fixture/Example{i}"} for i in range(125)]
            for protocol in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=protocol) as client:
                    listed = (await client.list_tools())[0]
                    assert listed.name == "get_tests"
                    assert listed.annotations.read_only_hint is True
                    assert listed.annotations.destructive_hint is False
                    schema = listed.input_schema["properties"]
                    assert set(schema) == {"mode", "name_pattern", "page_size", "cursor"}
                    collected = []
                    for cursor, count, next_cursor in ((0, 50, 50), (50, 50, 100), (100, 25, None), (125, 0, None)):
                        page = {"items": rows[cursor:cursor+50], "cursor": cursor,
                            "nextCursor": next_cursor, "totalCount": 125, "pageSize": 50,
                            "hasMore": next_cursor is not None}
                        boundary.return_value = {"success": True, "data": page}
                        before = boundary.await_count
                        result = await client.call_tool("get_tests", {"mode": "PlayMode",
                            "name_pattern": "Example", "page_size": 50, "cursor": cursor})
                        assert not result.is_error
                        response = payload(result)
                        assert response.get("success") is True, response
                        assert response["data"] == dict(page, partial=True)
                        assert len(response["data"]["items"]) == count
                        collected.extend(response["data"]["items"])
                        assert boundary.await_count == before + 1
                        assert boundary.call_args.args == ("Selected@hash", "get_tests",
                            {"mode": "PlayMode", "filter": "Example", "page_size": 50, "cursor": cursor})
                    assert collected == rows
                    boundary.return_value = {"success": True, "data": {"items": [], "cursor": 0,
                        "nextCursor": None, "totalCount": 0, "pageSize": 50, "hasMore": False}}
                    result = await client.call_tool("get_tests", {})
                    assert payload(result)["data"]["partial"] is False
                    assert boundary.call_args.args == ("Selected@hash", "get_tests", {"page_size": 50, "cursor": 0})
                    error = {"success": False, "error": "discovery_failed", "message": "Failed to retrieve tests",
                        "hint": "retry", "data": {"reason": "native failure"}}
                    boundary.return_value = error
                    result = await client.call_tool("get_tests", {})
                    assert payload(result) == error
                    for arguments in ({"page_size": 0}, {"page_size": -1}, {"page_size": 201},
                        {"page_size": True}, {"page_size": "50"}, {"page_size": 1.5},
                        {"cursor": -1}, {"cursor": 2147483648}, {"cursor": True},
                        {"cursor": "50"}, {"cursor": 1.5}, {"mode": "invalid"}):
                        before = boundary.await_count
                        result = await client.call_tool("get_tests", arguments, raise_on_error=False)
                        assert result.is_error, arguments
                        assert boundary.await_count == before, arguments
        anyio.run(scenario)
        """,
        tmp_path,
    )


def test_get_tests_http_catalog_visibility_keeps_resource_tenant_routing(tmp_path):
    _run_sdk_scenario(
        """
        import anyio, socket, sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from starlette.websockets import WebSocketState
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        import services.tools.get_tests as discovery
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        import transport.unity_transport as transport
        import services.tools.run_tests
        from mcp.shared.exceptions import MCPError

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            metadata = [t for t in get_registered_tools() if t["name"] == "get_tests"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            for hosted in (False, True):
                for protocol in ("2026-07-28", "legacy"):
                    config.transport_mode = "http"
                    config.http_remote_hosted = hosted
                    registry = PluginRegistry()
                    PluginHub.configure(registry)
                    await registry.register("owned", "Owned", "owned-hash", "test",
                        user_id="alice" if hosted else None)
                    await registry.register("foreign", "Foreign", "foreign-hash", "test",
                        user_id="bob" if hosted else None)
                    catalog = [ToolDefinitionModel(name="run_tests")]
                    await registry.register_tools_for_session("owned", catalog)
                    await registry.register_tools_for_session("foreign", catalog)
                    for sid, owner in (("owned", "alice"), ("foreign", "bob")):
                        PluginHub._connections[sid] = SimpleNamespace(
                            state=SimpleNamespace(user_id=owner, plugin_generation=sid + "-generation",
                                plugin_registered=True, plugin_session_id=sid),
                            client_state=WebSocketState.CONNECTED,
                            application_state=WebSocketState.CONNECTED)
                    middleware = UnityInstanceMiddleware()
                    principal = AsyncMock(return_value="alice" if hosted else None)
                    middleware._resolve_user_id = principal
                    transport._resolve_user_id_from_request = principal
                    server = FastMCP("discovery-http-catalog")
                    server.add_middleware(middleware)
                    tools.register_all_tools(server)
                    PluginHub._mcp = server
                    PluginHub._sync_server_tool_visibility(catalog)
                    page = {"items": [], "cursor": 0, "nextCursor": None,
                        "totalCount": 0, "pageSize": 50, "hasMore": False}
                    routed = []
                    async def dispatch(instance, command, params, *, user_id, **kwargs):
                        session_id = await PluginHub._resolve_session_id(instance, user_id=user_id)
                        routed.append((session_id, command, user_id))
                        return {"success": True, "data": page}
                    PluginHub.send_command_for_instance = dispatch
                    async with Client(server, mode=protocol) as client:
                        assert "get_tests" in {t.name for t in await client.list_tools()}, (hosted, protocol)
                        result = await client.call_tool("get_tests", {"unity_instance": "Owned@owned-hash"})
                        content = result.structured_content
                        assert content.get("result", content)["success"] is True
                        assert routed == [("owned", "get_tests", "alice" if hosted else None)]
                        if hosted:
                            before = len(routed)
                            try:
                                result = await client.call_tool("get_tests",
                                    {"unity_instance": "Foreign@foreign-hash"}, raise_on_error=False)
                            except MCPError:
                                pass
                            else:
                                assert result.is_error
                            assert len(routed) == before
                            principal.return_value = None
                            try:
                                result = await client.call_tool("get_tests",
                                    {"unity_instance": "Owned@owned-hash"}, raise_on_error=False)
                            except MCPError:
                                pass
                            else:
                                assert result.is_error
                            assert len(routed) == before
        anyio.run(scenario)
        """,
        tmp_path,
    )
