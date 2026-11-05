"""Operational actions preserve scope, authored state and unfinished work."""

from tests.lifecycle import FixtureProgress
from tests.timing import await_signal, watchdog_timeout
from types import SimpleNamespace
import asyncio
import json
import threading
import time

import pytest
import pytest_asyncio

from core.config import config
from services.reference_graph.service import ReferenceGraphService, native_send
from services.reference_graph.store import Edge, RevisionConflict
from services.reference_graph.http import handle
from services.credential_lease import CredentialLease, CredentialLeaseError, bind_credential_lease
from services.operation_permissions import OperationGrants, OperationPermissionError, Permission
from services.registry.operation_invocation import wrap_tool
from services.tools.manage_reference_graph import manage_reference_graph
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.local_auth_middleware import LocalControlAuthMiddleware
from transport.models import CommandResultMessage
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE, RemoteControlAuthMiddleware
from starlette.applications import Starlette
from starlette.routing import Route
import httpx


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["impact", "snapshot"])
async def test_cleanup_reclaims_obsolete_scratch_but_preserves_resumable_work(setup, kind):
    from services.reference_graph.operations import cleanup
    from services.reference_graph.store import GraphStore

    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace = entry.workspace
        store = workspace.store
        for index in range(10):
            store.put_declaration(str(index), "a", Edge(str(index), "manual"), expected_version=0)
        queries = workspace.queries()
        ready = queries.snapshot(name="ready", limit=100)["snapshot"]["id"]
        arguments = (
            {"node": "a", "direction": "outgoing"} if kind == "impact" else {"name": "partial"}
        )
        obsolete = getattr(queries, kind)(**arguments, limit=2)
        other = GraphStore.open(service.root, store.scope)
        other.put_declaration("change", "a", Edge("extra", "manual"), expected_version=0)
        with pytest.raises(RevisionConflict):
            getattr(queries, kind)(**arguments, limit=2, cursor=obsolete["next_cursor"])
        active = getattr(queries, kind)(**arguments, limit=2)
        totals = 0
        for _ in range(30):
            result = cleanup(workspace, before=time.time() + 1, limit=1)
            assert result["query_scratch_rows_removed"] <= 1
            totals += result["query_scratch_rows_removed"]
            if not result["more"]:
                break
        assert totals > 0
        assert not result["more"]
        with store._connect() as db:
            assert not db.execute(
                "SELECT 1 FROM graph_queries WHERE id=?", (obsolete["query_id"],)
            ).fetchone()
            assert not db.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=?", (obsolete["query_id"],)
            ).fetchone()
            assert not db.execute(
                "SELECT 1 FROM graph_snapshot_edges WHERE snapshot=?", (obsolete["query_id"],)
            ).fetchone()
            assert db.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=? AND state='ready'", (ready,)
            ).fetchone()
        assert getattr(queries, kind)(**arguments, limit=2, cursor=active["next_cursor"])
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["impact", "snapshot"])
@pytest.mark.parametrize("failure_call", [1, 2])
@pytest.mark.parametrize("page_limit", [1, 100])
@pytest.mark.parametrize("cleanup_before_failure", [False, True])
async def test_cleanup_reclaims_undelivered_first_page_after_validation_failure(
    setup, monkeypatch, kind, failure_call, page_limit, cleanup_before_failure
):
    from services.reference_graph.operations import cleanup

    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace = entry.workspace
        for index in range(4):
            workspace.store.put_declaration(
                str(index), "a", Edge(str(index), "manual"), expected_version=0
            )
        entry.watching = True
        calls = 0
        validate = service._validate_exploration

        def reject(workspace, result):
            nonlocal calls
            calls += 1
            if calls == failure_call:
                if cleanup_before_failure:
                    cleanup(workspace, before=time.time() + 1, limit=100)
                raise PermissionError("source access revoked")
            return validate(workspace, result)

        monkeypatch.setattr(service, "_validate_exploration", reject)
        arguments = (
            {"node": "a", "direction": "outgoing"} if kind == "impact" else {"name": "partial"}
        )
        with pytest.raises(PermissionError, match="source access revoked"):
            await service.execute(ctx, kind, {**arguments, "limit": page_limit})
        for _ in range(20):
            result = cleanup(workspace, before=time.time() + 1, limit=1)
            assert result["query_scratch_rows_removed"] <= 1
            if not result["more"]:
                break
        with workspace.store._connect() as db:
            for table in (
                "graph_queries",
                "graph_query_nodes",
                "graph_query_queue",
                "graph_snapshots",
                "graph_snapshot_edges",
                "graph_snapshot_refs",
                "graph_snapshot_versions",
                "graph_snapshot_contents",
            ):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        await service.aclose()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)

    async def send(instance, params):
        result = {
            "instance_id": instance,
            "project_id": "project",
            "epoch": "epoch",
            "revision": 0,
            "ignore_state": "available",
            "ignore_revision": 0,
        }
        if params["action"] == "ignore_rules":
            result.update(snapshot="ignore", files=[], next_offset=None)
        elif params["action"] == "inventory":
            result.update(snapshot="inventory", assets=[], next_offset=None)
        elif params["action"] == "changes":
            result.update(requires_census=False, changes=[], next_after=0, has_more=False)
        return result

    service = ReferenceGraphService(tmp_path / "graphs", send=send)
    monkeypatch.setattr(service, "start", lambda *_, **__: None)

    class Context:
        fastmcp = SimpleNamespace(_reference_graph_service=service)
        instance = "one"

        async def get_state(self, key):
            return self.instance if key == "unity_instance" else None

    return service, Context()


@pytest.mark.asyncio
async def test_config_pages_cas_reauthorization_and_isolation(setup, tmp_path, monkeypatch):
    service, ctx = setup
    try:
        for index in range(3):
            await service.execute(
                ctx,
                "import_config",
                {
                    "kind": "rule",
                    "id": str(index),
                    "expected_version": 0,
                    "config": {"call": f"Loader{index}.Load", "argument": 0, "scheme": "resources"},
                },
            )
        page = (await service.execute(ctx, "export_config", {"kind": "rule", "limit": 2}))["data"]
        assert len(page["items"]) == 2 and page["next_after"] == "1"
        rest = (
            await service.execute(
                ctx, "export_config", {"kind": "rule", "after": page["next_after"], "limit": 2}
            )
        )["data"]
        assert [row["id"] for row in rest["items"]] == ["2"]
        assert rest["next_after"] is None
        with pytest.raises(RevisionConflict):
            await service.execute(
                ctx,
                "import_config",
                {"kind": "rule", "id": "0", "expected_version": 0, "config": {"enabled": False}},
            )
        root = tmp_path / "external"
        root.mkdir()
        source = {
            "kind": "source",
            "id": "settings",
            "expected_version": 0,
            "config": {"root": str(root), "include": ["*.json"]},
        }
        with pytest.raises(ValueError, match="reauthoriz"):
            await service.execute(ctx, "import_config", source)
        monkeypatch.delenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", raising=False)
        with pytest.raises(PermissionError):
            await service.execute(ctx, "import_config", {**source, "reauthorize": True})
        monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", json.dumps([str(root)]))
        await service.execute(ctx, "import_config", {**source, "reauthorize": True})
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_source(
            "external:legacy",
            {"root": str(root), "include": ["*.json"], "api_key": "never-export"},
            expected_version=0,
            enabled=False,
        )
        exported = (await service.execute(ctx, "export_config", {"kind": "source"}))["data"]
        assert "never-export" not in json.dumps(exported["items"])
        ctx.instance = "two"
        assert (await service.execute(ctx, "export_config", {"kind": "rule"}))["data"][
            "items"
        ] == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cleanup_preserves_active_jobs_and_authored_history(setup):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace = entry.workspace
        store = workspace.store
        store.put_declaration("kept", "a", Edge("b", "manual"), expected_version=0)
        queries = workspace.queries()
        active = queries.impact("b", limit=1)
        finished = queries.impact("missing", limit=1)
        snapshot = queries.snapshot(name="keep", limit=1)
        before = time.time() + 1
        with store._connect(write=True) as db:
            db.execute("UPDATE graph_queries SET finished=0 WHERE id=?", (active["query_id"],))
            db.execute(
                "INSERT INTO graph_staging VALUES (?,?,?,?,?,?,?)",
                ("abandoned", "owner", 1, "id", "kind", "{}", 0),
            )
            db.execute("INSERT INTO graph_worker VALUES (1,?,?)", ("live", time.time() + 100))
            db.execute(
                "INSERT INTO graph_staging VALUES (?,?,?,?,?,?,?)",
                ("live", "owner", 1, "id", "kind", "{}", 0),
            )
        await entry.engine._gate.acquire()
        try:
            result = (await service.execute(ctx, "cleanup", {"before": before, "limit": 1}))["data"]
            assert result["memory_caches_cleared"] is False
        finally:
            entry.engine._gate.release()
        assert result["completed_queries_removed"] == 1
        with store._connect() as db:
            assert db.execute(
                "SELECT 1 FROM graph_queries WHERE id=?", (active["query_id"],)
            ).fetchone()
            assert not db.execute(
                "SELECT 1 FROM graph_queries WHERE id=?", (finished["query_id"],)
            ).fetchone()
            assert (
                db.execute("SELECT count(*) FROM graph_staging WHERE worker='live'").fetchone()[0]
                == 1
            )
            assert db.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=?", (snapshot["snapshot"]["id"],)
            ).fetchone()
        assert store.declarations()[0]["id"] == "kept"
        overview = (await service.execute(ctx, "overview", {}))["data"]
        assert overview["identity"]["instance_id"] == "one"
        assert overview["storage"]["allocated_bytes"] > 0
        assert overview["memory"]["measurement"] == "cache_presence_only"
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retry_scan",
    [None, {"through": 100, "after": [99, "z"]}, {"through": 100, "ready": True}],
)
async def test_retry_preserves_failure_eligibility_and_recovery_invalidation(setup, retry_scan):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace = entry.workspace
        assert workspace.acquire("worker")
        asset = {
            "guid": "a" * 32,
            "path": "Assets/Fixture.prefab",
            "kind": "asset",
            "source_format": "none",
            "keys": [],
        }
        healthy = {**asset, "guid": "b" * 32, "path": "Assets/Healthy.prefab"}
        workspace.record_page("worker", [asset, healthy], {}, "scan")
        workspace.clean_asset("worker", healthy["guid"])
        workspace.fail_asset("worker", asset, ValueError("temporary collection failure"))
        assert workspace.dirty_assets() == []
        assert workspace.counts()["dirty_assets"] == 0
        if retry_scan is not None:
            with workspace.store._connect(write=True) as db:
                workspace._set(db, "retry_scan", retry_scan)

        await service.execute(ctx, "retry", {})
        assert [item["guid"] for item in workspace.dirty_assets()] == [asset["guid"]]
        assert workspace.counts()["dirty_assets"] == 1
        assert workspace.counts()["failed_assets"] == 1
        assert workspace.state("retry_scan") == retry_scan
        with workspace.store._connect(write=True) as db:
            assert tuple(
                db.execute("SELECT attempts,retry_at FROM graph_asset_failures").fetchone()
            ) == (1, 0)
            db.execute("DELETE FROM graph_changed_inputs")
        workspace.clean_asset("worker", asset["guid"])
        assert workspace.counts()["failed_assets"] == 0
        with workspace.store._connect() as db:
            assert db.execute(
                "SELECT 1 FROM graph_changed_inputs WHERE owner=?", ("asset:" + asset["guid"],)
            ).fetchone()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_editor_fence_before_database_creation(setup):
    service, ctx = setup
    ctx.reference_graph_expected_scope = ("other", "project")
    try:
        with pytest.raises(RevisionConflict):
            await service.execute(ctx, "overview", {})
        assert not service.entries
        assert not service.root.exists()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_large_completed_scratch_is_resumable_and_configuration_survives_restart(setup):
    service, ctx = setup
    try:
        await service.execute(
            ctx,
            "import_config",
            {"kind": "rule", "id": "keep", "expected_version": 0, "config": {"enabled": False}},
        )
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        query = entry.workspace.queries().impact("missing")
        with entry.workspace.store._connect(write=True) as db:
            db.executemany(
                "INSERT INTO graph_query_nodes VALUES (?,?,0,NULL)",
                ((query["query_id"], str(index)) for index in range(10001)),
            )
        total = removed = 0
        for _ in range(12):
            data = (
                await service.execute(ctx, "cleanup", {"before": time.time() + 1, "limit": 1000})
            )["data"]
            assert data["query_scratch_rows_removed"] <= 1000
            total += data["query_scratch_rows_removed"]
            removed += data["completed_queries_removed"]
            if not data["more"]:
                break
        assert total >= 10001 and removed == 1 and not data["more"]
        path = service.root
        await service.aclose()
        from services.reference_graph.store import GraphStore
        from services.reference_graph.workspace import GraphWorkspace

        reopened = GraphWorkspace(GraphStore.open(path, entry.workspace.store.scope))
        assert reopened.store.rules()[0]["config"]["enabled"] is False
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_http_local_auth_and_editor_fence(setup, monkeypatch):
    service, _ = setup

    async def route(request):
        return await handle(request, service)

    app = LocalControlAuthMiddleware(
        Starlette(routes=[Route("/api/reference-graph", route, methods=["POST"])]),
        token="owned-test-token",
    )
    body = {"action": "overview", "instance": "one", "editor_id": "one", "project_id": "project"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            assert (await client.post("/api/reference-graph", json=body)).status_code == 401
            headers = {"X-Unity-MCP-Token": "owned-test-token"}
            assert (
                await client.post(
                    "/api/reference-graph", json=body, headers={**headers, "Origin": "null"}
                )
            ).status_code == 403
            response = await client.post("/api/reference-graph", json=body, headers=headers)
            assert response.status_code == 200
            assert response.json()["data"]["identity"]["instance_id"] == "one"
            assert (
                await client.post(
                    "/api/reference-graph", json={**body, "editor_id": "wrong"}, headers=headers
                )
            ).status_code == 409
            assert (
                await client.post(
                    "/api/reference-graph", json={**body, "user": "other"}, headers=headers
                )
            ).status_code == 400

            async def denied_source(*_, **__):
                raise PermissionError("private-folder credential-detail")

            monkeypatch.setattr(service, "execute", denied_source)
            denied = await client.post("/api/reference-graph", json=body, headers=headers)
            assert denied.status_code == 403
            assert denied.json()["error"] == "source_access_unavailable"
            assert "private-folder" not in denied.text and "credential-detail" not in denied.text
    finally:
        await service.aclose()


async def select_plugin(command_hub, lease):
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "one", "6000", user_id=lease.user_id)
    command_hub.websocket.state.user_id = lease.user_id
    setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, lease)


@pytest.mark.asyncio
async def test_remote_http_principal_permissions_and_expiry(setup, command_hub, monkeypatch):
    service, _ = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)

    class Authority:
        revoked = False

        def lease_retired(self, _):
            return self.revoked

        def check_lease_now(self, _):
            if self.revoked:
                from services.credential_lease import CredentialLeaseError

                raise CredentialLeaseError()

        async def check_lease(self, lease):
            self.check_lease_now(lease)

    authority = Authority()
    # Read-only scope cannot use import or maintenance even through the UI facade.
    from services.operation_permissions import LEGACY_GRANTS

    read = OperationGrants(frozenset({Permission.READ}), scoped=True)
    lease = CredentialLease(authority, object(), "alice", read)
    payload = {"action": "overview", "instance": "one", "editor_id": "one", "project_id": "project"}

    class Request:
        state = SimpleNamespace()

        async def json(self):
            return payload

    request = Request()
    setattr(request.state, AUTHENTICATED_LEASE_STATE, lease)
    await select_plugin(command_hub, lease)
    try:
        first = await handle(request, service)
        assert first.status_code == 200
        first_scope = json.loads(first.body)["data"]["scope"]
        for action, options in (
            ("query", {"node": "missing"}),
            ("explain", {"id": "missing"}),
            ("impact", {"node": "missing"}),
            ("path", {"node": "missing", "target": "target"}),
        ):
            payload.update(action=action, options=options)
            assert (await handle(request, service)).status_code == 200
        payload["action"] = "cleanup"
        payload["options"] = {"before": time.time()}
        assert (await handle(request, service)).status_code == 403
        payload["action"] = "overview"
        payload["options"] = {}
        bob = CredentialLease(authority, object(), "bob", LEGACY_GRANTS)
        setattr(request.state, AUTHENTICATED_LEASE_STATE, bob)
        await select_plugin(command_hub, bob)
        second = await handle(request, service)
        assert second.status_code == 200
        assert json.loads(second.body)["data"]["scope"] != first_scope
        authority.revoked = True
        assert (await handle(request, service)).status_code == 403
    finally:
        await service.aclose()


@pytest_asyncio.fixture
async def hosted_graph(setup, command_hub, monkeypatch):
    service, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    editor_reply = service.send
    service.send = native_send

    async def reply(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            data = await editor_reply("one", message["params"])
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
            )

    command_hub.websocket.send_json = reply

    async def route(request):
        return await handle(request, service)

    url = "/api/reference-graph"
    app = RemoteControlAuthMiddleware(Starlette(routes=[Route(url, route, methods=["POST"])]))
    claims = {
        key: {"unity_mcp_permissions": grants}
        for key, grants in (
            ("read", ["read"]),
            ("write", ["read", "edit"]),
            ("plugin", ["read", "edit"]),
        )
    }
    try:
        async with (
            permission_service(monkeypatch, claims) as auth,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://hosted"
            ) as client,
        ):
            leases = {key: (await auth.service.validate(key)).lease for key in claims}
            await select_plugin(command_hub, leases["plugin"])

            async def post(action, options, key="write", editor_id="one"):
                body = dict(
                    action=action,
                    options=options,
                    instance="one",
                    editor_id=editor_id,
                    project_id="project",
                )
                return await client.post(url, json=body, headers={"X-API-Key": key})

            tool = wrap_tool(
                manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
            )

            async def invoke(action, options, key="write"):
                with bind_credential_lease(leases[key]):
                    return await tool(ctx, action, options)

            yield SimpleNamespace(
                post=post, invoke=invoke, leases=leases, hub=command_hub, service=service
            )
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["requester", "plugin", None])
async def test_hosted_rule_import_requires_both_grants_on_rest_and_mcp(hosted_graph, restricted):
    graph = hosted_graph
    options = {
        "kind": "rule",
        "id": "existing",
        "config": {"enabled": False},
        "expected_version": 0,
    }
    assert (await graph.post("import_config", options, editor_id="wrong")).status_code == 409
    assert not graph.service.entries
    assert (await graph.post("import_config", options)).status_code == 200
    store = next(iter(graph.service.entries.values())).workspace.store
    original = store.rules_state()
    if restricted == "plugin":
        setattr(graph.hub.websocket.state, AUTHENTICATED_LEASE_STATE, graph.leases["read"])
    key = "read" if restricted == "requester" else "write"
    assert (await graph.post("export_config", {"kind": "rule"}, key)).status_code == 200
    for option in ({**options, "id": "new"}, {**options, "expected_version": 1}):
        graph.hub.sent.clear()
        response = await graph.post("import_config", option, key=key)
        if restricted:
            assert response.status_code == 403
            assert response.json()["error"] == "authorization_unavailable"
            with pytest.raises(OperationPermissionError):
                await graph.invoke("import_config", option, key)
            assert not graph.hub.sent
            assert store.rules_state() == original
        else:
            assert response.status_code == 200
            assert response.json()["data"]["version"] == option["expected_version"] + 1
            assert [cmd["params"]["action"] for cmd in graph.hub.sent] == ["status"]
            updated = {**option, "expected_version": response.json()["data"]["version"]}
            assert (await graph.invoke("import_config", updated))["data"]["version"] == updated[
                "expected_version"
            ] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "import_config",
        "set_rule",
        "set_source",
        "set_policy",
        "rebuild",
        "declare",
        "remove_declaration",
        "cleanup",
    ],
)
async def test_hosted_mcp_edit_without_initial_target_refuses_later_read_only_socket(
    hosted_graph, monkeypatch, action
):
    from services.registry import operation_invocation

    graph = hosted_graph
    await graph.hub.registry.unregister("session")
    capture = operation_invocation.capture_target

    async def reconnect_after_capture(*args, **kwargs):
        initial = await capture(*args, **kwargs)
        assert initial is None
        await select_plugin(graph.hub, graph.leases["read"])
        return initial

    monkeypatch.setattr(operation_invocation, "capture_target", reconnect_after_capture)
    options = {"kind": "rule", "id": "new", "config": {"enabled": False}, "expected_version": 0}
    with pytest.raises(CredentialLeaseError):
        await graph.invoke(action, options if action == "import_config" else {})
    assert not graph.hub.sent
    assert not graph.service.entries and not graph.service.root.exists()


@pytest.mark.asyncio
async def test_hosted_reconnect_before_status_cannot_select_the_replacement(
    hosted_graph, monkeypatch
):
    graph = hosted_graph
    send = graph.service.send

    async def reconnect(instance, params):
        graph.hub.websocket.state.plugin_generation = "replacement"
        return await send(instance, params)

    monkeypatch.setattr(graph.service, "send", reconnect)
    response = await graph.post("overview", {})
    assert response.status_code == 409
    assert not graph.hub.sent
    assert not graph.service.entries


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["rest", "mcp_import", "mcp_direct"])
@pytest.mark.parametrize(
    "change,kind,expected_status",
    [
        ("generation", "rule", 409),
        ("plugin_lease", "rule", 403),
        ("generation", "policy", 409),
        ("plugin_lease", "source", 403),
    ],
)
async def test_hosted_import_rolls_back_when_plugin_changes_before_commit(
    hosted_graph, monkeypatch, change, kind, expected_status, route
):
    from services.reference_graph.operations import export_config

    graph = hosted_graph
    settings = {
        "rule": {"id": "existing", "config": {"enabled": False}},
        "policy": {"config": {"exclude": ["old/**"]}},
        "source": {"id": "existing", "config": {}, "enabled": False, "reauthorize": True},
    }[kind]
    options = {"kind": kind, **settings, "expected_version": 0}
    assert (await graph.post("import_config", options)).status_code == 200
    workspace = next(iter(graph.service.entries.values())).workspace
    store = workspace.store
    original = export_config(workspace, kind=kind)
    if kind == "policy":
        options["config"] = {"exclude": ["new/**"]}
    bump = store._bump
    entered, resume = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def delayed_bump(db):
        bump(db)
        loop.call_soon_threadsafe(entered.set)
        assert resume.wait(watchdog_timeout())

    monkeypatch.setattr(store, "_bump", delayed_bump)

    async def update():
        arguments = {**options, "expected_version": 1}
        if route == "rest":
            assert (await graph.post("import_config", arguments)).status_code == expected_status
            return
        action = "import_config"
        if route == "mcp_direct":
            action = "set_" + kind
            arguments.pop("kind")
            arguments.pop("reauthorize", None)
        if change == "plugin_lease":
            with pytest.raises(CredentialLeaseError):
                await graph.invoke(action, arguments)
        else:
            assert (await graph.invoke(action, arguments))["error"] == "operation_target_changed"

    post = asyncio.create_task(update())
    try:
        await await_signal(entered, "authored update before commit", producers=[post])
        if change == "generation":
            graph.hub.websocket.state.plugin_generation = "replacement"
        else:
            setattr(graph.hub.websocket.state, AUTHENTICATED_LEASE_STATE, graph.leases["read"])
    finally:
        resume.set()
        await post
    assert export_config(workspace, kind=kind) == original


@pytest.mark.asyncio
async def test_editor_explorer_routes_preserve_paging_and_scope(setup):
    from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE

    service, ctx = setup
    body = {
        "action": "query",
        "instance": "one",
        "editor_id": "one",
        "project_id": "project",
        "options": {"node": "a", "direction": "outgoing", "limit": 25},
    }

    class Request:
        state = SimpleNamespace()

        async def json(self):
            return body

    request = Request()
    setattr(request.state, AUTHENTICATED_LOCAL_STATE, True)
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        for index in range(61):
            entry.workspace.store.put_declaration(
                str(index), "a", Edge(f"b-{index}", "manual"), expected_version=0
            )
        edges = []
        progress = FixtureProgress(4 * 61 + 32, "REST graph fixture")
        while True:
            progress.step(body["options"].get("cursor"))
            response = await handle(request, service)
            assert response.status_code == 200
            page = json.loads(response.body)["data"]
            assert len(page["edges"]) <= 25
            edges.extend(page["edges"])
            if page["next_cursor"] is None:
                break
            body["options"]["cursor"] = page["next_cursor"]
        assert len({edge["id"] for edge in edges}) == 61
        for action, options in (
            ("explain", {"id": edges[0]["id"]}),
            ("impact", {"node": "a", "direction": "outgoing", "limit": 25}),
            ("path", {"node": "a", "target": "b-0", "direction": "outgoing", "limit": 25}),
        ):
            body.update(action=action, options=options)
            response = await handle(request, service)
            assert response.status_code == 200
            assert "graph_status" in json.loads(response.body)["data"]
        body["editor_id"] = "another-editor"
        assert (await handle(request, service)).status_code == 409
        body.update(action="declare", editor_id="one")
        assert (await handle(request, service)).status_code == 400
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_failed_ready_capture_cleanup_preserves_disclosed_shared_snapshot(setup, monkeypatch):
    from services.reference_graph.operations import cleanup
    from services.reference_graph import snapshot_storage

    service, ctx = setup
    try:
        # Given a ready snapshot disclosed through the production service.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        entry.watching = True
        workspace, store = entry.workspace, entry.workspace.store
        for index in range(4):
            store.put_declaration(str(index), "a", Edge(str(index), "manual"), expected_version=0)
        saved = (await service.execute(ctx, "snapshot", {"name": "saved"}))["data"]

        # When a shared alias fails delivery after cleanup has retired both finished jobs.
        def cleanup_and_reject(*_):
            cleanup(workspace, before=time.time() + 1, limit=100)
            raise PermissionError("source access revoked")

        with monkeypatch.context() as patch:
            patch.setattr(service, "_validate_exploration", cleanup_and_reject)
            with pytest.raises(PermissionError):
                await service.execute(ctx, "snapshot", {"name": "undisclosed"})
        for _ in range(10):
            result = cleanup(workspace, before=time.time() + 1, limit=1)
            if not result["more"]:
                break

        # Then only the disclosed snapshot owns the intact and still reusable backing.
        with store._connect() as db:
            snapshots = list(db.execute("SELECT id FROM graph_snapshots"))
            assert [row[0] for row in snapshots] == [saved["snapshot"]["id"]]
            assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 4
            assert db.execute("SELECT count(*) FROM graph_snapshot_refs").fetchone()[0] == 1
            backing = snapshot_storage.backing(db, saved["snapshot"]["id"])
        fresh = (await service.execute(ctx, "snapshot", {"name": "fresh"}))["data"]
        assert fresh["edges"] == saved["edges"]
        with store._connect() as db:
            assert snapshot_storage.backing(db, fresh["snapshot"]["id"]) == backing
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["impact", "snapshot"])
async def test_failed_continuation_remains_replayable_and_ready_snapshot_survives(
    setup, monkeypatch, kind
):
    from services.reference_graph.operations import cleanup

    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        entry.watching = True
        workspace = entry.workspace
        for index in range(5):
            workspace.store.put_declaration(
                str(index), "a", Edge(str(index), "manual"), expected_version=0
            )
        queries = workspace.queries()
        arguments = (
            {"node": "a", "direction": "outgoing"} if kind == "impact" else {"name": "resumable"}
        )
        first = (await service.execute(ctx, kind, {**arguments, "limit": 1}))["data"]
        ready = queries.snapshot(name="ready", limit=100)["snapshot"]["id"]

        def reject(*_):
            raise PermissionError("revoked")

        monkeypatch.setattr(service, "_validate_exploration", reject)
        with pytest.raises(PermissionError):
            await service.execute(
                ctx,
                kind,
                {**arguments, "limit": 1, "cursor": first["next_cursor"]},
            )
        cleanup(workspace, before=time.time() + 1, limit=100)
        assert getattr(queries, kind)(**arguments, limit=1, cursor=first["next_cursor"])
        assert queries.diff(ready, ready)["changes"] == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_first_page_waits_for_producer_then_retires_scratch(setup, monkeypatch):
    import asyncio
    import threading
    from services.reference_graph.operations import cleanup

    service, ctx = setup
    unblock = threading.Event()
    task = None
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        entry.watching = True
        workspace = entry.workspace
        for index in range(3):
            workspace.store.put_declaration(
                str(index), "a", Edge(str(index), "manual"), expected_version=0
            )
        queries = workspace.queries()
        impact = queries.impact
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def stalled(**arguments):
            page = impact(**arguments)
            loop.call_soon_threadsafe(entered.set)
            assert unblock.wait(10)
            return page

        monkeypatch.setattr(queries, "impact", stalled)
        task = asyncio.create_task(
            service.execute(ctx, "impact", {"node": "a", "direction": "outgoing", "limit": 1})
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        unblock.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = cleanup(workspace, before=time.time() + 1, limit=100)
        assert result["obsolete_queries_removed"] == 1
        with workspace.store._connect() as db:
            assert db.execute("SELECT count(*) FROM graph_query_nodes").fetchone()[0] == 0
    finally:
        unblock.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_cleanup_rolls_back_partial_reclamation(setup, monkeypatch):
    from contextlib import contextmanager
    from services.reference_graph.operations import cleanup

    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace = entry.workspace
        store = workspace.store
        for index in range(3):
            store.put_declaration(str(index), "a", Edge(str(index), "manual"), expected_version=0)
        queries = workspace.queries()
        page = queries.snapshot(limit=1)
        store.put_declaration("change", "a", Edge("extra", "manual"), expected_version=0)
        connect = store._connect

        @contextmanager
        def interrupted(*, write=False, **kwargs):
            with connect(write=write, **kwargs) as db:
                yield db
                if write:
                    raise RuntimeError("rollback cleanup")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_connect", interrupted)
            with pytest.raises(RuntimeError, match="rollback cleanup"):
                cleanup(workspace, before=time.time() + 1, limit=1)
        with store._connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM graph_snapshot_edges WHERE snapshot=?",
                    (page["query_id"],),
                ).fetchone()[0]
                == 1
            )
        # The edge and its backing reference are separate bounded cleanup rows.
        first = cleanup(workspace, before=time.time() + 1, limit=1)
        second = cleanup(workspace, before=time.time() + 1, limit=1)
        assert first["obsolete_queries_removed"] + second["obsolete_queries_removed"] == 1
    finally:
        await service.aclose()
