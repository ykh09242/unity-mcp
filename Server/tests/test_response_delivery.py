"""Client-sized responses retain the complete operation result for bounded reads."""

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.tools import ToolResult
from mcp.shared.exceptions import MCPError
from mcp_types import TextContent

from services.response_delivery import ResponseAuthority, ResponseScope, ResponseStore
from services.tools.read_response import read_response
from services.response_delivery import response_store
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.unity_instance_middleware import UnityInstanceMiddleware
from utils.retained_response import assemble_response


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize(
    "text", ["x" * (8 * 1024 * 1024), "한" * (3 * 1024 * 1024)], ids=["ascii", "unicode"]
)
async def test_oversized_mutation_runs_once_and_all_bytes_can_be_read(mode, text):
    server = FastMCP("bounded-delivery")
    server.add_middleware(ResponseLimitMiddleware())
    executions = 0

    @server.tool
    def mutation() -> ToolResult:
        nonlocal executions
        executions += 1
        return ToolResult(content=[TextContent(text=text)])

    server.tool(read_response)
    async with Client(server, mode=mode, cache=False) as client:
        result = await client.call_tool("mutation", {})
        summary = result.structured_content
        assert summary["delivery"]["state"] == "retained"
        assert result.is_error is False
        handle = summary["delivery"]
        offset = 0
        chunks = []
        while offset is not None:
            page = await client.call_tool(
                "read_response", {"response_id": handle["response_id"], "offset": offset}
            )
            data = page.structured_content["data"]
            chunk = base64.b64decode(data["data_base64"], validate=True)
            assert len(chunk) <= 256 * 1024
            chunks.append(chunk)
            offset = data["next_offset"]
        raw = b"".join(chunks)
        assert hashlib.sha256(raw).hexdigest() == handle["sha256"]
        assert json.loads(raw)["content"][0]["text"] == text
        assembled = await assemble_response(
            client.call_tool, handle, max_bytes=16 * 1024 * 1024, max_pages=64, fields=["content"]
        )
        assert assembled["content"][0]["text"] == text
        released = await client.call_tool(
            "read_response", {"response_id": handle["response_id"], "release": True}
        )
        assert released.structured_content["data"]["state"] == "released"
        missing = await client.call_tool(
            "read_response", {"response_id": handle["response_id"]}, raise_on_error=False
        )
        assert missing.is_error
        assert missing.structured_content["error"] == "response_not_found"
    assert executions == 1


def authority(owner="alice", project="project", session="session", grants=("read", "edit")):
    return ResponseAuthority(ResponseScope(owner, project, session), frozenset(grants))


def test_artifacts_are_scope_and_grant_isolated_and_expire_without_sliding():
    clock = [100.0]
    store = ResponseStore(clock=lambda: clock[0])
    original = authority()
    handle = store.put(b"complete", original)
    assert handle is not None
    response_id = handle["response_id"]
    for foreign in (
        authority(owner="bob"),
        authority(project="other"),
        authority(session="other"),
        authority(grants=("read",)),
    ):
        assert store.read(response_id, foreign) is None
        assert store.release(response_id, foreign) is False
    clock[0] = 699.0
    assert store.read(response_id, original)["data_base64"] == "Y29tcGxldGU="
    clock[0] = 700.0
    assert store.read(response_id, original) is None
    assert store.retained_bytes == 0


def test_capacity_does_not_evict_an_existing_result_or_admit_unbounded_pages():
    store = ResponseStore(max_bytes=10)
    first = store.put(b"123456", authority())
    assert store.put(b"abcdef", authority()) is None
    assert store.read(first["response_id"], authority())["total_bytes"] == 6
    with pytest.raises(ValueError, match="offset"):
        store.read(first["response_id"], authority(), offset=7)
    assert store.release(first["response_id"], authority())
    assert store.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("wrapped", [True, False])
async def test_retention_refusal_preserves_outcome_and_receipt_without_retry(
    monkeypatch, success, wrapped
):
    import transport.response_delivery as delivery

    server = FastMCP("cache-full-outcome")
    server.add_middleware(ResponseLimitMiddleware())
    monkeypatch.setattr(delivery, "response_store", lambda _server: ResponseStore(max_bytes=1))
    calls = []
    receipt = {"receipt_id": "a" * 32, "status": "SUCCEEDED" if success else "UNKNOWN"}

    @server.tool
    def mutation() -> ToolResult:
        calls.append(1)
        evidence = {
            "reason": "observed" if success else "outcome_unknown",
            "command_id": "native-id",
        }
        outcome = {"success": success, "status": "done" if success else "unknown", "data": evidence}
        meta = {"io.github.ykh09242.unity-mcp/execution": receipt}
        if wrapped:
            meta["fastmcp"] = {"wrap_result": True}
        return ToolResult(
            content=[TextContent(text="x" * (8 * 1024 * 1024))],
            structured_content={"result": outcome} if wrapped else outcome,
            meta=meta,
        )

    async with Client(server, cache=False) as client:
        result = await client.call_tool("mutation", {}, raise_on_error=False)
    assert calls == [1]
    assert result.is_error is (not success)
    assert result.structured_content["success"] is success
    assert result.structured_content["data"]["command_id"] == "native-id"
    assert result.structured_content["data"]["reason"] == (
        "observed" if success else "outcome_unknown"
    )
    assert result.structured_content["delivery"]["state"] == "unavailable"
    assert result.structured_content["delivery"]["retry_safe"] is False
    assert result.meta["io.github.ykh09242.unity-mcp/execution"] == receipt


@pytest.mark.asyncio
async def test_legacy_handle_cannot_be_read_from_another_session():
    server = FastMCP("isolated-legacy-results")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    def pressure() -> ToolResult:
        return ToolResult(content=[TextContent(text="x" * (8 * 1024 * 1024))])

    server.tool(read_response)
    async with Client(server, mode="legacy", cache=False) as original:
        result = await original.call_tool("pressure", {})
        handle = result.structured_content["delivery"]
        async with Client(server, mode="legacy", cache=False) as foreign:
            denied = await foreign.call_tool(
                "read_response", {"response_id": handle["response_id"]}, raise_on_error=False
            )
            assert denied.structured_content["error"] == "response_not_found"
        page = await original.call_tool("read_response", {"response_id": handle["response_id"]})
        assert page.structured_content["success"] is True
    assert response_store(server).retained_bytes > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_exact_artifact_selector_recovers_after_disconnect_without_routing(mode, monkeypatch):
    from core.config import config
    from services.registry.operation_invocation import wrap_tool
    from transport.response_delivery import capture_response_delivery, retained_response

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    one = "First@0123456789abcdef"
    two = "Second@fedcba9876543210"
    editors = [SimpleNamespace(id=name, hash=name.split("@")[1]) for name in (one, two)]
    middleware = UnityInstanceMiddleware()
    discoveries = []

    async def discover(ctx):
        discoveries.append(tuple(editors))
        return editors

    monkeypatch.setattr(middleware, "_discover_instances", discover)
    server = FastMCP("project-retained-output")
    server.add_middleware(middleware)
    server.tool(name="read_response")(wrap_tool(read_response, "read_response", None))
    original = {"details": "x" * 300_000}
    executions = []

    @server.tool
    async def retain(ctx: Context) -> dict:
        executions.append(await ctx.get_state("unity_instance"))
        return retained_response(original, await capture_response_delivery(ctx))

    @server.tool
    async def select(ctx: Context) -> str:
        selected = await ctx.get_state("unity_instance")
        await middleware.set_active_instance(ctx, selected)
        return selected

    @server.tool
    async def selected(ctx: Context) -> str:
        return await middleware.get_active_instance(ctx) or "none"

    async with Client(server, mode=mode, cache=False) as client:
        if mode == "legacy":
            await client.call_tool("select", {"unity_instance": two})
        result = await client.call_tool("retain", {"unity_instance": one})
        handle = result.structured_content
        response = {"response_id": handle["response_id"]}
        assert executions == [one]
        editors.pop(0)
        before = len(discoveries)
        assert await assemble_response(client.call_tool, handle, unity_instance=one) == original
        for selector in (two, "Incorrect@0123456789abcdef"):
            for release in (False, True):
                refused = await client.call_tool(
                    "read_response",
                    {**response, "unity_instance": selector, "release": release},
                    raise_on_error=False,
                )
                assert refused.structured_content["error"] == "response_not_found"
        if mode == "legacy":
            async with Client(server, mode=mode, cache=False) as foreign:
                refused = await foreign.call_tool(
                    "read_response", {**response, "unity_instance": one}, raise_on_error=False
                )
                assert refused.structured_content["error"] == "response_not_found"
        assert len(discoveries) == before
        assert (await client.call_tool("selected", {})).data == (
            two if mode == "legacy" else "none"
        )
        absent = await client.call_tool("read_response", response, raise_on_error=False)
        assert absent.structured_content["error"] == "response_not_found"
        with pytest.raises(MCPError):
            await client.call_tool("read_response", {**response, "unity_instance": "0123456789a"})
        assert len(discoveries) > before
        editors.clear()
        released = await client.call_tool(
            "read_response", {**response, "unity_instance": "0123456789abcdef", "release": True}
        )
        assert released.structured_content["data"]["state"] == "released"
        assert executions == [one]


@pytest.mark.asyncio
async def test_remote_read_rechecks_original_floor_without_requiring_unrelated_read_grant(
    monkeypatch,
):
    from core.config import config
    from services.credential_lease import (
        CredentialLease,
        CredentialLeaseError,
        bind_credential_lease,
    )
    from services.operation_permissions import (
        OperationGrants,
        Permission,
        authorize_remote_operation,
    )

    monkeypatch.setattr(config, "http_remote_hosted", True)
    server = FastMCP("remote-artifact-floor")
    checked = []

    class LeaseService:
        revoked = False

        async def check_lease(self, lease):
            checked.append(lease.user_id)
            if self.revoked:
                raise CredentialLeaseError()

    class RequestContext:
        fastmcp = server
        request_context = SimpleNamespace(protocol_version="2026-07-28")
        session_id = "per-request-session"

        async def get_state(self, name):
            return {
                "unity_instance": "Fixture@0123456789abcdef",
                "mcpforunity.tool_permission_identity": SimpleNamespace(user_id="alice"),
            }.get(name)

    service = LeaseService()
    grants = OperationGrants(frozenset({Permission.EDIT}), scoped=True)
    lease = CredentialLease(service, object(), "alice", grants)
    owner = ResponseAuthority(
        ResponseScope("remote:alice", "0123456789abcdef", ""), frozenset({"edit"})
    )
    handle = response_store(server).put(b"original mutation result", owner)
    with bind_credential_lease(lease):
        await authorize_remote_operation("read_response", {})
        assert (await read_response(RequestContext(), handle["response_id"]))["success"] is True
    weaker = CredentialLease(
        service, object(), "alice", OperationGrants(frozenset({Permission.READ}), scoped=True)
    )
    with bind_credential_lease(weaker):
        refused = await read_response(RequestContext(), handle["response_id"])
    assert refused == {"success": False, "error": "response_not_found"}
    assert checked == ["alice", "alice", "alice"]

    class Disconnected(RequestContext):
        async def get_state(self, name):
            return None

    ctx = Disconnected()
    lookup = {"unity_instance": "0123456789abcdef"}
    foreign = CredentialLease(service, object(), "other", grants)
    for denied_lease in (weaker, foreign):
        with bind_credential_lease(denied_lease):
            for release in (False, True):
                result = await read_response(ctx, handle["response_id"], release=release, **lookup)
                assert result == refused
    with bind_credential_lease(lease):
        assert (await read_response(ctx, handle["response_id"], **lookup))["success"] is True
        service.revoked = True
        for release in (False, True):
            with pytest.raises(CredentialLeaseError):
                await read_response(ctx, handle["response_id"], release=release, **lookup)
    assert response_store(server).retained_bytes > 0
