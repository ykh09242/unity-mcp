"""Operational actions preserve scope, authored state and unfinished work."""

from types import SimpleNamespace
import json
import time

import pytest

from core.config import config
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import Edge, RevisionConflict
from services.reference_graph.http import handle
from services.credential_lease import CredentialLease
from services.operation_permissions import OperationGrants, Permission
from transport.local_auth_middleware import LocalControlAuthMiddleware
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from starlette.applications import Starlette
from starlette.routing import Route
import httpx


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)

    async def send(instance, params):
        return {"instance_id": instance, "project_id": "project", "epoch": "epoch", "revision": 0}

    service = ReferenceGraphService(tmp_path / "graphs", send=send)
    monkeypatch.setattr(service, "start", lambda *_, **__: None)

    class Context:
        fastmcp = SimpleNamespace(_reference_graph_service=service)
        instance = "one"

        async def get_state(self, key):
            return self.instance if key == "unity_instance" else None

    return service, Context()


@pytest.mark.asyncio
async def test_config_pages_cas_reauthorization_and_isolation(setup, tmp_path):
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
                    "config": {"call": "Loader.Load", "argument": 0, "scheme": "resources"},
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
async def test_retry_preserves_failure_eligibility_and_recovery_invalidation(setup):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        assert workspace.acquire("worker")
        asset = {
            "guid": "a" * 32,
            "path": "Assets/Fixture.prefab",
            "kind": "asset",
            "source_format": "none",
            "keys": [],
        }
        workspace.record_page("worker", [asset], {}, "scan")
        workspace.fail_asset("worker", asset, ValueError("temporary collection failure"))
        assert workspace.dirty_assets() == []

        await service.execute(ctx, "retry", {})
        assert [item["guid"] for item in workspace.dirty_assets()] == [asset["guid"]]
        assert workspace.counts()["failed_assets"] == 1
        with workspace.store._connect(write=True) as db:
            assert db.execute("SELECT attempts FROM graph_asset_failures").fetchone()[0] == 1
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


@pytest.mark.asyncio
async def test_remote_http_principal_permissions_and_expiry(setup, monkeypatch):
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
    try:
        first = await handle(request, service)
        assert first.status_code == 200
        first_scope = json.loads(first.body)["data"]["scope"]
        payload["action"] = "cleanup"
        payload["options"] = {"before": time.time()}
        assert (await handle(request, service)).status_code == 403
        payload["action"] = "overview"
        payload["options"] = {}
        setattr(
            request.state,
            AUTHENTICATED_LEASE_STATE,
            CredentialLease(authority, object(), "bob", LEGACY_GRANTS),
        )
        second = await handle(request, service)
        assert second.status_code == 200
        assert json.loads(second.body)["data"]["scope"] != first_scope
        authority.revoked = True
        assert (await handle(request, service)).status_code == 403
    finally:
        await service.aclose()
