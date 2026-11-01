"""Prepared descriptors keep public catalogs and per-application mutations isolated."""

from functools import wraps
from dataclasses import replace
import inspect
import json
from typing import Annotated, Literal

import anyio
import pytest
from pydantic import Field
from fastmcp import Client, Context, FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from starlette.testclient import TestClient

import main
from core.config import config
from services import tools
from services.tools import run_tests


def test_prepared_registration_keeps_public_apps_and_descriptors_isolated(
    monkeypatch, prepared_tool_factory
) -> None:
    # Given real registration and independently constructed production servers/apps.
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "prepared-registration-token")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    original = main.create_mcp_server(False)

    def fresh():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Tool, "from_function", prepared_tool_factory)
            return main.create_mcp_server(False)

    first, second = fresh(), fresh()
    apps = []
    clients = []
    session_managers = []

    def public_catalog(server):
        app = server.http_app(json_response=True, stateless_http=True)
        apps.append(app)
        with TestClient(app) as client:
            clients.append(client)
            session_managers.append(
                next(route.endpoint for route in app.routes if route.path == "/mcp").session_manager
            )
            headers = {
                "X-Unity-MCP-Token": "prepared-registration-token",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-03-26",
            }
            response = client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 200, response.text
            return response.json()["result"]["tools"]

    baseline = public_catalog(original)
    assert public_catalog(first) == baseline
    assert public_catalog(second) == baseline
    first_tools = anyio.run(first.local_provider.list_tools)
    second_tools = anyio.run(second.local_provider.list_tools)
    assert [
        tool.model_dump(mode="json", exclude={"fn", "return_type"}) for tool in first_tools
    ] == [tool.model_dump(mode="json", exclude={"fn", "return_type"}) for tool in second_tools]
    selected_name = baseline[0]["name"]
    selected = next(tool for tool in first_tools if tool.name == selected_name)
    peer = next(tool for tool in second_tools if tool.name == selected_name)

    # When a delivered descriptor and its server visibility are mutated.
    selected.parameters["clone-only"] = True
    selected.tags.add("clone-only")
    first.disable(tags={"clone-only"}, components={"tool"})

    # Then other apps and the next clone retain the original schema/catalog/state.
    assert selected is not peer and selected.parameters is not peer.parameters
    assert "clone-only" not in peer.parameters and "clone-only" not in peer.tags
    assert selected_name not in {tool["name"] for tool in public_catalog(first)}
    assert public_catalog(second) == baseline
    third = fresh()
    assert public_catalog(third) == baseline
    assert len({id(server.local_provider) for server in (original, first, second, third)}) == 4
    assert len({id(app) for app in apps}) == len(apps)
    assert len({id(client) for client in clients}) == len(clients)
    assert len({id(manager) for manager in session_managers}) == len(session_managers)


def test_prepared_registration_reparses_changed_options_and_new_declarations(
    prepared_tool_factory,
) -> None:
    # Given an original declaration with changed metadata and a different same-name function.
    changed = FastMCP("changed-registration")
    replacement = FastMCP("replacement-registration")

    def run_tests_replacement(new_argument: str) -> str:
        return new_argument

    run_tests_replacement.__name__ = "run_tests"
    # When the registration factory sees each nonmatching declaration/options pair.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        changed.tool(name="run_tests", description="changed descriptor")(run_tests.run_tests)
        replacement.tool(name="run_tests")(run_tests_replacement)
    # Then the real SDK parses their respective metadata and argument schemas.
    changed_tool = anyio.run(changed.get_tool, "run_tests")
    replacement_tool = anyio.run(replacement.get_tool, "run_tests")
    assert changed_tool.description == "changed descriptor"
    assert "mode" in changed_tool.parameters["properties"]
    assert set(replacement_tool.parameters["properties"]) == {"new_argument"}


def test_prepared_registration_reparses_behavior_changing_wrappers(
    monkeypatch, prepared_tool_factory
) -> None:
    # Given the same original declaration and factory metadata with a new logging wrapper.
    def changed_logging(name, type_label):
        def decorate(fn):
            @wraps(fn)
            async def changed(*args, **kwargs):
                return {"changed_wrapper": True}

            return changed

        return decorate

    monkeypatch.setattr(tools, "log_execution", changed_logging)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    # When a fresh server is constructed and the registered tool runs through the SDK.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        server = main.create_mcp_server(False)

    async def invoke():
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("manage_tools", {"action": "list_groups"})

    result = anyio.run(invoke)
    # Then the new wrapper executes instead of a cached wrapper for the old declaration.
    assert json.loads(result.content[0].text) == {"changed_wrapper": True}


def test_prepared_registration_reuses_production_admission_wrappers(monkeypatch):
    from tests.http import prepared_tool_registration as prepared

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn.__name__)
        return original(fn, **kwargs)

    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    assert parsed.count("wait_for_operation") == 1
    assert parsed.count("manage_physics_settings") == 1
    monkeypatch.setattr(Tool, "from_function", factory)
    tools.register_all_tools(FastMCP("production-admission-clones"), project_scoped_tools=False)
    assert parsed.count("wait_for_operation") == 1, (
        "Unchanged production wrappers must reuse schemas"
    )
    assert parsed.count("manage_physics_settings") == 1, (
        "Unchanged operation adapters must reuse schemas"
    )


@pytest.mark.parametrize("location", ["annotation", "default"])
def test_prepared_registration_reuses_patterns_and_reparses_changed_constraints(
    monkeypatch, location
):
    from tests.http import prepared_tool_registration as prepared

    constraint = Field(pattern="^before$")

    def constrained(value: Annotated[str, constraint]) -> str:
        return value

    if location == "default":
        constrained.__annotations__["value"] = str
        constrained.__defaults__ = (constraint,)

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(constrained, "constrained", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    first = FastMCP("first-pattern")
    register(first)
    before = anyio.run(first.get_tool, "constrained")
    assert len(parsed) == 1, "Unchanged field metadata must not rebuild the schema"
    assert before.parameters["properties"]["value"]["pattern"] == "^before$"

    constraint.metadata[0].pattern = "^after$"
    second = FastMCP("changed-pattern")
    register(second)
    after = anyio.run(second.get_tool, "constrained")
    assert len(parsed) == 2, "Changed constraints must use the real SDK parser"
    assert after.parameters["properties"]["value"]["pattern"] == "^after$"
    assert before.parameters["properties"]["value"]["pattern"] == "^before$"


def test_prepared_registration_reparses_unsupported_mutable_signature_defaults(monkeypatch):
    from tests.http import prepared_tool_registration as prepared

    default = ["before"]

    def declared(value: list[str]) -> list[str]:
        return value

    declared.__defaults__ = (default,)

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(declared, "declared", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    first = FastMCP("mutable-default-before")
    register(first)
    before = anyio.run(first.get_tool, "declared")
    assert len(parsed) == 2, "Unsupported mutable defaults must retain the real SDK parser"

    default.append("after")
    second = FastMCP("mutable-default-after")
    register(second)
    after = anyio.run(second.get_tool, "declared")
    assert len(parsed) == 3
    assert before.parameters["properties"]["value"]["default"] == ["before"]
    assert after.parameters["properties"]["value"]["default"] == ["before", "after"]


def test_prepared_registration_reuses_context_injection_and_rejects_marker_state(monkeypatch):
    from tests.http import prepared_tool_registration as prepared

    marker = CurrentContext()

    async def contextual(value: int = 7, ctx: Context = marker) -> int:
        await ctx.set_state("prepared-value", value)
        return await ctx.get_state("prepared-value")

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(contextual, "contextual", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    first = FastMCP("stateless-injection")
    register(first)
    assert len(parsed) == 1
    assert "ctx" not in anyio.run(first.get_tool, "contextual").parameters["properties"]

    marker.fixture_state = []
    second = FastMCP("changed-injection")
    register(second)
    assert len(parsed) == 2, "A context marker with instance state must use the real SDK parser"

    async def invoke():
        for server, value in ((first, 23), (second, 41)):
            async with Client(server, mode="legacy") as client:
                result = await client.call_tool("contextual", {"value": value})
                assert not result.is_error
                assert json.loads(result.content[0].text) == value

    anyio.run(invoke)


def test_prepared_registration_reparses_changed_capability_values(monkeypatch):
    from services.registry.capability_catalog import CAPABILITIES
    from tests.http import prepared_tool_registration as prepared

    capability = CAPABILITIES[0]

    def described() -> str:
        return capability.title

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(described, "described", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    first = FastMCP("original-capability")
    register(first)
    assert len(parsed) == 1

    capability = replace(capability, effects=replace(capability.effects, read_only=True))
    second = FastMCP("changed-capability")
    register(second)
    assert len(parsed) == 2, "Changed nested capability values must use the real SDK parser"

    capability = replace(capability, parameters=["unexpected-mutable-shape"])
    third = FastMCP("mutable-capability")
    register(third)
    assert len(parsed) == 3, "Unexpected mutable capability fields must use the real SDK parser"


@pytest.mark.parametrize("location", ["plain", "field"])
@pytest.mark.parametrize(("before", "after"), [(1, True), (1, 1.0), (0.0, -0.0)])
def test_prepared_registration_reparses_serialized_default_changes(
    monkeypatch, location, before, after
):
    from tests.http import prepared_tool_registration as prepared

    def declared(value: float) -> str:
        return str(value)

    declared.__defaults__ = (Field(default=before) if location == "field" else before,)

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(declared, "declared", None))

    original = Tool.from_function
    monkeypatch.setattr(prepared, "register_all_tools", register)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    declared.__defaults__ = (Field(default=after) if location == "field" else after,)
    server = FastMCP("changed-default-type")
    register(server)
    actual = anyio.run(server.get_tool, "declared")
    fresh = original(tools.wrap_tool(declared, "declared", None))
    assert json.dumps(actual.parameters, sort_keys=True) == json.dumps(
        fresh.parameters, sort_keys=True
    )


def test_prepared_registration_reparses_changed_literal_types(monkeypatch):
    from tests.http import prepared_tool_registration as prepared

    def declared(value: Literal[1]) -> str:
        return str(value)

    def register(server, **_kwargs):
        server.tool(tools.wrap_tool(declared, "declared", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    declared.__annotations__["value"] = Literal[True]
    signature = inspect.signature(declared)
    declared.__signature__ = signature.replace(
        parameters=[signature.parameters["value"].replace(annotation=Literal[True])]
    )
    server = FastMCP("changed-literal-type")
    register(server)
    actual = anyio.run(server.get_tool, "declared")
    fresh = original(tools.wrap_tool(declared, "declared", None))
    assert json.dumps(actual.parameters, sort_keys=True) == json.dumps(
        fresh.parameters, sort_keys=True
    )
    assert len(parsed) == 2, "Different Literal argument types must use the real SDK parser"


@pytest.mark.parametrize("location", ["meta", "annotations"])
@pytest.mark.parametrize(("before", "after"), [(1, True), (1, 1.0), (0.0, -0.0)])
def test_prepared_registration_reparses_serialized_option_changes(
    monkeypatch, location, before, after
):
    from tests.http import prepared_tool_registration as prepared

    def declared(value: int = 7) -> int:
        return value

    def options(value):
        payload = {"nested": [{"value": value}]}
        return {location: ToolAnnotations(**payload) if location == "annotations" else payload}

    selected = before

    def register(server, **_kwargs):
        server.tool(**options(selected))(tools.wrap_tool(declared, "declared", None))

    original = Tool.from_function
    monkeypatch.setattr(prepared, "register_all_tools", register)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    selected = after
    server = FastMCP("changed-option-type")
    register(server)
    actual = anyio.run(server.get_tool, "declared")
    fresh = original(tools.wrap_tool(declared, "declared", None), **options(after))
    assert json.dumps(actual.to_mcp_tool().model_dump(mode="json"), sort_keys=True) == json.dumps(
        fresh.to_mcp_tool().model_dump(mode="json"), sort_keys=True
    )


def test_prepared_registration_reparses_opaque_factory_options(monkeypatch):
    from tests.http import prepared_tool_registration as prepared

    def declared() -> bool:
        return True

    def authorize(_ctx):
        return True

    def register(server, **_kwargs):
        server.tool(auth=authorize)(tools.wrap_tool(declared, "declared", None))

    original = Tool.from_function
    parsed = []

    def parse(fn, **kwargs):
        parsed.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(prepared, "register_all_tools", register)
    monkeypatch.setattr(Tool, "from_function", parse)
    factory = prepared.prepared_tool_factory.__wrapped__()
    monkeypatch.setattr(Tool, "from_function", factory)
    register(FastMCP("opaque-options-first"))
    register(FastMCP("opaque-options-second"))
    assert len(parsed) == 3, "Opaque option callbacks must use the real SDK parser each time"
