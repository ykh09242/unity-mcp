"""Offline Apps contracts. Source proposal: all scenarios are initially UNRUN."""

import copy
import importlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, Context, FastMCP
from fastmcp.server.middleware import Middleware
from mcp.client.extension import ClientExtension
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
import pytest

from core.config import config
from services.operation_permissions import (
    OperationGrants,
    OperationPermissionError,
    Permission,
    authorize_operation,
    authorize_resource,
    authorize_remote_operation,
)
from services.credential_lease import CredentialLeaseError
from services.test_results_app import (
    TEST_RESULTS_URI,
    install_test_results_app,
    project_test_results_tools,
)
from transport.unity_instance_middleware import UnityInstanceMiddleware
from transport.plugin_hub import PluginHub

view = importlib.import_module("services.tools.view_test_results")


def completed():
    """A real native page shape, including Unicode and retained truncation."""
    return {
        "success": True,
        "message": "Test job status retrieved.",
        "data": {
            "job_id": "owned-job",
            "status": "failed",
            "completion_kind": "completed",
            "result_availability": "available",
            "result_truncated_count": 1,
            "result": {
                "mode": "EditMode",
                "summary": {
                    "total": 102,
                    "passed": 0,
                    "failed": 101,
                    "skipped": 1,
                    "durationSeconds": 1.5,
                    "resultState": "Failed",
                },
            },
            "result_page": {
                "items": [
                    {
                        "name": "한글 🌙",
                        "fullName": "Fixture.한글 🌙",
                        "state": "Failed",
                        "durationSeconds": 0.1,
                        "message": "<img src=x onerror=alert(1)> 실패",
                        "stackTrace": "line 1\nline 2",
                        "output": "Unicode preserved",
                        "truncated_fields": ["message"],
                        "original_field_bytes": {"message": 13000},
                    }
                ],
                "total_count": 102,
                "has_more": True,
                "next_cursor": "opaque-generation-1",
                "generation": "generation-1",
            },
        },
    }


@pytest.mark.asyncio
async def test_one_canonical_page_read_preserves_fallback_and_has_no_nudge(monkeypatch):
    # Given a selected Editor, an authenticated caller, and native result data.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(
        view, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    raw = completed()
    send = AsyncMock(return_value=raw)
    monkeypatch.setattr(view.unity_transport, "send_with_unity_instance", send)
    jobs = importlib.import_module("services.tools.run_tests")
    nudge = AsyncMock(side_effect=AssertionError("View must not nudge the desktop"))
    monkeypatch.setattr(jobs, "_update_job_nudge", nudge)
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="owner"))
    # When the read-only view tool is called.
    result = await view.view_test_results(ctx, "owned-job")
    # Then exactly one canonical fixed page read carries the authenticated user.
    send.assert_awaited_once_with(
        view.async_send_command_with_retry,
        "Game@owned",
        "get_test_job",
        {"job_id": "owned-job", "includeFailedTests": True, "page_size": 100},
        user_id="owner",
    )
    nudge.assert_not_awaited()
    assert not result.is_error
    assert result.structured_content["data"]["result_page"] == raw["data"]["result_page"]
    assert json.loads(result.content[0].text) == result.structured_content
    assert "한글 🌙" in result.content[0].text
    assert raw == completed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "success",
        "empty",
        "running",
        "cancelled",
        "unknown",
        "unavailable",
        "malformed",
        "mismatch",
        "incomplete",
        "oversize",
        "bad_cursor",
    ],
)
async def test_only_completed_matching_bounded_pages_are_displayable(monkeypatch, case):
    # Given a native reply, altered at one trust boundary.
    raw = completed()
    data = raw["data"]
    match case:
        case "success":
            data["status"] = "succeeded"
            data["result"]["summary"].update(passed=102, failed=0, skipped=0, resultState="Passed")
            data["result_page"].update(items=[], total_count=0, has_more=False, next_cursor=None)
        case "empty":
            data["status"] = "succeeded"
            data["result"]["summary"].update(
                total=0, passed=0, failed=0, skipped=0, resultState="Passed"
            )
            data["result_page"].update(items=[], total_count=0, has_more=False, next_cursor=None)
        case "running" | "cancelled":
            data["status"] = case
        case "unknown":
            raw = {"success": False, "error": "Unknown job_id."}
        case "unavailable":
            data["result_availability"] = "legacy_unavailable"
        case "malformed":
            data["result_page"]["items"][0]["state"] = 7
        case "mismatch":
            data["job_id"] = "another-job"
        case "incomplete":
            data["completion_kind"] = "interrupted"
        case "oversize":
            data["result_page"]["items"] *= 101
        case "bad_cursor":
            data["result_page"]["has_more"] = False
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(
        view, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    send = AsyncMock(return_value=raw)
    monkeypatch.setattr(view.unity_transport, "send_with_unity_instance", send)
    # When the one-page wrapper parses the reply.
    result = await view.view_test_results(SimpleNamespace(), "owned-job")
    # Then only completed valid pages are successful, and no retry is made.
    assert result.is_error is (case not in {"success", "empty"})
    assert send.await_count == 1
    assert result.content[0].text


@pytest.mark.parametrize("route", ["tool", "resource"])
@pytest.mark.parametrize(
    "permissions,allowed",
    [
        ({Permission.READ}, True),
        ({Permission.TEST}, False),
        ({Permission.EDIT}, False),
        ({Permission.RUN}, False),
        (set(), False),
    ],
)
def test_view_wrapper_and_exact_resource_require_read(permissions, allowed, route):
    # Given a scoped permission set.
    grants = OperationGrants(frozenset(permissions), scoped=True)

    # When either route is independently authorized.
    def check():
        if route == "tool":
            authorize_operation(grants, "view_test_results", {"job_id": "owned-job"})
        else:
            authorize_resource(grants, TEST_RESULTS_URI)

    # Then READ alone permits them; test/edit/run alone cannot.
    if allowed:
        check()
    else:
        with pytest.raises(OperationPermissionError):
            check()


@pytest.mark.parametrize("suffix", ["?x=1", "#fragment", "/", "?", "#", "-other"])
@pytest.mark.parametrize("scoped", [True, False])
def test_near_match_resource_uris_are_denied(suffix, scoped):
    # Given READ authority, including the legacy migration authority.
    grants = OperationGrants(frozenset({Permission.READ}), scoped=scoped)
    # When an alternate resource is requested, then it fails closed.
    with pytest.raises(OperationPermissionError):
        authorize_resource(grants, TEST_RESULTS_URI + suffix)


@pytest.mark.asyncio
async def test_static_resource_read_authenticates_without_selecting_editor(monkeypatch):
    # Given middleware with no selected or reachable Editor.
    middleware = UnityInstanceMiddleware()
    auth = AsyncMock()
    module = importlib.import_module("transport.unity_instance_middleware")
    monkeypatch.setattr(module, "authorize_remote_resource", auth)
    select = AsyncMock(side_effect=AssertionError("Static HTML must not select an Editor"))
    monkeypatch.setattr(middleware, "_inject_unity_instance", select)
    context = SimpleNamespace(message=SimpleNamespace(uri=TEST_RESULTS_URI))
    handler = AsyncMock(return_value="static")
    # When the exact resource is read.
    result = await middleware.on_read_resource(context, handler)
    # Then authorization precedes delivery, and no Editor is consulted.
    assert result == "static"
    auth.assert_awaited_once_with(TEST_RESULTS_URI)
    select.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requester,plugin,tenant",
    [
        (Permission.READ, Permission.READ, "owner"),
        (Permission.TEST, Permission.READ, "owner"),
        (Permission.READ, Permission.TEST, "owner"),
        (Permission.READ, Permission.READ, "another-tenant"),
    ],
)
async def test_selected_plugin_intersection_and_tenant_are_preserved(
    monkeypatch, requester, plugin, tenant
):
    # Given independent requester/plugin grants on a selected Editor session.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    lease = SimpleNamespace(
        user_id="owner", permissions=OperationGrants(frozenset({requester}), scoped=True)
    )
    selected = SimpleNamespace(
        user_id=tenant,
        permissions=OperationGrants(frozenset({plugin}), scoped=True),
        check=AsyncMock(),
    )
    leases = importlib.import_module("services.credential_lease")
    monkeypatch.setattr(leases, "require_credential_lease", AsyncMock(return_value=lease))
    connection = SimpleNamespace()
    monkeypatch.setattr(PluginHub, "_connections", {"selected-editor": connection})
    monkeypatch.setattr(PluginHub, "_socket_lease", staticmethod(lambda socket: selected))
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="selected-editor"))

    # When the public wrapper's complete operation is admitted for that Editor.
    async def admit():
        await authorize_remote_operation("view_test_results", {"job_id": "owned-job"}, ctx=ctx)

    # Then both READ grants and the same tenant are required.
    if requester == plugin == Permission.READ and tenant == "owner":
        await admit()
        selected.check.assert_awaited_once()
    else:
        with pytest.raises((OperationPermissionError, CredentialLeaseError)):
            await admit()


@pytest.mark.asyncio
@pytest.mark.parametrize("context_kind", ["missing", "partial", "unavailable", "sdk_unbound"])
async def test_unavailable_request_context_keeps_fallback_and_registered_metadata(context_kind):
    # Given real descriptors and no negotiated request context, including existing partial callers.
    app = FastMCP("test-results-unavailable-context")

    @app.tool(
        name="view_test_results",
        meta={"ui": {"resourceUri": TEST_RESULTS_URI}, "preserved": "value"},
    )
    def result_view() -> str:
        return "unused"

    @app.tool(name="other_tool", meta={"ui": {"resourceUri": "ui://other/view"}})
    def other_view() -> str:
        return "unused"

    registered = await app.get_tool("view_test_results")
    other = await app.get_tool("other_tool")
    original = copy.deepcopy(registered.meta)
    match context_kind:
        case "missing":
            ctx = None
        case "partial":
            ctx = SimpleNamespace()
        case "unavailable":
            ctx = SimpleNamespace(request_context=None)
        case "sdk_unbound":
            ctx = Context(app)
        case _:
            raise AssertionError("Unknown context fixture")

    # When the Apps descriptor is projected without an active capability-bearing request.
    projected = project_test_results_tools(ctx, [registered, other])
    # Then ordinary fallback is safe, isolated, and leaves all other descriptors alone.
    assert projected[0] is not registered
    assert projected[0].meta == {"preserved": "value"}
    assert registered.meta == original
    assert projected[1] is other
    assert other.meta == {"ui": {"resourceUri": "ui://other/view"}}


class AppsClient(ClientExtension):
    """Advertise the capability through the real public client API."""

    identifier = EXTENSION_ID

    def __init__(self, mime):
        self.mime = mime

    def settings(self):
        return {"mimeTypes": [self.mime]}


class Projection(Middleware):
    async def on_list_tools(self, context, call_next):
        return project_test_results_tools(context.fastmcp_context, await call_next(context))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("mime", [APP_MIME_TYPE, "text/html", None])
@pytest.mark.parametrize("registration", ["production", "sdk"])
async def test_public_modern_and_legacy_capability_metadata_and_fallback(
    monkeypatch, mode, mime, registration
):
    # Given a real server and public client negotiation with explicit MIME support or fallback.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(
        view, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(
        view.unity_transport, "send_with_unity_instance", AsyncMock(return_value=completed())
    )
    app = FastMCP("test-results-app-contract")
    app.add_middleware(Projection())
    install_test_results_app(app)
    if registration == "production":
        from services.registry import get_registered_tools
        import services.tools as tools_package

        definitions = [
            item for item in get_registered_tools() if item["name"] == "view_test_results"
        ]
        assert len(definitions) == 1
        monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
        monkeypatch.setattr(config, "transport_mode", "stdio")
        monkeypatch.setattr(tools_package, "discover_modules", lambda *args, **kwargs: iter(()))
        monkeypatch.setattr(tools_package, "get_registered_tools", lambda: definitions)
        tools_package.register_all_tools(app, project_scoped_tools=False)
    else:
        app.tool(
            name="view_test_results",
            meta={"ui": {"resourceUri": TEST_RESULTS_URI, "visibility": ["model"]}},
        )(view.view_test_results)
    registered = await app.get_tool("view_test_results")
    metadata = copy.deepcopy(registered.meta)
    extensions = [AppsClient(mime)] if mime is not None else []
    # When clients list and invoke the tool using actual SDK surfaces.
    async with Client(app, mode=mode, extensions=extensions) as client:
        tools = await client.list_tools()
        result = await client.call_tool("view_test_results", {"job_id": "owned-job"})
        resources = await client.read_resource(TEST_RESULTS_URI)
    # Then capability affects only listing metadata and fallback data always survives.
    tool = next(tool for tool in tools if tool.name == "view_test_results")
    assert ("ui" in (tool.meta or {})) is (mime == APP_MIME_TYPE)
    assert registered.meta == metadata
    assert result.structured_content["success"] is True
    assert "한글" in result.content[0].text
    assert resources[0].model_dump(by_alias=True)["mimeType"] == APP_MIME_TYPE
    assert resources[0].meta["ui"]["csp"]["connectDomains"] == []
    assert "owned-job" not in resources[0].text


def test_package_declares_static_html():
    # Given source package configuration.
    manifest = Path(__file__).parents[1] / "pyproject.toml"
    import tomllib

    # When packaging metadata is read, then the HTML is an explicit package asset.
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    assert "test_results_view.html" in data["tool"]["setuptools"]["package-data"]["services"]


def run_packaged_app(packets):
    """Run the package's JavaScript; the fixture only supplies host and DOM seams."""
    node = shutil.which("node")
    assert node is not None, "Packaged App lifecycle regressions require the installed Node runtime"
    source = Path(__file__).parents[1] / "src/services/test_results_view.html"
    fixture = Path(__file__).parent / "fixtures/test_results_app_vm.cjs"
    process = subprocess.run(
        [node, str(fixture), str(source)],
        input=json.dumps({"packets": packets}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def app_input(arguments):
    return {"method": "ui/notifications/tool-input", "params": {"arguments": arguments}}


def app_result(job_id="owned-job"):
    envelope = completed()
    envelope["data"]["job_id"] = job_id
    return {"method": "ui/notifications/tool-result", "params": {"structuredContent": envelope}}


@pytest.mark.parametrize(
    "routing",
    [
        {},
        {"unity_instance": "Game@owned"},
        {"unity_instance": 6400},
        {"unity_instance": -1},
        {"unity_instance": None},
    ],
)
def test_packaged_app_accepts_advertised_routing_selectors(routing):
    # Given input containing one of the routing schema's selector forms.
    packets = [app_input({"job_id": "owned-job", **routing}), app_result()]
    # When the actual packaged script receives input and a matching completed result.
    result = run_packaged_app(packets)
    # Then the completed page renders, without initiating any further tool calls.
    assert result["states"][-1]["visible"] is True
    assert result["states"][-1]["rowCount"] == 1
    assert result["states"][-1]["summaryCount"] == 4
    assert all(
        packet.get("method") not in {"tools/call", "resources/read"}
        for packet in result["outbound"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"unity_instance": "Game@owned", "unexpected": True},
        {"job_id": ""},
        {"job_id": None},
        {"job_id": 4},
        {"job_id": "x" * 257},
        {"unity_instance": True},
        {"unity_instance": 1.5},
        {"unity_instance": []},
        {"unity_instance": {}},
    ],
)
def test_packaged_app_rejects_unknown_fields_and_invalid_input_values(extra):
    # Given unknown input, an invalid job identity, or an invalid routing selector.
    packets = [app_input({"job_id": "owned-job", **extra}), app_result()]
    # When the packaged script consumes those host packets.
    state = run_packaged_app(packets)["states"][-1]
    # Then it closes and detaches rather than displaying the following result.
    assert state["visible"] is False
    assert state["listening"] is False
    assert state["rowCount"] == 0


@pytest.mark.parametrize(
    "ending", ["mismatch", "duplicate_input", "duplicate_result", "cancel", "teardown"]
)
def test_packaged_app_retains_job_identity_and_lifecycle_guards_with_routing(ending):
    # Given a routed invocation and the lifecycle event under test.
    packets = [app_input({"job_id": "owned-job", "unity_instance": "Game@owned"})]
    endings = {
        "mismatch": [app_result("another-job")],
        "duplicate_input": [app_input({"job_id": "owned-job", "unity_instance": 6400})],
        "duplicate_result": [app_result(), app_result()],
        "cancel": [app_result(), {"method": "ui/notifications/tool-cancelled", "params": {}}],
        "teardown": [
            app_result(),
            {"id": "teardown", "method": "ui/resource-teardown", "params": {}},
        ],
    }
    packets.extend(endings[ending])
    # When the packaged script executes the complete lifecycle.
    result = run_packaged_app(packets)
    assert result["states"][0]["listening"] is True
    if ending in {"duplicate_result", "cancel", "teardown"}:
        assert result["states"][1]["visible"] is True
    state = result["states"][-1]
    # Then old job data, callbacks and pending timers are discarded.
    assert state == {
        "visible": False,
        "rowCount": 0,
        "summaryCount": 0,
        "listening": False,
        "filterListening": False,
        "timerCount": 0,
        "disconnected": True,
    }
    if ending == "teardown":
        assert {"jsonrpc": "2.0", "id": "teardown", "result": {}} in result["outbound"]
