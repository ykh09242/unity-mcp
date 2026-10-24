"""Inspection consumes actual SDK schemas and existing evidence without dispatch."""

from tests.timing import watchdog_timeout

import copy
import json
from typing import Literal
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
import pytest


TARGET = "Fixture@aaaa1111"


class SdkConnection:
    """Adapt public in-memory SDK operations to the same no-cache helper surface."""

    def __init__(self, server, protocol, trace):
        self.client = Client(server, mode=protocol, cache=False)
        self.trace = trace

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    @property
    def protocol_version(self):
        return self.client.protocol_version

    async def list_tools(self, *, cursor=None, meta=None):
        from mcp_types import PaginatedRequestParams, RequestParamsMeta

        self.trace.append(("tools/list", copy.deepcopy(meta)))
        return await self.client.session.list_tools(
            params=PaginatedRequestParams(
                cursor=cursor, _meta=RequestParamsMeta(**meta) if meta else None
            )
        )

    async def list_resources(self, *, cursor=None, meta=None):
        from mcp_types import PaginatedRequestParams, RequestParamsMeta

        self.trace.append(("resources/list", copy.deepcopy(meta)))
        return await self.client.session.list_resources(
            params=PaginatedRequestParams(
                cursor=cursor, _meta=RequestParamsMeta(**meta) if meta else None
            )
        )

    async def read_resource(self, uri, *, meta=None):
        from mcp_types import RequestParamsMeta

        self.trace.append((uri, copy.deepcopy(meta)))
        return await self.client.session.read_resource(
            uri, meta=RequestParamsMeta(**meta) if meta else None
        )


def capabilities(target=TARGET):
    from models.capabilities import (
        CapabilityConnection,
        CapabilityFeature,
        CapabilityLimits,
        CapabilityTarget,
        ProjectCapabilities,
    )

    value = ProjectCapabilities(
        target=CapabilityTarget(instance_id=target, project_hash="aaaa1111"),
        connection=CapabilityConnection(
            transport="http",
            status="connected",
            instance_id=target,
            generation="opaque-fixture-generation",
        ),
        limits=CapabilityLimits(
            query_timeout_seconds=5,
            query_cleanup_grace_seconds=1,
            query_max_elapsed_seconds=6,
            response_bytes=1048576,
            response_depth=32,
            response_nodes=4096,
        ),
        packages={"inputsystem": False},
        features={
            "input": CapabilityFeature(
                supported=False,
                ready=False,
                source="manage_input/status",
                next_action="configure_input_system_backend",
            )
        },
    )
    return {"success": True, "data": value.model_dump(mode="json")}


def rich_descriptor(name="owned_nested"):
    return {
        "name": name,
        "schema_version": 1,
        "input_schema": {
            "type": "object",
            "properties": {
                "nodes": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                "nullable": {"type": ["string", "null"]},
                "choice": {"type": "integer", "enum": [0, 1], "default": 1},
            },
            "required": ["nodes", "nullable"],
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 1},
                        "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                    },
                    "required": ["count"],
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
        "output_data_schema": {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    }


def install_resources(server, descriptors=(), *, target=TARGET):
    @server.resource("mcpforunity://capabilities")
    async def cap():
        return json.dumps(capabilities(target))

    @server.resource("mcpforunity://custom-tools")
    async def custom():
        return json.dumps(
            {
                "success": True,
                "data": {
                    "project_id": "fixture",
                    "tool_count": len(descriptors),
                    "tools": list(descriptors),
                },
            }
        )

    @server.resource("mcpforunity://tool-groups")
    async def groups():
        return json.dumps(
            {"groups": [{"name": "core", "tools": ["manage_scene"], "default_enabled": True}]}
        )


def install_connection(monkeypatch, server):
    import cli.utils.tool_description as inspection

    trace = []
    monkeypatch.setattr(
        inspection, "_auth_headers", lambda _: {"X-Unity-MCP-Token": "fixture-secret"}
    )
    monkeypatch.setattr(
        inspection,
        "_make_client",
        lambda cfg, protocol, factory, headers: SdkConnection(server, protocol, trace),
    )
    return inspection, trace


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_builtin_exact_advertisement_and_existing_evidence_without_execution(
    monkeypatch, mode
):
    from cli.utils.config import CLIConfig

    server = FastMCP("tool-inspection-builtin")
    calls = []

    @server.tool(name="manage_scene")
    async def scene(action: Literal["get_active"]):
        calls.append(action)
        return {"success": True}

    install_resources(server)
    inspection, trace = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(
        CLIConfig(unity_instance=TARGET), "manage_scene", protocol=mode
    )
    assert receipt.exit_code == 0 and receipt.advertised is True
    assert receipt.schema_source == "sdk_tools_list"
    assert receipt.availability.native_enabled is None
    assert receipt.availability.packages == {"inputsystem": False}
    assert "probuilder" not in receipt.availability.packages
    assert receipt.availability.features["input"]["supported"] is False
    assert receipt.availability.features["input"]["next_action"] == "configure_input_system_backend"
    assert calls == []
    assert all(meta == {"unity_instance": TARGET} for _, meta in trace)
    async with Client(server, mode=mode, cache=False) as client:
        expected = next(item for item in await client.list_tools() if item.name == "manage_scene")
        assert receipt.tool["inputSchema"] == expected.input_schema
        assert receipt.tool.get("outputSchema") == expected.output_schema
        await client.call_tool("manage_scene", {"action": "get_active"})
        refused = await client.call_tool(
            "manage_scene", {"action": "invalid"}, raise_on_error=False
        )
        assert refused.is_error
    assert calls == ["get_active"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_actual_registered_builtin_matches_its_sdk_call_contract(monkeypatch, mode):
    from importlib import import_module
    from cli.utils.config import CLIConfig
    from core.logging_decorator import log_execution
    from core.telemetry_decorator import telemetry_tool
    from services.registry import get_registered_tools

    native = import_module("services.tools.manage_scene")
    info = next(
        item
        for item in get_registered_tools()
        if item["name"] == "manage_scene" and item["func"] is native.manage_scene
    )
    server = FastMCP("actual-builtin")
    wrapped = telemetry_tool(info["name"])(log_execution(info["name"], "Tool")(info["func"]))
    server.tool(name=info["name"], description=info["description"], **info["kwargs"])(wrapped)
    send = AsyncMock(return_value={"success": True, "data": {"name": "OwnedScene"}})
    preflight = AsyncMock(return_value=None)
    monkeypatch.setattr(native, "send_with_unity_instance", send)
    monkeypatch.setattr(native, "get_unity_instance_from_context", AsyncMock(return_value=TARGET))
    monkeypatch.setattr(native, "preflight", preflight)
    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(CLIConfig(), "manage_scene", protocol=mode)
    assert receipt.exit_code == 0 and send.await_count == preflight.await_count == 0
    assert "get_hierarchy" in receipt.tool["inputSchema"]["properties"]["action"]["enum"]
    assert "ctx" not in receipt.tool["inputSchema"]["properties"]
    async with Client(server, mode=mode, cache=False) as client:
        listed = (await client.list_tools())[0]
        assert receipt.tool["inputSchema"] == listed.input_schema
        assert receipt.tool.get("outputSchema") == listed.output_schema
        await client.call_tool("manage_scene", {"action": "get_active"})
        refused = await client.call_tool(
            "manage_scene", {"action": "invalid"}, raise_on_error=False
        )
        assert refused.is_error
    assert send.await_count == preflight.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_rich_custom_exact_refs_defaults_output_and_callable_parity(monkeypatch, mode):
    from cli.utils.config import CLIConfig
    from core.config import config
    from models.models import ToolDefinitionModel
    import services.custom_tool_service as custom

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(custom.CustomToolService, "_instance", None)
    monkeypatch.setattr(custom, "get_unity_instance_from_context", AsyncMock(return_value=TARGET))
    monkeypatch.setattr(custom, "resolve_project_id_for_unity_instance", lambda _: "shared")
    sent = AsyncMock(return_value={"success": True, "data": {"count": 1}})
    monkeypatch.setattr(custom, "send_with_unity_instance", sent)
    server = FastMCP("tool-inspection-rich")
    descriptor = rich_descriptor()
    custom.CustomToolService(server).register_global_tools(
        [ToolDefinitionModel.model_validate(descriptor)]
    )
    install_resources(server, [descriptor])
    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(
        CLIConfig(unity_instance=TARGET), descriptor["name"], protocol=mode
    )
    assert receipt.exit_code == 0
    from tests.tool_schema import native_input_schema

    assert native_input_schema(receipt.tool["inputSchema"]) == descriptor["input_schema"]
    assert "unity_instance" in receipt.tool["inputSchema"]["properties"]
    assert receipt.project_descriptor["input_schema"] == descriptor["input_schema"]
    assert sent.await_count == 0
    async with Client(server, mode=mode, cache=False) as client:
        expected = next(
            item for item in await client.list_tools() if item.name == descriptor["name"]
        )
        assert receipt.tool.get("outputSchema") == expected.output_schema
        result = await client.call_tool(
            descriptor["name"], {"nodes": [{"count": 1}], "nullable": None}
        )
        assert result.structured_content["data"] == {"count": 1}
        assert sent.await_args.args[3]["choice"] == 1
        refused = await client.call_tool(
            descriptor["name"],
            {"nodes": [{"count": "bad"}], "nullable": None},
            raise_on_error=False,
        )
        assert refused.is_error
    assert sent.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("rich", [True, False])
async def test_project_only_schema_is_declared_only_never_a_fabricated_sdk_contract(
    monkeypatch, mode, rich
):
    from cli.utils.config import CLIConfig

    server = FastMCP("hosted-fallback-only")
    descriptor = (
        rich_descriptor("project_only")
        if rich
        else {
            "name": "project_only",
            "parameters": [{"name": "value", "type": "integer", "required": True}],
        }
    )
    calls = []

    @server.tool()
    async def execute_custom_tool(tool_name: str, parameters: dict):
        calls.append((tool_name, parameters))
        return {"success": True}

    install_resources(server, [descriptor])
    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(
        CLIConfig(unity_instance=TARGET), "project_only", protocol=mode
    )
    assert receipt.exit_code == 3 and receipt.advertised is False
    assert receipt.tool is None and receipt.project_descriptor == descriptor
    assert receipt.schema_source == ("project_declared_only" if rich else "unavailable")
    assert receipt.fallback_advertised is True
    assert receipt.availability.native_enabled is None
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_disabled_and_missing_names_do_not_invent_causal_diagnostics(monkeypatch, mode):
    from cli.utils.config import CLIConfig

    server = FastMCP("tool-hidden")

    @server.tool(name="disabled_builtin")
    async def disabled():
        pytest.fail("inspection invoked a hidden tool")

    server.disable(names={"disabled_builtin"}, components={"tool"})
    inspection, trace = install_connection(monkeypatch, server)
    for name in ("disabled_builtin", "missing_probuilder"):
        receipt = await inspection.inspect_tool(
            CLIConfig(unity_instance=TARGET), name, protocol=mode
        )
        assert receipt.exit_code == 3 and receipt.advertised is False
        assert receipt.reason == "not_advertised"
        assert receipt.tool is None and receipt.schema_source == "unavailable"
        assert receipt.availability.native_enabled is None and receipt.availability.packages == {}
        assert "disabled" not in json.dumps(receipt.evidence).lower()
    assert all(method in {"tools/list", "resources/list"} for method, _ in trace)


@pytest.mark.asyncio
async def test_no_explicit_instance_does_not_read_an_arbitrary_project(monkeypatch):
    from cli.utils.config import CLIConfig

    server = FastMCP("server-schema-only")

    @server.tool()
    async def fixture():
        return None

    install_resources(server, [rich_descriptor()])
    inspection, trace = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 0 and receipt.scope == "sdk_session"
    assert receipt.project_descriptor is None and receipt.availability.packages == {}
    assert trace == [("tools/list", None)]


@pytest.mark.asyncio
async def test_wrong_target_discards_project_evidence_but_keeps_actual_schema(monkeypatch):
    from cli.utils.config import CLIConfig

    server = FastMCP("wrong-project")

    @server.tool()
    async def fixture():
        return None

    install_resources(server, [rich_descriptor("fixture")], target="Other@bbbb2222")
    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), "fixture")
    assert receipt.exit_code == 0 and receipt.advertised is True and receipt.partial
    assert receipt.project_descriptor is None and receipt.availability.packages == {}
    assert receipt.evidence["capabilities"]["reason"] == "wrong_target"
    assert receipt.selection_verified is False and receipt.groups == []
    assert receipt.evidence["custom_tools"]["status"] == "unknown"
    assert receipt.evidence["tool_groups"]["status"] == "unknown"


def test_http_budget_factory_is_reused_without_doctor_state_machine():
    from cli.utils.doctor_connection import DoctorHttpFactory
    import cli.utils.tool_description as inspection

    assert inspection.DoctorHttpFactory is DoctorHttpFactory


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,legacy_only", [("auto", False), ("legacy", False), ("auto", True)])
async def test_real_http_sdk_lifetime_preserves_auth_selection_and_schema(
    monkeypatch, mode, legacy_only
):
    from fastmcp import Context
    from fastmcp.server.middleware import Middleware
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection
    from tests.test_cli_doctor import live_mcp

    server = FastMCP("wire-tool-inspection")
    observed = []
    calls = []

    class SelectionProbe(Middleware):
        async def on_list_tools(self, context, call_next):
            observed.append(context.fastmcp_context.request_context.meta)
            return await call_next(context)

        async def on_list_resources(self, context, call_next):
            observed.append(context.fastmcp_context.request_context.meta)
            return await call_next(context)

        async def on_read_resource(self, context, call_next):
            observed.append(context.fastmcp_context.request_context.meta)
            return await call_next(context)

    server.add_middleware(SelectionProbe())

    @server.tool()
    async def fixture(ctx: Context, count: int = 1):
        calls.append(count)
        return count

    install_resources(server)
    monkeypatch.setattr(
        inspection, "_auth_headers", lambda _: {"X-Unity-MCP-Token": "fixture-secret"}
    )
    trace = []
    async with live_mcp(server, trace, legacy_only, required_token="fixture-secret") as port:
        receipt = await inspection.inspect_tool(
            CLIConfig(port=port, unity_instance=TARGET), "fixture", protocol=mode
        )
    assert receipt.exit_code == 0, receipt.model_dump_json()
    assert receipt.tool["inputSchema"]["properties"]["count"]["default"] == 1
    assert observed and all(meta.get("unity_instance") == TARGET for meta in observed)
    assert "tools/list" in trace and "resources/read" in trace
    assert "tools/call" not in trace and calls == []
    assert "fixture-secret" not in receipt.model_dump_json()
    assert receipt.observed_protocol == (
        "2026-07-28" if mode == "auto" and not legacy_only else "2025-11-25"
    )


class ScriptedConnection:
    """No-network faults retain the actual SDK's public result shapes."""

    protocol_version = "2026-07-28"

    def __init__(self, tools=(), *, repeat_cursor=False, block=False):
        self.tools = tools
        self.repeat_cursor = repeat_cursor
        self.block = block
        self.calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def list_tools(self, **_):
        import anyio
        from mcp_types import ListToolsResult, Tool

        self.calls += 1
        if self.block:
            await anyio.sleep_forever()
        return ListToolsResult(
            tools=[Tool(**tool) for tool in self.tools],
            nextCursor="repeated" if self.repeat_cursor else None,
        )


@pytest.mark.asyncio
async def test_incomplete_cursor_listing_never_becomes_complete_not_found(monkeypatch):
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    connection = ScriptedConnection(repeat_cursor=True)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "missing")
    assert receipt.exit_code == 3 and receipt.advertised is None
    assert receipt.reason == "listing_incomplete" and receipt.partial
    assert connection.calls == 2 and connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["repeat", "pages", "deadline"])
async def test_early_match_is_not_authoritative_after_incomplete_listing(monkeypatch, ending):
    import anyio
    from mcp_types import ListToolsResult, Tool
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    class Incomplete(ScriptedConnection):
        async def list_tools(self, **_):
            self.calls += 1
            if ending == "deadline" and self.calls > 1:
                await anyio.sleep_forever()
            return ListToolsResult(
                tools=[Tool(name="fixture", inputSchema={})] if self.calls == 1 else [],
                nextCursor="repeat" if ending != "pages" else f"page-{self.calls}",
            )

    connection = Incomplete()
    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 3 and receipt.advertised is None
    assert receipt.tool is None and receipt.schema_source == "unavailable"
    assert receipt.reason == ("deadline" if ending == "deadline" else "listing_incomplete")
    assert receipt.partial and connection.closed


@pytest.mark.asyncio
async def test_oversized_selected_schema_is_not_rendered_or_validated(monkeypatch):
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    secret = "untrusted-schema-marker"
    connection = ScriptedConnection(
        [{"name": "fixture", "inputSchema": {"type": "object", "description": secret * 15000}}]
    )
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 3 and receipt.reason == "inspection_limit"
    assert receipt.tool is None and secret not in receipt.model_dump_json()
    assert connection.closed


@pytest.mark.asyncio
async def test_single_read_deadline_closes_without_later_operations(monkeypatch):
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    connection = ScriptedConnection(block=True)
    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 3 and receipt.reason == "deadline"
    assert receipt.advertised is None and connection.calls == 1 and connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_origin", [None, 31.999999999999996])
async def test_connection_entry_cannot_spend_the_cleanup_grace_on_reads(monkeypatch, clock_origin):
    import anyio
    from contextlib import contextmanager
    from types import SimpleNamespace
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    scopes = []
    budgets = []
    clock = anyio.current_time() if clock_origin is None else clock_origin

    @contextmanager
    def capture_scope(delay):
        with anyio.fail_after(delay) as scope:
            scopes.append((delay, scope))
            try:
                yield scope
            finally:
                scopes.pop()

    async def local_auth(function, *args, **_kwargs):
        return function(*args)

    class EnterBlocked(ScriptedConnection):
        entered = False

        async def __aenter__(self):
            self.entered = True
            # Verify the budget at the actual blocked boundary, independent of
            # runner scheduling. Advancing this scope causes the real cancellation.
            delay, scope = scopes[-1]
            budgets.append((delay, scopes[-2][0]))
            scope.deadline = anyio.current_time()
            await anyio.sleep_forever()

    connection = EnterBlocked()
    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(inspection, "_CLEANUP_GRACE_SECONDS", 0.3)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    monkeypatch.setattr(
        inspection,
        "anyio",
        SimpleNamespace(
            current_time=lambda: clock,
            fail_after=capture_scope,
            to_thread=SimpleNamespace(run_sync=local_auth),
        ),
    )
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.reason == "deadline" and connection.calls == 0 and connection.entered
    assert len(budgets) == 1
    assert budgets[0] == pytest.approx((30.0, 30.3))


@pytest.mark.asyncio
async def test_auth_failure_is_static_and_never_opens_sdk(monkeypatch):
    from cli.utils.config import CLIConfig
    from cli.utils.connection import UnityConnectionError
    import cli.utils.tool_description as inspection

    def auth(_):
        raise UnityConnectionError("untrusted-token-secret")

    monkeypatch.setattr(inspection, "_auth_headers", auth)
    monkeypatch.setattr(
        inspection, "_make_client", lambda *_: pytest.fail("auth failure opened SDK")
    )
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 1 and receipt.reason == "auth_required"
    assert "untrusted-token-secret" not in receipt.model_dump_json()


@pytest.mark.asyncio
async def test_conflicting_duplicate_advertisement_never_chooses_a_callable_schema(monkeypatch):
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    connection = ScriptedConnection(
        [
            {
                "name": "fixture",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "integer"}}},
            },
            {
                "name": "fixture",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
            },
        ]
    )
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 3 and receipt.reason == "listing_incomplete"
    assert receipt.tool is None and receipt.advertised is None


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["depth", "nodes", "tools", "pages"])
async def test_bounded_public_inspection_refuses_without_schema_evaluation(monkeypatch, limit):
    from mcp_types import ListToolsResult
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    class Pages(ScriptedConnection):
        async def list_tools(self, **_):
            self.calls += 1
            return ListToolsResult(tools=[], nextCursor=f"page-{self.calls}")

    schema = {"type": "object"}
    if limit == "depth":
        for _ in range(65):
            schema = {"properties": {"child": schema}}
    if limit == "nodes":
        schema = {"enum": [0] * 33000}
    entries = [{"name": "fixture", "inputSchema": schema}]
    if limit == "tools":
        entries = [{"name": f"tool_{index}", "inputSchema": {}} for index in range(513)]
    connection = Pages() if limit == "pages" else ScriptedConnection(entries)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.exit_code == 3
    assert receipt.reason == (
        "listing_incomplete" if limit in {"tools", "pages"} else "inspection_limit"
    )
    assert receipt.tool is None and connection.closed
    assert connection.calls == (8 if limit == "pages" else 1)


@pytest.mark.asyncio
async def test_stalled_cleanup_is_separately_bounded_and_reported(monkeypatch):
    import anyio
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    class CloseBlocked(ScriptedConnection):
        async def __aexit__(self, *_):
            self.closed = True
            await anyio.sleep_forever()

    connection = CloseBlocked([{"name": "fixture", "inputSchema": {}}])
    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_CLEANUP_GRACE_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    with anyio.fail_after(watchdog_timeout()):
        receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert receipt.tool is not None and receipt.partial and receipt.cleanup_complete is False
    assert receipt.evidence["cleanup"]["reason"] == "cleanup_incomplete" and connection.closed


@pytest.mark.asyncio
async def test_fast_read_cannot_lend_remaining_read_budget_to_cleanup(monkeypatch):
    import anyio
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    close_started = None

    class CloseBlocked(ScriptedConnection):
        async def __aexit__(self, *_):
            nonlocal close_started
            self.closed = True
            close_started = anyio.current_time()
            await anyio.sleep_forever()

    connection = CloseBlocked([{"name": "fixture", "inputSchema": {}}])
    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(inspection, "_CLEANUP_GRACE_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_auth_headers", lambda _: {})
    monkeypatch.setattr(inspection, "_make_client", lambda *_: connection)
    receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
    assert close_started is not None and anyio.current_time() - close_started < 0.2
    assert receipt.tool is not None and receipt.cleanup_complete is False and receipt.partial
    assert receipt.evidence["cleanup"]["reason"] == "cleanup_incomplete"


@pytest.mark.asyncio
async def test_expired_auth_worker_never_starts_sdk_later(monkeypatch):
    import threading
    import anyio
    from cli.utils.config import CLIConfig
    import cli.utils.tool_description as inspection

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def auth(_):
        entered.set()
        release.wait(watchdog_timeout())
        finished.set()
        return {}

    monkeypatch.setattr(inspection, "_READ_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(inspection, "_auth_headers", auth)
    monkeypatch.setattr(
        inspection, "_make_client", lambda *_: pytest.fail("expired auth started SDK")
    )
    try:
        receipt = await inspection.inspect_tool(CLIConfig(), "fixture")
        assert entered.is_set() and receipt.reason == "deadline"
        assert receipt.residual_auth_io == "unknown" and receipt.advertised is None
    finally:
        release.set()
        await anyio.to_thread.run_sync(finished.wait, watchdog_timeout())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_same_name_project_fallback_evidence_follows_each_request_selection(
    monkeypatch, mode
):
    from fastmcp import Context
    from cli.utils.config import CLIConfig

    server = FastMCP("selected-projects")

    @server.tool()
    async def execute_custom_tool(tool_name: str, parameters: dict):
        pytest.fail("inspection executed the fallback")

    def selection(ctx):
        selector = ctx.request_context.meta["unity_instance"]
        return ("A@aaaa1111", 0) if selector in {"A@aaaa1111", "aaaa1111"} else ("B@bbbb2222", 1)

    @server.resource("mcpforunity://capabilities")
    async def cap(ctx: Context):
        return json.dumps(capabilities(selection(ctx)[0]))

    @server.resource("mcpforunity://custom-tools")
    async def catalog(ctx: Context):
        descriptor = rich_descriptor("same_name")
        descriptor["input_schema"]["properties"]["choice"]["default"] = selection(ctx)[1]
        return json.dumps({"success": True, "data": {"tools": [descriptor]}})

    inspection, _ = install_connection(monkeypatch, server)
    for selector, expected in (("A@aaaa1111", 0), ("B@bbbb2222", 1), ("aaaa1111", 0)):
        receipt = await inspection.inspect_tool(
            CLIConfig(unity_instance=selector), "same_name", protocol=mode
        )
        assert receipt.exit_code == 3 and receipt.schema_source == "project_declared_only"
        assert receipt.selection_verified is True and receipt.tool is None
        assert (
            receipt.project_descriptor["input_schema"]["properties"]["choice"]["default"]
            == expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["capabilities", "custom_tools", "tool_groups"])
async def test_malformed_optional_evidence_stays_unknown(monkeypatch, source):
    from cli.utils.config import CLIConfig

    server = FastMCP("malformed-evidence")

    @server.tool()
    async def fixture():
        return None

    install_resources(server)
    if source == "capabilities":
        raw = capabilities()
        raw["data"]["packages"]["inputsystem"] = "false"
    else:
        raw = {"success": True, "data": {}} if source == "custom_tools" else {}
    uri = {
        "capabilities": "mcpforunity://capabilities",
        "custom_tools": "mcpforunity://custom-tools",
        "tool_groups": "mcpforunity://tool-groups",
    }[source]

    @server.resource(uri)
    async def malformed():
        return json.dumps(raw)

    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), "fixture")
    assert receipt.exit_code == 0 and receipt.advertised is True and receipt.partial
    assert receipt.evidence[source]["status"] == "unknown"
    assert receipt.evidence[source]["reason"] == "invalid_source_response"
    if source == "capabilities":
        assert receipt.availability.packages == {} and receipt.selection_verified is None


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", ["null_ids", "missing_connection_id", "unavailable"])
async def test_explicit_unavailable_selection_clears_parallel_project_attribution(
    monkeypatch, contradiction
):
    from cli.utils.config import CLIConfig

    server = FastMCP("contradictory-selection")

    @server.tool()
    async def manage_scene():
        return None

    install_resources(server, [rich_descriptor("manage_scene")])
    raw = capabilities()
    if contradiction == "null_ids":
        raw["data"]["target"]["instance_id"] = None
        raw["data"]["connection"]["instance_id"] = None
    elif contradiction == "missing_connection_id":
        raw["data"]["connection"]["instance_id"] = None
    else:
        raw["data"]["connection"]["status"] = "unavailable"
        raw["data"]["connection"]["generation"] = None

    @server.resource("mcpforunity://capabilities")
    async def contradictory():
        return json.dumps(raw)

    inspection, _ = install_connection(monkeypatch, server)
    receipt = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), "manage_scene")
    assert receipt.exit_code == 0 and receipt.tool is not None and receipt.partial
    assert receipt.selection_verified is False
    assert receipt.project_descriptor is None and receipt.groups == []
    assert receipt.availability.packages == {} and receipt.availability.features == {}
    assert receipt.evidence["capabilities"]["reason"] == "connection_unavailable"
    assert receipt.evidence["custom_tools"]["status"] == "unknown"
    assert receipt.evidence["tool_groups"]["status"] == "unknown"
