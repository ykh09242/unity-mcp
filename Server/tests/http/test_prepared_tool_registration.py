"""Prepared descriptors keep public catalogs and per-application mutations isolated."""

from functools import wraps
import json

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool
from starlette.testclient import TestClient

import main
from core.config import config
from services import tools
from services.tools import run_tests
from tests.http.prepared_tool_registration import prepared_tool_factory


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
