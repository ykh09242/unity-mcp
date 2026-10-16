"""Versioned nested schemas through the installed SDK, isolated from legacy stubs."""

import pytest

from .test_custom_tool_sdk_registration import run_sdk_regression


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_bounded_ref_discovery_preserves_rich_contract_and_legacy_policy(mode):
    source = """
        import asyncio, copy
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from fastmcp.server.middleware import Middleware
        from fastmcp.server.middleware.dereference import DereferenceRefsMiddleware
        from pydantic import BaseModel
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module
        class Leaf(BaseModel):
            count: int
        class Sentinel(Middleware): pass
        async def scenario():
            app = FastMCP("bounded-ref-discovery")
            before, after = Sentinel(), Sentinel()
            app.middleware.insert(0, before)
            app.add_middleware(after)
            original = app.middleware[1]
            assert type(original) is DereferenceRefsMiddleware
            first = module.CustomToolService(app)
            policy = app.middleware[1]
            service = module.CustomToolService(app)
            assert app.middleware == [before, policy, after]
            assert app.middleware[1] is policy  # Repeated setup is idempotent.
            resources = []
            original.on_list_resource_templates = AsyncMock(return_value=resources)
            resource_context, resource_next = object(), AsyncMock()
            assert await policy.on_list_resource_templates(resource_context, resource_next) is resources
            original.on_list_resource_templates.assert_awaited_once_with(resource_context, resource_next)
            @app.tool(name="a_legacy")
            async def legacy(node: Leaf) -> int: return node.count
            legacy_component = await app.get_tool("a_legacy")
            assert "$defs" in legacy_component.parameters
            definitions = {"Bounded16": {"type": "integer"}}
            for level in reversed(range(16)):
                reference = {"$ref": "#/$defs/Bounded" + str(level + 1)}
                definitions["Bounded" + str(level)] = {"type": "object", "properties": {"left": reference, "right": copy.deepcopy(reference)}, "required": ["left", "right"]}
            schema = {"type": "object", "properties": {"node": {"$ref": "#/$defs/Bounded0"}}, "$defs": definitions}
            output = {"$ref": "#/$defs/Bounded0", "$defs": copy.deepcopy(definitions)}
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock()
            service.register_global_tools([ToolDefinitionModel(name="z_rich", schema_version=1,
                input_schema=schema, output_data_schema=output)])
            # Fail fast if the SDK tries to expand even one rich ref DAG.
            import fastmcp.server.middleware.dereference as dereference
            original_dereference = dereference.dereference_refs
            visited = []
            def bounded_only(value):
                assert not any(key.startswith("Bounded") for key in value.get("$defs", {})), "Rich refs entered SDK expansion"
                visited.append(value)
                return original_dereference(value)
            dereference.dereference_refs = bounded_only
            async with Client(app, mode=__MODE__) as client:
                listed = await client.list_tools()
                assert [tool.name for tool in listed] == ["a_legacy", "z_rich"]
                assert listed[1].input_schema == schema
                assert listed[1].output_schema["$defs"] == definitions
                assert listed[1].output_schema["allOf"][0]["then"]["properties"]["data"] == {"$ref": "#/$defs/Bounded0"}
                assert "$defs" not in listed[0].input_schema and visited
                assert (await client.call_tool("a_legacy", {"node": {"count": 4}})).data == 4
                invalid = await client.call_tool("z_rich", {"node": "wrong"}, raise_on_error=False)
                assert invalid.is_error and module.send_with_unity_instance.await_count == 0
            # Unknown subclasses remain untouched and cannot silently opt into bounded v1 claims.
            class Foreign(DereferenceRefsMiddleware): pass
            unsupported = FastMCP("unsupported-listing-policy")
            foreign = Foreign()
            unsupported.middleware[0] = foreign
            blocked = module.CustomToolService(unsupported)
            try: blocked.register_global_tools([ToolDefinitionModel(name="unsafe", schema_version=1, input_schema={"type": "object"})])
            except ValueError: pass
            else: raise AssertionError("Unsupported dereference policy admitted a rich descriptor")
            assert unsupported.middleware[0] is foreign
            assert await unsupported.list_tools() == []
        asyncio.run(scenario())
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_native_compatible_null_default_projection_keeps_authoritative_null(mode):
    source = """
        import asyncio
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module
        async def scenario():
            app = FastMCP("nullable-default-projection")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {}})
            descriptor = {"name": "nullable", "schema_version": 1,
                "parameters": [{"name": "value", "type": "integer", "required": False, "default_value": None},
                    {"name": "node", "type": "object", "required": False, "default_value": None},
                    {"name": "flexible", "type": "object", "required": False, "default_value": None},
                    {"name": "values", "type": "object", "required": False, "default_value": None}],
                "input_schema": {"type": "object", "additionalProperties": False, "properties": {
                    "value": {"type": ["integer", "null"], "default": None},
                    "node": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}], "default": None},
                    "flexible": {"default": 7}, "values": {"default": [1, 2]}},
                    "$defs": {"Node": {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}}}}
            service.register_global_tools([ToolDefinitionModel.model_validate(descriptor)])
            async with Client(app, mode=__MODE__) as client:
                listed = (await client.list_tools())[0].input_schema
                assert listed == descriptor["input_schema"], (listed, descriptor["input_schema"])
                assert (await client.call_tool("nullable", {})).structured_content["success"]
                assert module.send_with_unity_instance.call_args.args[3] == {"value": None, "node": None, "flexible": 7, "values": [1, 2]}
                rejected = await client.call_tool("nullable", {"node": {"count": "bad"}}, raise_on_error=False)
                assert rejected.is_error and module.send_with_unity_instance.await_count == 1
        asyncio.run(scenario())
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_untyped_rich_content_only_preserves_text_envelope(mode):
    source = """
        import asyncio, json
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module
        async def scenario():
            app = FastMCP("rich-content-only")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"kept": 1}})
            service.register_global_tools([ToolDefinitionModel(name="text", structured_output=False,
                schema_version=1, input_schema={"type": "object"})])
            async with Client(app, mode=__MODE__) as client:
                assert (await client.list_tools())[0].output_schema is None
                result = await client.call_tool("text", {})
                assert result.structured_content is None
                assert json.loads(result.content[0].text)["data"] == {"kept": 1}
                assert module.send_with_unity_instance.await_count == 1
        asyncio.run(scenario())
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_nested_invalid_default_refuses_whole_transport_catalog(mode, transport):
    source = """
        import asyncio, copy
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from starlette.websockets import WebSocketState
        from core.config import config
        import services.custom_tool_service as module
        from services.tools import sync_tool_visibility_from_unity
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.models import RegisterMessage
        from transport.legacy import unity_connection as legacy

        async def scenario():
            config.transport_mode = __TRANSPORT__
            config.http_remote_hosted = False
            app = FastMCP("atomic-nested-default")
            service = module.CustomToolService(app)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            PluginHub._notify_committed_catalog = AsyncMock()
            PluginHub._notify_mcp_tool_list_changed = AsyncMock()
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "a"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"kept": True}})
            good = {"name": "stable", "schema_version": 1, "is_built_in": False,
                "input_schema": {"type": "object", "additionalProperties": False}}
            rows = [good]
            if __TRANSPORT__ == "http":
                hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
                ws = AsyncMock()
                ws.state = SimpleNamespace(user_id=None)
                ws.client_state = ws.application_state = WebSocketState.CONNECTED
                await hub._handle_register(ws, RegisterMessage(project_name="A", project_hash="a"))
                async def publish():
                    await hub.on_receive(ws, {"type": "register_tools", "tools": rows})
            else:
                conn = legacy.UnityConnection(port=1, instance_id="A@a")
                conn.sock = object()
                conn.session_generation = "authenticated-generation"
                legacy._unity_connection_pool = legacy.UnityConnectionPool()
                legacy._unity_connection_pool._connections[conn.instance_id] = conn
                legacy.get_unity_connection = lambda *a: conn
                legacy.get_authenticated_stdio_generation = AsyncMock(return_value=conn.session_generation)
                conn.send_command = lambda *a, **kw: {"success": True, "data": {"tools": rows}}
                async def publish():
                    return await sync_tool_visibility_from_unity("A@a", notify=False)
            await publish()
            prior = service._sources[(__TRANSPORT__, None, "a")]
            notify_count = PluginHub._notify_committed_catalog.await_count
            if __TRANSPORT__ == "http":
                prior_session = await registry.get_session(ws.state.plugin_session_id)
                prior_revision = prior_session.tools_revision
                prior_tools = dict(prior_session.tools)
            async with Client(app, mode=__MODE__) as client:
                old = await client.list_tools()
                assert [tool.name for tool in old] == ["stable"]
                bad = copy.deepcopy(good)
                bad["name"] = "bad_default"
                bad["input_schema"] = {"type": "object", "properties": {"node": {
                    "type": "object", "properties": {"count": {"type": "integer"}},
                    "required": ["count"], "default": {"count": "not-an-integer"}}}}
                # Include a valid new member and a bad disabled member: neither can leak.
                rows = [{**good, "name": "new_member"}, {**bad, "enabled": False}]
                outcome = await publish()
                if __TRANSPORT__ == "stdio": assert "error" in outcome
                assert service._sources[(__TRANSPORT__, None, "a")] is prior
                assert PluginHub._notify_committed_catalog.await_count == notify_count
                assert module.send_with_unity_instance.await_count == 0
                assert await client.list_tools() == old
                if __TRANSPORT__ == "http":
                    current = await registry.get_session(ws.state.plugin_session_id)
                    assert current.tools_revision == prior_revision and current.tools == prior_tools
                    ws.close.assert_not_awaited()
                assert (await client.call_tool("stable", {})).structured_content["data"] == {"kept": True}
                assert module.send_with_unity_instance.await_count == 1
            if __TRANSPORT__ == "http": await PluginHub.shutdown()
        asyncio.run(scenario())
    """
    run_sdk_regression(
        source.replace("__MODE__", repr(mode)).replace("__TRANSPORT__", repr(transport))
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("refresh_listing", [False, True])
def test_refresh_captures_output_policy_and_revalidates_new_nested_contract(mode, refresh_listing):
    source = """
        import asyncio, copy, json
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("captured-rich-policy")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def send(*args, **kwargs):
                calls.append(args[3])
                if len(calls) == 1:
                    entered.set(); await release.wait()
                    return {"success": True, "data": 7}
                return {"success": True, "data": "new"}
            module.send_with_unity_instance = send
            old = ToolDefinitionModel(name="refresh", schema_version=1,
                input_schema={"type": "object"}, output_data_schema={"type": "integer"})
            service.register_global_tools([old])
            async with Client(app, mode=__MODE__) as client:
                # Capture the old advertised contract before admitting old work.
                await client.list_tools()
                pending = asyncio.create_task(client.call_tool("refresh", {}))
                await asyncio.wait_for(entered.wait(), 2)
                new_schema = {"type": "object", "properties": {"node": {
                    "type": "object", "properties": {"label": {"type": "string", "enum": ["new"]}},
                    "required": ["label"], "additionalProperties": False}}, "required": ["node"]}
                new = ToolDefinitionModel(name="refresh", schema_version=1,
                    input_schema=new_schema, output_data_schema={"type": "string"})
                service.register_global_tools([new])
                if __REFRESH_LISTING__: await client.list_tools()
                release.set()
                # Already admitted work is checked against its captured old output contract.
                if __REFRESH_LISTING__:
                    # MCP2.3 validates against its latest cached listing after response receipt.
                    # The server's valid old-generation result is therefore rejected by this client.
                    try: await pending
                    except RuntimeError as exc: assert "7 is not of type 'string'" in str(exc)
                    else: raise AssertionError("Expected the observed client-generation limitation")
                    assert len(calls) == 1  # This does not replay admitted work.
                else:
                    result = await pending
                    assert result.structured_content["data"] == 7
                listed = (await client.list_tools())[0]
                assert listed.input_schema == new_schema
                rejected = await client.call_tool("refresh", {"node": {"label": 1}}, raise_on_error=False)
                assert rejected.is_error and len(calls) == 1
                accepted = await client.call_tool("refresh", {"node": {"label": "new"}})
                assert accepted.structured_content["data"] == "new" and len(calls) == 2
                # SDK-visible mutable schema dictionaries cannot mutate validation state.
                listed.input_schema["properties"]["node"]["properties"]["label"]["type"] = "integer"
                component = await app.get_tool("refresh")
                component.parameters["properties"]["node"]["properties"]["label"]["type"] = "integer"
                refused = await client.call_tool("refresh", {"node": {"label": 2}}, raise_on_error=False)
                assert refused.is_error and len(calls) == 2
        asyncio.run(scenario())
    """
    run_sdk_regression(
        source.replace("__MODE__", repr(mode)).replace("__REFRESH_LISTING__", repr(refresh_listing))
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("hosted", [False, True])
def test_project_and_tenant_fallback_uses_selected_rich_contract(mode, hosted):
    source = """
        import asyncio, importlib, json
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module
        execute = importlib.import_module("services.tools.execute_custom_tool")
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def scenario():
            config.http_remote_hosted = __HOSTED__
            app = FastMCP("selected-rich-contract")
            app.tool(name="execute_custom_tool")(execute.execute_custom_tool)
            service = module.CustomToolService(app, project_scoped_tools=False)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=app)
            selected = ["A@a", "tenant-a"]
            async def instance(ctx): return selected[0]
            async def user(ctx): return selected[1] if __HOSTED__ else None
            module.get_unity_instance_from_context = execute.get_unity_instance_from_context = instance
            module.get_user_id_from_context = execute.get_user_id_from_context = user
            module.resolve_project_id_for_unity_instance = execute.resolve_project_id_for_unity_instance = lambda value: value.rsplit("@", 1)[1]
            sent = []
            async def send(fn, instance, command, params, **kwargs):
                sent.append((instance, kwargs["user_id"], params))
                return {"success": True, "data": params["node"]["value"]}
            module.send_with_unity_instance = send
            def definition(kind, default):
                return ToolDefinitionModel(name="same", schema_version=1, input_schema={
                    "type": "object", "properties": {
                        "node": {"type": "object", "properties": {"value": {"type": kind}}, "required": ["value"], "additionalProperties": False},
                        "nullable": {"type": ["integer", "null"], "default": default}},
                    "required": ["node"], "additionalProperties": False}, output_data_schema={"type": kind})
            first, second = definition("integer", 1), definition("string", 2)
            if __HOSTED__:
                config.transport_mode = "http"
                for user_id, definition in [("tenant-a", first), ("tenant-b", second)]:
                    await registry.register(user_id, "A", "a", "fixture", user_id=user_id)
                    async with PluginHub._lock:
                        await registry.register_tools_for_session(user_id, [definition],
                            _publish=lambda current, tools: PluginHub._publish_session_catalog(
                                current, tools, registry._sessions, generation=current.session_id))
            else:
                service.replace_project_catalog("a", [first], kind="manual", publish_global=True)
                service.replace_project_catalog("b", [second], kind="manual", publish_global=True)
            async with Client(app, mode=__MODE__) as client:
                if __HOSTED__:
                    assert [tool.name for tool in await client.list_tools()] == ["execute_custom_tool"]
                else:
                    assert next(t for t in await client.list_tools() if t.name == "same").input_schema == first.input_schema
                selected[:] = ["A@a" if __HOSTED__ else "B@b", "tenant-b"]
                if not __HOSTED__:
                    denied = (await client.call_tool("same", {"node": {"value": 3}})).structured_content
                    assert not denied["success"] and denied["data"]["reason"] == "custom_tool_schema_changed"
                assert sent == []
                bad = await client.call_tool("execute_custom_tool", {"tool_name": "same", "parameters": {"node": {"value": 3}}})
                assert not json.loads(bad.content[0].text)["success"] and sent == []
                for parameters, expected in [({"node": {"value": "selected"}}, 2), ({"node": {"value": "selected"}, "nullable": None}, None)]:
                    result = await client.call_tool("execute_custom_tool", {"tool_name": "same", "parameters": parameters})
                    assert json.loads(result.content[0].text)["data"] == "selected"
                    assert sent[-1][2]["nullable"] == expected
                    assert sent[-1][1] == ("tenant-b" if __HOSTED__ else None)
                selected[:] = ["A@a", "tenant-a"]
                result = await client.call_tool("execute_custom_tool", {"tool_name": "same", "parameters": {"node": {"value": 8}}})
                assert json.loads(result.content[0].text)["data"] == 8
                assert sent[-1][2]["nullable"] == 1
                if __HOSTED__:
                    selected[1] = "tenant-unknown"
                    missing = await client.call_tool("execute_custom_tool", {"tool_name": "same", "parameters": {"node": {"value": 8}}})
                    assert not json.loads(missing.content[0].text)["success"] and len(sent) == 3
            await PluginHub.shutdown()
        asyncio.run(scenario())
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)).replace("__HOSTED__", repr(hosted)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_polling_validates_only_terminal_data_and_retains_job_without_replay(mode):
    source = """
        import asyncio
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("typed-polling")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            service.register_global_tools([ToolDefinitionModel(name="poll", schema_version=1,
                input_schema={"type": "object"}, output_data_schema={"type": "integer"},
                requires_polling=True, max_poll_seconds=2)])
            async with Client(app, mode=__MODE__) as client:
                for terminal, expected in [(9, True), ("invalid", False), ("x" * (1024 * 1024 + 1), False)]:
                    module.send_with_unity_instance = AsyncMock(side_effect=[
                        {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": .1, "data": {"job_id": "job-known"}},
                        {"success": True, "_mcp_status": "complete", "data": terminal}])
                    result = (await client.call_tool("poll", {})).structured_content
                    assert result["success"] is expected
                    assert module.send_with_unity_instance.await_count == 2
                    assert module.send_with_unity_instance.call_args.args[3] == {"action": "status", "job_id": "job-known"}
                    if expected: assert result["data"] == 9
                    else:
                        assert result["data"]["job_id"] == "job-known"
                        assert result["data"]["retry_safe"] is False
                        assert result["hint"] == "inspect_state_before_retry"
                        assert "invalid" not in result["message"] or "output" in result["message"]
                assert service._active_polls == 0
        asyncio.run(scenario())
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


def test_json_schema_default_and_cache_budgets_are_exact_and_isolated():
    run_sdk_regression("""
        import json
        import models.custom_tool_schema as module
        from models.custom_tool_schema import prepare_schema, bounded_json, SchemaContractError

        def rejects(action):
            try:
                action()
            except SchemaContractError:
                return
            raise AssertionError("Expected bounded contract refusal")

        # Exact JSON bytes/depth/nodes, including repeated shared subtrees and cycles.
        payload = {"x": "a" * 24}
        assert len(bounded_json(payload, max_bytes=32, max_nodes=2).encode()) == 32
        rejects(lambda: bounded_json(payload, max_bytes=31, max_nodes=2))
        rejects(lambda: bounded_json(payload, max_bytes=32, max_nodes=1))
        tree = 0
        for _ in range(32): tree = [tree]
        bounded_json(tree, max_bytes=100, max_nodes=33)
        rejects(lambda: bounded_json([tree], max_bytes=100, max_nodes=34))
        cyclic = []; cyclic.append(cyclic)
        rejects(lambda: bounded_json(cyclic, max_bytes=100, max_nodes=100))
        rejects(lambda: bounded_json(10 ** 5000, max_bytes=256 * 1024, max_nodes=1))
        shared = {"n": 1}
        rejects(lambda: bounded_json([shared, shared], max_bytes=100, max_nodes=4))
        output = prepare_schema({"type": "object"}, {"type": "string"})
        output.validate_output("x" * (1024 * 1024 - 2))
        rejects(lambda: output.validate_output("x" * (1024 * 1024 - 1)))
        rows = prepare_schema({"type": "object", "properties": {"rows": {"type": "array"}}})
        rows.validate_input({"rows": [None] * 4094})
        rejects(lambda: rows.validate_input({"rows": [None] * 4095}))

        # Required-nullable, optional-nonnullable, root-only defaults and detached values.
        schema = {"type": "object", "additionalProperties": False, "properties": {
            "nullable": {"type": ["integer", "null"]},
            "node": {"type": "object", "properties": {"count": {"type": "integer", "default": 9}}, "default": {}, "additionalProperties": False}},
            "required": ["nullable"]}
        prepared = prepare_schema(schema)
        assert prepared.validate_input({"nullable": None}) == {"nullable": None, "node": {}}
        rejects(lambda: prepared.validate_input({}))
        rejects(lambda: prepared.validate_input({"nullable": None, "node": None}))
        applied = prepared.validate_input({"nullable": 1}); applied["node"]["count"] = "changed"
        assert prepared.validate_input({"nullable": 1})["node"] == {}
        bad_default = {"type": "object", "properties": {"node": {"type": "object", "properties": {"count": {"type": "integer", "default": 9}}, "required": ["count"], "default": {}}}}
        rejects(lambda: prepare_schema(bad_default))

        # Combined schema bytes are measured without synthetic wrapper overhead.
        large = {"type": "object", "$comment": "0000", "$defs": {
            "t" + str(i): {"type": "object", "description": ""} for i in range(64)}}
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        remaining = module.MAX_SCHEMA_BYTES - len(canonical(large).encode())
        for body in large["$defs"].values():
            take = min(1024, remaining); body["description"] = "x" * take; remaining -= take
        assert remaining == 0 and len(canonical(large).encode()) == module.MAX_SCHEMA_BYTES
        prepare_schema(large)
        large["$defs"]["t63"]["description"] += "x"
        rejects(lambda: prepare_schema(large))
        large["$defs"]["t63"]["description"] = large["$defs"]["t63"]["description"][:-1]
        module._cache.clear(); module._cache_weight = 0
        for index in range(70):
            large["$comment"] = f"{index:04}"
            prepare_schema(large)
        assert len(module._cache) == 64 and module._cache_weight == module.MAX_CACHE_BYTES
        module._cache.clear(); module._cache_weight = 0
        for index in range(130): prepare_schema({"type": "object", "title": str(index)})
        assert len(module._cache) == module.MAX_CACHE_ENTRIES
        assert module._cache_weight <= module.MAX_CACHE_BYTES
    """)


def test_combined_definition_limit_is_per_tool_not_per_half():
    run_sdk_regression("""
        from models.custom_tool_schema import prepare_schema, SchemaContractError
        input_schema = {"type": "object", "$defs": {"t" + str(i): {"type": "object"} for i in range(128)}}
        output_schema = {"type": "object", "$defs": {"extra": {"type": "object"}}}
        try:
            prepare_schema(input_schema, output_schema)
        except SchemaContractError:
            pass
        else:
            raise AssertionError("Combined129definitions exceeded the128per-tool bound")
    """)


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_rich_schema_is_exact_and_invalid_nested_input_never_dispatches(mode):
    # Given a recursive, typed tool declaration and the actual installed SDK.
    source = """
        import anyio, json
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("rich-schema")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"count": 1}})
            schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object", "additionalProperties": False,
                "properties": {
                    "nodes": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                    "weights": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "mode": {"type": "integer", "enum": [0, 2]},
                    "optional": {"type": ["integer", "null"]},
                },
                "required": ["nodes", "weights", "mode", "optional"],
                "$defs": {"Node": {"type": "object", "additionalProperties": False,
                    "properties": {"count": {"type": "integer", "minimum": 0},
                        "child": {"$ref": "#/$defs/Node"}}, "required": ["count"]}},
            }
            output = {"type": "object", "properties": {"count": {"type": "integer"}},
                "required": ["count"], "additionalProperties": False}
            definition = ToolDefinitionModel(name="rich", schema_version=1,
                input_schema=schema, output_data_schema=output,
                parameters=[{"name": "nodes", "type": "array"}, {"name": "weights", "type": "object"},
                    {"name": "mode", "type": "integer"}, {"name": "optional", "type": "integer"}])
            service.register_global_tools([definition])
            async with Client(app, mode=__MODE__) as client:
                # When the client discovers and calls the real registered component.
                tools = await client.list_tools()
                listed = next(tool for tool in tools if tool.name == "rich")
                assert listed.input_schema == schema
                good = {"nodes": [{"count": 1, "child": {"count": 2}}],
                    "weights": {"a": 3}, "mode": 2, "optional": None}
                bad = [{**good, "nodes": [{"count": "1"}]},
                    {**good, "weights": {"a": True}}, {**good, "mode": 1},
                    {**good, "optional": "1"}, {**good, "nodes": [{"count": -1}]},
                    {k: v for k, v in good.items() if k != "optional"}]
                for arguments in bad:
                    result = await client.call_tool("rich", arguments, raise_on_error=False)
                    assert result.is_error
                # Then all invalid nested inputs are stopped before transport.
                assert module.send_with_unity_instance.await_count == 0
                result = await client.call_tool("rich", good)
                assert result.structured_content["data"] == {"count": 1}
                assert json.loads(result.content[0].text) == result.structured_content
                assert module.send_with_unity_instance.call_args.args[3] == good
                assert module.send_with_unity_instance.await_count == 1
        anyio.run(scenario)
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_rich_output_schema_matches_typed_data_and_nullable_envelopes(mode):
    source = """
        import anyio, json
        from unittest.mock import AsyncMock
        from jsonschema import Draft202012Validator
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("typed-output")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            cases = {
                "object_data": ({"type": "object", "properties": {"description": {"type": "integer"}}, "required": ["description"]}, {"success": True, "data": {"description": 3}}),
                "array_data": ({"type": "array", "items": {"type": "integer"}}, {"success": True, "data": [1, 2]}),
                "recursive_data": ({"$ref": "#/$defs/Node", "$defs": {"Node": {"type": "object", "properties": {"value": {"type": "integer"}, "child": {"$ref": "#/$defs/Node"}}, "required": ["value"]}}}, {"success": True, "data": {"value": 1, "child": {"value": 2}}}),
                "map_data": ({"type": "object", "additionalProperties": {"type": "integer"}}, {"success": True, "data": {"a": 2}}),
                "enum_data": ({"type": "integer", "enum": [0, 2]}, {"success": True, "data": 2}),
                "null_data": ({"type": ["integer", "null"]}, {"success": True, "data": None}),
                "omitted_null": ({"type": ["integer", "null"]}, {"success": True}),
                "failure_data": ({"type": "array", "items": {"type": "integer"}}, {"success": False, "message": "owned failure"}),
                "bad_output": ({"type": "integer"}, {"success": True, "data": "private-invalid-result"}),
            }
            calls = []
            async def send(*args, **kwargs):
                calls.append(args[2])
                return cases[args[2]][1]
            module.send_with_unity_instance = send
            service.register_global_tools([ToolDefinitionModel(name=name, schema_version=1,
                input_schema={"type": "object", "additionalProperties": False}, output_data_schema=schema)
                for name, (schema, _) in cases.items()])
            async with Client(app, mode=__MODE__) as client:
                listed = {tool.name: tool for tool in await client.list_tools()}
                for name, (_, envelope) in cases.items():
                    result = await client.call_tool(name, {})
                    structured = result.structured_content
                    Draft202012Validator(listed[name].output_schema).validate(structured)
                    assert json.loads(result.content[0].text) == structured
                    assert calls.count(name) == 1
                    if name == "bad_output":
                        assert structured["success"] is False
                        assert structured["data"]["reason"] == "custom_tool_output_invalid"
                        assert structured["data"]["retry_safe"] is False
                        assert "private-invalid-result" not in result.content[0].text
                    else:
                        assert structured["success"] == envelope["success"]
                        assert structured["data"] == envelope.get("data")
        anyio.run(scenario)
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("replace", [False, True])
def test_invalid_input_root_keeps_previous_sdk_generation(mode, replace):
    source = """
        import anyio
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("root-schema-atomicity")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"old": True}})
            good = ToolDefinitionModel(name="stable", schema_version=1, input_schema={"type": "object"})
            service.register_global_tools([good])
            old_revision = service.catalog_revision("global", "shared")
            invalid = [{"type": "string"}, {"type": "array"}, {"type": ["object", "null"]},
                {"$ref": "#/$defs/Root", "$defs": {"Root": {"type": "object"}}},
                {"anyOf": [{"type": "object"}, {"type": "null"}]}]
            async with Client(app, mode=__MODE__) as client:
                for root in invalid:
                    bad = ToolDefinitionModel.model_construct(name="invalid", schema_version=1, input_schema=root)
                    try:
                        service.replace_project_catalog("shared", [good, bad], kind="global", replace=__REPLACE__, publish_global=True)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Invalid root was published")
                    assert service.catalog_revision("global", "shared") == old_revision
                    assert [tool.name for tool in await client.list_tools()] == ["stable"]
                    result = await client.call_tool("stable", {})
                    assert result.structured_content["data"] == {"old": True}
                assert module.send_with_unity_instance.await_count == len(invalid)
        anyio.run(scenario)
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)).replace("__REPLACE__", repr(replace)))


def test_profile_rejects_unsupported_constraints_and_invalid_utf8_before_use():
    run_sdk_regression("""
        from models.custom_tool_schema import prepare_schema, SchemaContractError
        from models.models import ToolDefinitionModel
        from unittest.mock import patch
        invalid = [
            {"type": "object", "properties": {"x": {"type": "integer", "minimum": 3, "maximum": 1}}},
            {"type": "object", "properties": {"x": {"type": "number", "exclusiveMinimum": 3, "maximum": 3}}},
            {"type": "object", "properties": {"x": {"type": "array", "minItems": 3, "maxItems": 1}}},
            {"type": "object", "properties": {"x": {"type": "string", "pattern": ".*"}}},
            {"type": "object", "properties": {"x": {"$ref": "https://example.invalid/schema"}}},
            {"type": "object", "properties": {"x": {"$ref": "#/$defs/missing"}}},
            {"type": "object", "properties": {"x": {"$ref": "#/$defs/%61"}},
                "$defs": {"a": {"$ref": "#/$defs/%61"}, "%61": {"type": "object"}}},
            {"type": "object", "$defs": {"%61": {"type": "object"}}},
            {"type": "object", "properties": {"x": {"$ref": "#/$defs/N"}}, "$defs": {
                "N": {"type": "object", "properties": {"next": {"$ref": "#/$defs/N", "properties": {"next": {"$ref": "#/$defs/N"}}}}}}},
            {"type": "object", "properties": {"x": {"anyOf": [{"$ref": "#/$defs/N"}, {"type": "null"}], "properties": {"next": {"$ref": "#/$defs/N"}}}},
                "$defs": {"N": {"type": "object"}}},
            {"type": "object", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"type": "object"}}, "properties": {"x": {"$ref": "#/$defs/a"}}},
            {"type": "object", "properties": {"x": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}},
            {"type": "object", "properties": {"x": {"type": "integer", "default": True}}},
            {"type": "object", "properties": {"x": {"type": "array", "items": {"type": "integer"}, "default": ["wrong"]}}},
            {"type": "object", "$defs": {"bad": {"type": "string", "format": "uri"}}},
            {"type": "object", "description": chr(0xD800)},
        ]
        with patch("socket.create_connection", side_effect=AssertionError("Schema retrieval forbidden")):
            for schema in invalid:
                try:
                    prepare_schema(schema)
                except SchemaContractError:
                    pass
                else:
                    raise AssertionError("Invalid bounded profile was accepted")
            for fields in [{"schema_version": True}, {"schema_version": "1"}, {"schema_version": 2},
                {"input_schema": {"type": "object"}}, {"schema_version": 1},
                {"schema_version": 1, "input_schema": {"type": "object"}, "output_data_schema": {"type": "integer"}, "structured_output": False}]:
                try: ToolDefinitionModel(name="bad", **fields)
                except ValueError: pass
                else: raise AssertionError("Malformed versioned declaration was accepted")
    """)


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_invalid_nested_item_is_blocked_before_transport(mode):
    # Given broad legacy metadata plus the authoritative version-one nested shape.
    source = """
        import anyio
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP, Client
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as module

        async def scenario():
            app = FastMCP("nested-admission")
            service = module.CustomToolService(app)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            module.resolve_project_id_for_unity_instance = lambda _: "shared"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {}})
            service.register_global_tools([ToolDefinitionModel(name="nested", schema_version=1,
                parameters=[{"name": "rows", "type": "array"}], input_schema={
                    "type": "object", "properties": {"rows": {"type": "array", "items": {
                        "type": "object", "properties": {"count": {"type": "integer"}},
                        "required": ["count"], "additionalProperties": False}}},
                    "required": ["rows"], "additionalProperties": False})])
            async with Client(app, mode=__MODE__) as client:
                # When a nested scalar contradicts the declared contract.
                result = await client.call_tool("nested", {"rows": [{"count": "wrong"}]}, raise_on_error=False)
                # Then no native command can be admitted.
                assert module.send_with_unity_instance.await_count == 0
                assert result.is_error
        anyio.run(scenario)
    """
    run_sdk_regression(source.replace("__MODE__", repr(mode)))
