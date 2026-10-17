"""Guarded local REST commands use a real registry/hub and inert native wire peers."""

import asyncio
import json
from importlib import import_module

import anyio
import httpx
import pytest
import pytest_asyncio
from starlette.websockets import WebSocket

from tests.http.prepared_tool_registration import prepared_tool_factory
from tests.http.test_plugin_session_targeting_and_eviction import hub_environment

A_HASH = "a" * 16
B_HASH = "b" * 16
TOKEN = "owned-project-context-auth"


class Peer:
    """Only downstream native I/O is fake; server selection/admission remain real."""

    def __init__(self, hub, root, project_hash=A_HASH, reply=None):
        self.hub, self.root, self.project_hash = (
            hub,
            str(root) if root is not None else None,
            project_hash,
        )
        self.reply = reply or {"success": True, "data": {"observed": True}}
        self.executes = []
        self.before_reply = None
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait({"type": "websocket.connect"})
        scope = {"type": "websocket", "path": "/owned-context-peer", "headers": []}
        self.websocket = WebSocket(scope, self.incoming.get, self.send)
        self.endpoint = hub(scope, self.incoming.get, self.send)

    async def register(self):
        await self.websocket.accept()
        await self.endpoint.on_receive(
            self.websocket,
            {
                "type": "register",
                "project_name": "Same",
                "project_hash": self.project_hash,
                "project_path": self.root,
                "unity_version": "6000.0",
            },
        )
        return self

    async def send(self, message):
        if message["type"] != "websocket.send":
            return
        payload = json.loads(message["text"])
        if payload.get("type") == "execute":
            self.executes.append(payload)
            if self.before_reply is not None:
                await self.before_reply()
            await self.endpoint.on_receive(
                self.websocket,
                {
                    "type": "command_result",
                    "id": payload["id"],
                    "result": self.reply,
                },
            )


@pytest_asyncio.fixture
async def environment(hub_environment, prepared_tool_factory, monkeypatch, tmp_path):
    # Given: disposable app config and real hub state with no application sockets.
    main = import_module("main")
    from fastmcp.tools.base import Tool
    from core.config import config

    hub, registry = hub_environment
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", TOKEN)
    monkeypatch.setattr(
        main, "resolve_project_id_for_unity_instance", lambda value: value.rsplit("@", 1)[-1]
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        app = main.create_mcp_server(False).http_app()
    hub.configure(registry)
    a = await Peer(hub, tmp_path / "parent-a/Same").register()
    b = await Peer(hub, tmp_path / "parent-b/Same", B_HASH).register()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://owned",
            headers={"X-Unity-MCP-Token": TOKEN},
        ) as client:
            yield hub, registry, a, b, client
    finally:
        await hub.shutdown()


def payload(root, target=A_HASH, command="owned_mutation", params=None):
    return {
        "type": command,
        "params": params or {},
        "unity_instance": target,
        "expected_project": {"root": str(root)},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [A_HASH, "Same@" + A_HASH])
async def test_guarded_same_name_projects_route_only_exact_expected_root(environment, target):
    # Given: two same-name peers on the same actual app endpoint.
    _, _, a, b, client = environment
    # When: a context-bound command selects A's full hash/root.
    result = await client.post("/api/context-command", json=payload(a.root, target))
    # Then: the actual hub sends exactly one frame to A and none to B.
    assert result.status_code == 200, result.text
    assert result.json()["success"] is True
    assert len(a.executes) == 1 and b.executes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,root_source", [(B_HASH, "a"), ("c" * 16, "a"), ("Same", "a"), (A_HASH, "b")]
)
async def test_mismatch_has_no_native_frame_pending_reservation_or_epoch(
    environment, target, root_source
):
    # Given: an explicit target/root mismatch or unavailable exact target.
    hub, _, a, b, client = environment
    root = a.root if root_source == "a" else b.root
    before = {id(p): getattr(p.websocket.state, "plugin_command_epoch", 0) for p in (a, b)}
    # When: dispatch is attempted at the real guarded endpoint.
    result = await client.post("/api/context-command", json=payload(root, target))
    # Then: refusal describes only a logical operation with no prior dispatch.
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert result.json()["success"] is False and data["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == [] and hub._pending == {}
    assert before == {id(p): getattr(p.websocket.state, "plugin_command_epoch", 0) for p in (a, b)}
    assert a.root not in result.text and b.root not in result.text and TOKEN not in result.text


@pytest.mark.asyncio
async def test_explicit_override_b_is_verified_before_mutation(environment):
    # Given: both independent override values identify B.
    _, _, a, b, client = environment
    # When: using B's full target and expected root.
    result = await client.post("/api/context-command", json=payload(b.root, B_HASH))
    # Then: only B executes.
    assert result.status_code == 200, result.text
    assert result.json()["success"] is True
    assert a.executes == [] and len(b.executes) == 1


@pytest.mark.asyncio
async def test_port_reuse_or_hash_collision_never_substitutes_new_project(environment, tmp_path):
    # Given: a replacement peer claims A's hash but reports another root.
    hub, _, a, b, client = environment
    replacement = await Peer(hub, tmp_path / "other/Same", A_HASH).register()
    # When: a stale A context sends to the same server endpoint.
    result = await client.post("/api/context-command", json=payload(a.root))
    # Then: even exact-hash reuse cannot authorize the different project.
    assert result.status_code == 200, result.text
    assert result.json()["success"] is False
    assert result.json()["data"]["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == replacement.executes == []


@pytest.mark.asyncio
async def test_replacement_between_capture_and_admission_is_not_retargeted(
    environment, monkeypatch
):
    # Given: a reconnect is gated exactly before the actual builtin send.
    hub, _, a, b, client = environment
    original = hub.send_command
    replacement = None

    async def replace_then_send(*args, **kwargs):
        nonlocal replacement
        replacement = await Peer(hub, a.root, A_HASH).register()
        return await original(*args, **kwargs)

    monkeypatch.setattr(hub, "send_command", replace_then_send)
    # When: expected root/hash still match, but the captured socket has changed.
    result = await client.post("/api/context-command", json=payload(a.root))
    # Then: there is no native send or reload jump to the new generation.
    assert result.status_code == 200, result.text
    assert result.json()["data"]["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == replacement.executes == []


@pytest.mark.asyncio
async def test_custom_guard_uses_pinned_branch_before_all_resolver_readiness_work(
    environment, monkeypatch
):
    # Given: a real custom service dispatch with all ordinary selection paths forbidden.
    hub, _, a, b, client = environment
    from services.custom_tool_service import CustomToolService
    from models.models import ToolDefinitionModel

    service = CustomToolService.get_instance()
    service._project_tools[A_HASH] = {"owned_custom": ToolDefinitionModel(name="owned_custom")}

    async def forbidden(*args, **kwargs):
        raise AssertionError("Guarded dispatch entered resolver/recovery/readiness")

    for name in (
        "_resolve_session_id",
        "_ensure_live_connection",
        "_read_identity",
        "_readiness_probe",
    ):
        monkeypatch.setattr(hub, name, forbidden)
    # When: the actual local custom route executes through the common transport.
    result = await client.post(
        "/api/context-command",
        json=payload(a.root, command="execute_custom_tool", params={"tool_name": "owned_custom"}),
    )
    # Then: captured-session admission succeeds without those helpers.
    assert result.status_code == 200, result.text
    assert result.json()["success"] is True
    assert [p["name"] for p in a.executes] == ["owned_custom"] and b.executes == []


@pytest.mark.asyncio
async def test_custom_pending_then_refused_poll_retains_prior_dispatch_and_job(
    environment, monkeypatch
):
    # Given: actual custom polling has received a pending reply and pauses before its poll.
    hub, _, a, b, client = environment
    from services.custom_tool_service import CustomToolService
    from models.models import ToolDefinitionModel

    service = CustomToolService.get_instance()
    service._project_tools[A_HASH] = {
        "owned_custom": ToolDefinitionModel(
            name="owned_custom", requires_polling=True, max_poll_seconds=3
        )
    }
    a.reply = {
        "success": True,
        "_mcp_status": "pending",
        "_mcp_poll_interval": 0.1,
        "data": {"job_id": "owned-job-18"},
    }
    arrived, release = anyio.Event(), anyio.Event()
    original = service._send_custom
    calls = 0

    async def observed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            arrived.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "_send_custom", observed)
    responses = []

    async def request():
        responses.append(
            await client.post(
                "/api/context-command",
                json=payload(
                    a.root, command="execute_custom_tool", params={"tool_name": "owned_custom"}
                ),
            )
        )

    # When: replacing the connection before the poll reaches actual admission.
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(request)
            try:
                await arrived.wait()
                replacement = await Peer(hub, a.root, A_HASH).register()
            finally:
                release.set()
    # Then: prior mutation evidence survives; the replacement receives no poll.
    response = responses[0]
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is False and result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["job_id"] == "owned-job-18"
    assert result["data"].get("dispatch") != "not_dispatched"
    assert result["data"]["command_id"] == a.executes[0]["id"]
    assert len(a.executes) == 1 and replacement.executes == b.executes == [] and calls == 2
    assert hub._pending == {}


@pytest.mark.asyncio
async def test_valid_pending_reply_is_not_retroactively_invalidated(environment, monkeypatch):
    # Given: a legitimate native pending receipt from the captured generation.
    hub, _, a, _, client = environment
    expected = {"success": True, "data": {"status": "pending", "job_id": "retained-native-job"}}
    a.reply = expected
    # When: completing the admitted RPC without a later local guard refusal.
    response = await client.post("/api/context-command", json=payload(a.root))
    # Then: its pending data is preserved without claiming operation completion.
    assert response.status_code == 200, response.text
    assert response.json() == expected


@pytest.mark.asyncio
async def test_guard_context_cleanup_allows_next_unscoped_command(environment):
    # Given: a refused guarded request in the same server process.
    _, _, a, b, client = environment
    refused = await client.post("/api/context-command", json=payload(a.root, B_HASH))
    assert refused.status_code == 200, refused.text
    assert refused.json()["success"] is False
    # When: a subsequent legacy command explicitly targets B without a context.
    response = await client.post(
        "/api/command", json={"type": "owned_mutation", "params": {}, "unity_instance": B_HASH}
    )
    # Then: no stale guard/marker leaks across requests.
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert a.executes == [] and len(b.executes) == 1


@pytest.mark.asyncio
async def test_cancelled_pre_admission_request_does_not_leak_guard_or_send(
    environment, monkeypatch
):
    # Given: a real request is event-gated before its first admission.
    hub, _, a, b, client = environment
    arrived, release = anyio.Event(), anyio.Event()
    original = hub.send_command

    async def paused(*args, **kwargs):
        arrived.set()
        await release.wait()
        return await original(*args, **kwargs)

    async def request():
        await client.post("/api/context-command", json=payload(a.root))

    monkeypatch.setattr(hub, "send_command", paused)
    # When: cancelling the caller while it owns that gated request.
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(request)
            try:
                await arrived.wait()
                group.cancel_scope.cancel()
            finally:
                release.set()
    monkeypatch.setattr(hub, "send_command", original)
    # Then: no mutation was sent; a later unscoped request retains old behavior.
    assert a.executes == b.executes == [] and hub._pending == {}
    result = await client.post(
        "/api/command", json={"type": "owned_mutation", "params": {}, "unity_instance": B_HASH}
    )
    assert result.json()["success"] is True
    assert len(b.executes) == 1 and a.executes == []


@pytest.mark.asyncio
async def test_missing_registered_root_is_unverifiable_not_hash_only_success(environment):
    # Given: the current same-hash registration has no native root metadata.
    hub, _, a, b, client = environment
    replacement = await Peer(hub, None, A_HASH).register()
    # When: an otherwise exact full target requests A's expected root.
    response = await client.post("/api/context-command", json=payload(a.root))
    # Then: unavailable identity refuses before reservation/send instead of guessing.
    assert response.status_code == 200, response.text
    assert response.json()["success"] is False
    assert response.json()["data"]["reason"] == "project_context_unavailable"
    assert response.json()["data"]["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == replacement.executes == [] and hub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("after_dispatch", [False, True])
async def test_total_deadline_retains_actual_execution_evidence(
    environment, monkeypatch, after_dispatch
):
    # Given: the actual request scope can expire at an exact observed phase.
    hub, _, a, b, client = environment
    from transport.plugin_hub import http_request_budget

    reached, cancelled = [], []

    async def expire_scope():
        reached.append(True)
        http_request_budget.get().scope.reschedule(asyncio.get_running_loop().time())
        try:
            await anyio.Event().wait()
        finally:
            cancelled.append(True)

    if after_dispatch:
        a.before_reply = expire_scope
    else:

        async def before_admission(*args, **kwargs):
            await expire_scope()

        monkeypatch.setattr(hub, "send_command", before_admission)
    # When: the owned deadline expires before admission or after a real execute frame.
    with anyio.fail_after(3):
        response = await client.post("/api/context-command", json=payload(a.root))
    # Then: only prior execution permits outcome_unknown; all owned work is drained.
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is False and reached == cancelled == [True]
    assert len(a.executes) == int(after_dispatch) and b.executes == [] and hub._pending == {}
    if after_dispatch:
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["hint"] == "inspect_state_before_retry"
        assert result["data"]["command_id"] == a.executes[0]["id"]
    else:
        assert result["data"]["reason"] == "command_deadline_exceeded"
        assert result["data"].get("command_id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("override_expected", [False, True])
async def test_actual_click_to_asgi_verifies_override_before_native_wire(
    environment, monkeypatch, override_expected
):
    # Given: real CLI files identify A; same-name peer B shares this server endpoint.
    _, _, a, b, client = environment
    from pathlib import Path
    from click.testing import CliRunner
    from cli.main import cli
    from cli.utils import config as cli_config, connection

    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    for peer in (a, b):
        path = Path(peer.root)
        (path / "Assets").mkdir(parents=True)
        (path / "ProjectSettings").mkdir()
        (path / "ProjectSettings/ProjectVersion.txt").write_text("6000.0", encoding="utf-8")
    (Path(a.root) / ".unity-mcp.json").write_text(
        json.dumps({"schema_version": "unity-mcp/cli-context@1", "instance": A_HASH}),
        encoding="utf-8",
    )
    monkeypatch.chdir(a.root)
    monkeypatch.setattr(cli_config, "_config", None)
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {"X-Unity-MCP-Token": TOKEN})
    concrete_client = httpx.AsyncClient

    async def forward(request):
        response = await client.post(
            request.url.path, content=request.content, headers=request.headers
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=response.headers,
            request=request,
        )

    def relay(request):
        return anyio.from_thread.run(forward, request)

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **kw: concrete_client(transport=httpx.MockTransport(relay), **kw),
    )
    args = ["--instance", B_HASH, "--format", "json"]
    if override_expected:
        args += ["--expect-project", b.root]
    args += ["raw", "owned_mutation", "{}"]
    # When: actual Click executes through real connection decoding and the actual ASGI route/hub.
    result = await anyio.to_thread.run_sync(lambda: CliRunner().invoke(cli, args))
    # Then: an override needs both B's target and expected root before any B frame.
    assert a.executes == []
    if override_expected:
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["success"] is True and len(b.executes) == 1
    else:
        assert result.exit_code == 1, (result.output, len(b.executes))
        assert json.loads(result.stdout)["data"]["dispatch"] == "not_dispatched"
        assert b.executes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_dispatch", [False, True])
@pytest.mark.parametrize("change", ["replacement", "catalog"])
async def test_registered_catalog_refusal_preserves_actual_dispatch_evidence(
    environment, monkeypatch, prior_dispatch, change
):
    # Given: the real register_tools path publishes a generation-bound catalog.
    hub, _, a, b, client = environment
    from services.custom_tool_service import CustomToolService, _CatalogAdmission

    service = CustomToolService.get_instance()
    monkeypatch.setattr(hub, "_mcp", service._mcp)
    rows = [{"name": "owned_custom", "requires_polling": True, "max_poll_seconds": 3}]

    async def publish(peer, descriptors):
        await peer.endpoint.on_receive(
            peer.websocket, {"type": "register_tools", "tools": descriptors}
        )

    await publish(a, rows)
    assert A_HASH not in service._project_tools
    catalog = service._definition_catalog(A_HASH, "owned_custom", None)
    assert catalog is not None and catalog.generation == a.websocket.state.plugin_session_id
    a.reply = {
        "success": True,
        "_mcp_status": "pending",
        "_mcp_poll_interval": 0.1,
        "data": {"job_id": "registered-job-18"},
    }
    original = service._send_custom
    replacement = None
    calls = 0

    async def changed(guard, *args, **kwargs):
        nonlocal calls, replacement
        calls += 1
        assert isinstance(guard, _CatalogAdmission) and guard.catalog is catalog
        if calls == (2 if prior_dispatch else 1):
            if change == "replacement":
                replacement = await Peer(hub, a.root, A_HASH).register()
                await publish(replacement, rows)
            else:
                await publish(a, [{**rows[0], "description": "new registered revision"}])
            assert not guard.current()
        return await original(guard, *args, **kwargs)

    monkeypatch.setattr(service, "_send_custom", changed)
    # When: a real catalog refuses either the first send or a later job poll.
    with anyio.fail_after(5):
        response = await client.post(
            "/api/context-command",
            json=payload(
                a.root, command="execute_custom_tool", params={"tool_name": "owned_custom"}
            ),
        )
    # Then: only an earlier native send makes the logical outcome uncertain.
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is False and result["hint"] == "inspect_state_before_retry"
    data = result["data"]
    if prior_dispatch:
        assert data["reason"] == "outcome_unknown"
        assert data["cause"] == "custom_tool_catalog_changed"
        assert data["job_id"] == "registered-job-18"
        assert data["command_id"] == a.executes[0]["id"]
        assert data["session_id"] == a.websocket.state.plugin_session_id
        assert data.get("dispatched") is not False and data.get("dispatch") != "not_dispatched"
    else:
        assert data == {"reason": "custom_tool_catalog_changed", "dispatched": False}
    assert len(a.executes) == int(prior_dispatch) and calls == (2 if prior_dispatch else 1)
    assert b.executes == [] and (replacement is None or replacement.executes == [])
    assert hub._pending == {}
    # The route-local context is reset even after a terminal catalog refusal.
    later = await client.post(
        "/api/command", json={"type": "owned_query", "params": {}, "unity_instance": B_HASH}
    )
    assert later.json()["success"] is True and len(b.executes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {"success": True, "data": {"status": "pending", "job_id": "registered-pending"}},
        {"success": True, "data": {"status": "completed", "owned_result": 18}},
        {
            "success": False,
            "error": "Owned native domain failure",
            "hint": "inspect_state_before_retry",
            "data": {"reason": "custom_tool_catalog_changed", "dispatched": True},
        },
    ],
)
async def test_registered_catalog_native_results_remain_unchanged(
    environment, monkeypatch, receipt
):
    # Given: a real registered tool and a legitimate native response from its admitted generation.
    hub, _, a, b, client = environment
    from services.custom_tool_service import CustomToolService

    service = CustomToolService.get_instance()
    monkeypatch.setattr(hub, "_mcp", service._mcp)
    await a.endpoint.on_receive(
        a.websocket, {"type": "register_tools", "tools": [{"name": "owned_custom"}]}
    )
    assert service._definition_catalog(A_HASH, "owned_custom", None) is not None
    a.reply = receipt
    # When: the registered tool returns without a terminal local catalog refusal.
    response = await client.post(
        "/api/context-command",
        json=payload(a.root, command="execute_custom_tool", params={"tool_name": "owned_custom"}),
    )
    # Then: the project fence does not rewrite pending, completed, or domain outcomes.
    assert response.status_code == 200, response.text
    unguarded = await client.post(
        "/api/command",
        json={
            "type": "execute_custom_tool",
            "params": {"tool_name": "owned_custom"},
            "unity_instance": A_HASH,
        },
    )
    assert unguarded.status_code == 200, unguarded.text
    assert response.json() == unguarded.json()
    assert all(response.json()[key] == value for key, value in receipt.items())
    assert len(a.executes) == 2 and b.executes == [] and hub._pending == {}
