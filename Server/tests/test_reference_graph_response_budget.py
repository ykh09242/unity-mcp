"""Large stored evidence stays bounded at the actual operations delivery boundary."""

import json
from types import SimpleNamespace

import pytest

from models.response_limits import MAX_RESPONSE_BYTES
from services.reference_graph.http import handle
from services.reference_graph import response_budget
from services.reference_graph.response_budget import GraphResponseTooLarge
from services.reference_graph.store import Edge
from tests.test_reference_graph_operations import setup  # noqa: F401
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE


class OperationsRequest:
    def __init__(self, action, options):
        self.state = SimpleNamespace()
        setattr(self.state, AUTHENTICATED_LOCAL_STATE, True)
        self.body = {
            "action": action,
            "instance": "one",
            "editor_id": "one",
            "project_id": "project",
            "options": options,
        }

    async def json(self):
        return self.body


@pytest.mark.asyncio
async def test_accumulated_declarations_stay_below_real_wire_limit(setup):
    service, ctx = setup
    try:
        # Given individually accepted declarations whose aggregate exceeds 64 MiB.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        for index in range(65):
            await service.execute(
                ctx,
                "declare",
                {
                    "id": str(index),
                    "owner": "a",
                    "edge": {"target": f"b-{index}", "kind": "manual", "evidence": "x" * 2**20},
                    "expected_version": 0,
                },
            )
        request = OperationsRequest("query", {"node": "a", "limit": 100})
        seen = []
        # When the same query is continued through the real REST handler.
        for _ in range(66):
            response = await handle(request, service)
            # Then every wire page is bounded and every declaration appears once.
            assert response.status_code == 200
            assert len(response.body) <= MAX_RESPONSE_BYTES
            page = json.loads(response.body)["data"]
            seen.extend(edge["id"] for edge in page["edges"])
            if page["next_cursor"] is None:
                break
            request.body["options"]["cursor"] = page["next_cursor"]
        else:
            pytest.fail("Byte-bounded continuation failed to finish")
        assert len(seen) == 65
        assert len(set(seen)) == 65
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["query", "impact", "path", "snapshot"])
async def test_byte_pages_continue_without_lost_or_repeated_evidence(setup, monkeypatch, action):
    service, ctx = setup
    try:
        # Given multi-byte and escaped evidence, with one or two items per byte page.
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store = entry.workspace.store
        evidence = '한\n"\\😀' * 300
        for index in range(7):
            owner = f"n{index}" if action == "path" else "n0"
            store.put_declaration(
                str(index),
                owner,
                Edge(f"n{index + 1}", "manual", evidence=evidence),
                expected_version=0,
            )
        options = {"limit": 100}
        if action != "snapshot":
            options.update(node="n0", direction="outgoing")
        if action == "path":
            options["target"] = "n7"
        seen, page_count = [], 0
        # When all byte-bound continuations and lost-response replays are consumed.
        for _ in range(15):
            page = (await service.execute(ctx, action, options))["data"]
            page_count += 1
            seen.extend(edge["id"] for edge in page["edges"])
            assert all(edge["evidence"] == evidence for edge in page["edges"])
            if options.get("cursor") and action != "query":
                replay = (await service.execute(ctx, action, options))["data"]
                assert replay["edges"] == page["edges"]
                assert replay["next_cursor"] == page["next_cursor"]
            if page["next_cursor"] is None:
                break
            options["cursor"] = page["next_cursor"]
        else:
            pytest.fail("Byte page continuation failed to finish")
        # Then complete authoritative payloads and cursor positions are preserved.
        assert page_count > 1
        assert len(seen) == 7
        assert set(seen) == {f"declared:{index}" for index in range(7)}
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["query", "impact", "path", "snapshot", "explain"])
async def test_impossible_item_never_commits_undeliverable_scratch(setup, monkeypatch, action):
    service, ctx = setup
    try:
        # Given one stored item which cannot fit even an otherwise empty page.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store = entry.workspace.store
        store.put_declaration(
            "huge", "a", Edge("b", "manual", evidence="x" * 16384), expected_version=0
        )
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        options = {}
        if action in {"query", "impact", "path"}:
            options.update(node="a", direction="outgoing")
        if action == "path":
            options["target"] = "b"
        if action == "explain":
            options["id"] = "declared:huge"
        # When SQLite evidence would be decoded and a first-page job committed.
        with pytest.raises(GraphResponseTooLarge, match="individual item"):
            await service.execute(ctx, action, options)
        # Then failure leaves no undisclosed traversal or snapshot state.
        entry.workspace.queries()
        with store._connect() as db:
            assert db.execute("SELECT count(*) FROM graph_queries").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM graph_snapshots").fetchone()[0] == 0
        if action != "snapshot":
            response = await handle(OperationsRequest(action, options), service)
            assert response.status_code == 413
            assert json.loads(response.body)["error"] == "reference_graph_response_too_large"
            assert len(response.body) < 512
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_final_delivery_failure_abandons_first_page_and_preserves_continuation(
    setup, monkeypatch
):
    service, ctx = setup
    try:
        # Given a resumable traversal with a successfully delivered first page.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store = entry.workspace.store
        for index in range(4):
            store.put_declaration(str(index), "a", Edge(str(index), "manual"), expected_version=0)
        options = {"node": "a", "direction": "outgoing", "limit": 1}
        first = (await service.execute(ctx, "impact", options))["data"]

        # When envelope admission fails after the SQLite producer has committed.
        def reject_envelope(value):
            if "graph_status" in value.get("data", {}):
                raise GraphResponseTooLarge()

        with monkeypatch.context() as patch:
            patch.setattr("services.reference_graph.service.require_response", reject_envelope)
            with pytest.raises(GraphResponseTooLarge):
                await service.execute(ctx, "impact", options)
            with pytest.raises(GraphResponseTooLarge):
                await service.execute(ctx, "impact", {**options, "cursor": first["next_cursor"]})
        # Then only the undisclosed new job is abandoned; continuation replays.
        with store._connect() as db:
            jobs = list(db.execute("SELECT id,abandoned FROM graph_queries"))
        assert len(jobs) == 2
        assert sum(row["abandoned"] for row in jobs) == 1
        assert next(row for row in jobs if row["id"] == first["query_id"])["abandoned"] == 0
        replay = (
            await service.execute(ctx, "impact", {**options, "cursor": first["next_cursor"]})
        )["data"]
        assert replay["edges"][0]["id"] != first["edges"][0]["id"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_diff_and_configuration_export_preserve_byte_continuations(setup, monkeypatch):
    service, ctx = setup
    try:
        # Given seven changed edges and portable rules larger than one byte page.
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store, queries = entry.workspace.store, entry.workspace.queries()

        def capture():
            options = {"limit": 100}
            for _ in range(10):
                page = queries.snapshot(**options)
                if page["next_cursor"] is None:
                    return page["snapshot"]["id"]
                options["cursor"] = page["next_cursor"]
            pytest.fail("Snapshot did not finish")

        for index in range(7):
            store.put_declaration(
                str(index), "a", Edge(str(index), "manual", evidence="x" * 1500), expected_version=0
            )
        before = capture()
        for index in range(7):
            store.put_declaration(
                str(index), "a", Edge(str(index), "manual", evidence="y" * 3000), expected_version=1
            )
            store.put_rule(
                str(index), {"enabled": False, "prefix": "한" * 1500}, expected_version=0
            )
        after = capture()
        # When diff candidate rows and configuration rows fill their byte budgets.
        options, changes, compared = {"before": before, "after": after, "limit": 100}, [], 0
        for _ in range(10):
            page = queries.diff(**options)
            changes.extend(page["changes"])
            compared += page["compared"]
            if page["next_cursor"] is None:
                break
            options["cursor"] = page["next_cursor"]
        else:
            pytest.fail("Diff did not finish")
        export_options, items = {"kind": "rule", "limit": 100}, []
        for _ in range(10):
            page = (await service.execute(ctx, "export_config", export_options))["data"]
            items.extend(page["items"])
            if page["next_after"] is None:
                break
            export_options["after"] = page["next_after"]
        else:
            pytest.fail("Configuration export did not finish")
        # Then candidates and authoritative fields survive every shortened page.
        assert compared == len(changes) == 7
        assert {item["id"] for item in changes} == {f"declared:{index}" for index in range(7)}
        assert all(
            item["change"] == "modified" and item["after"]["evidence"] == "y" * 3000
            for item in changes
        )
        assert [item["id"] for item in items] == [str(index) for index in range(7)]
        assert all(item["config"]["prefix"] == "한" * 1500 for item in items)
        with pytest.raises(GraphResponseTooLarge):
            await service.execute(ctx, "rules", {})
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_sql_rejects_large_scalar_before_materializing_it(setup, monkeypatch):
    service, ctx = setup
    try:
        # Given a byte-oversized, multi-byte payload and a large scalar identity.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store = entry.workspace.store
        store.put_declaration(
            "huge", "a", Edge("b", "manual", evidence="한" * 4096), expected_version=0
        )
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        # When the row crosses SQLite's response projection boundary.
        with store._connect() as db:
            row = response_budget.bounded_rows(db, "SELECT payload FROM edges").fetchone()
        # Then the Python row carries only a small rejection marker, no payload.
        assert row["payload"] is None
        assert row["_response_bytes"] > 8192
        with pytest.raises(GraphResponseTooLarge):
            store.query("a")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_rest_final_envelope_limit_returns_a_bounded_error(setup, monkeypatch):
    service, _ = setup
    try:
        # Given an unexpected oversized service envelope, independently of paging.
        async def oversized(*_):
            return {"success": True, "data": {"value": "한\n" * 4096}}

        monkeypatch.setattr(service, "execute", oversized)
        monkeypatch.setattr("services.reference_graph.http.MAX_RESPONSE_BYTES", 8192)
        # When the actual REST facade admits its final wire body.
        response = await handle(OperationsRequest("overview", {}), service)
        # Then no oversized success payload escapes the facade.
        assert response.status_code == 413
        assert len(response.body) < 512
        assert json.loads(response.body)["error"] == "reference_graph_response_too_large"
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,evidence_bytes", [("impact", 6800), ("path", 6800), ("snapshot", 7440)]
)
async def test_admitted_near_boundary_page_remains_replayable(
    setup, monkeypatch, action, evidence_bytes
):
    service, ctx = setup
    try:
        # Given persisted legacy evidence near the raw-row assembly boundary.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store, queries = entry.workspace.store, entry.workspace.queries()
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        with store._connect(write=True) as db:
            for index in range(3):
                owner = str(index) if action == "path" else "a"
                target = str(index + 1)
                payload = json.dumps(
                    {"target": target, "kind": "manual", "evidence": "x" * evidence_bytes},
                    separators=(",", ":"),
                )
                db.execute(
                    "INSERT INTO edges VALUES (?,?,?,?,?,?)",
                    (f"declared:{index}", owner, "manual", target, "manual", payload),
                )
        options = {"limit": 1}
        if action != "snapshot":
            options.update(node="0" if action == "path" else "a", direction="outgoing")
        if action == "path":
            options["target"] = "3"
        # When a committed page is continued and that same continuation is retried.
        method = getattr(queries, action)
        first = method(**options)
        assert first["next_cursor"]
        second = method(**options, cursor=first["next_cursor"])
        replay = method(**options, cursor=first["next_cursor"])
        # Then enrichment and job-header charges cannot invalidate admitted cursors.
        assert second == replay
        seen = [edge["id"] for edge in first["edges"] + second["edges"]]
        page = second
        for _ in range(10):
            if page["next_cursor"] is None:
                break
            page = method(**options, cursor=page["next_cursor"])
            seen.extend(edge["id"] for edge in page["edges"])
        else:
            pytest.fail("Near-boundary continuation failed to finish")
        assert sorted(seen) == [f"declared:{index}" for index in range(3)]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_authorization_metadata_is_complete_or_byte_bounded_failure(setup, monkeypatch):
    service, ctx = setup
    try:
        # Given a compact evidence page whose joined source metadata is much larger.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace, store = entry.workspace, entry.workspace.store
        with store._connect(write=True) as db:
            db.execute(
                "INSERT INTO sources VALUES ('external:test',1,1,?)",
                (json.dumps({"include": ["x" * 4000]}),),
            )
            for index in range(3):
                db.execute(
                    "INSERT INTO graph_external VALUES (?,?,?,?,?,?,?)",
                    (
                        "external:test",
                        f"external:{index}",
                        f"file{index}.json",
                        "fingerprint",
                        "{}",
                        "analysis",
                        "seen",
                    ),
                )
                db.execute(
                    "INSERT INTO graph_external_paths VALUES (?,?,?)",
                    ("external:test", f"external:{index}", f"file{index}.json"),
                )
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        # When complete authorization requires multiple copies of that metadata.
        with pytest.raises(GraphResponseTooLarge):
            workspace.external_validation(
                (("external:test", f"external:{index}") for index in range(3))
            )
        with pytest.raises(GraphResponseTooLarge):
            workspace.external_nodes((f"external:{index}" for index in range(3)))
        # Then a smaller request retains all requested admission evidence unchanged.
        rows = workspace.external_nodes(["external:0"])
        assert len(rows) == 1 and rows[0]["owner"] == "external:0"
        assert json.loads(rows[0]["config"])["include"] == ["x" * 4000]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_diagnostics_byte_offsets_and_response_metadata_are_bounded(setup, monkeypatch):
    service, ctx = setup
    try:
        # Given diagnostic payloads and response metadata backed by real SQLite rows.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace, store = entry.workspace, entry.workspace.store
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        with store._connect(write=True) as db:
            for index in range(4):
                db.execute(
                    "INSERT INTO graph_issues VALUES (?,?,?,?)",
                    (
                        "a",
                        "manual",
                        index,
                        json.dumps({"detail": "한" * 1500, "ordinal": index}, ensure_ascii=False),
                    ),
                )
        # When shorter diagnostic pages advance the revision-bound offset.
        options, issues = {"node": "a", "limit": 100}, []
        for _ in range(5):
            page = (await service.execute(ctx, "diagnostics", options))["data"]
            issues.extend(page["issues"])
            if page["next_offset"] is None:
                break
            options.update(offset=page["next_offset"], revision=page["revision"])
        else:
            pytest.fail("Diagnostic byte pages did not finish")
        assert [item["ordinal"] for item in issues] == list(range(4))
        # Then oversized non-page metadata fails before decoding a response copy.
        with store._connect(write=True) as db:
            workspace._set(db, "progress", {"phase": "idle", "detail": "x" * 10000})
            workspace._set(db, "policy", {"version": 1, "exclude": ["x" * 10000]})
        with pytest.raises(GraphResponseTooLarge):
            workspace.progress(bounded=True)
        with pytest.raises(GraphResponseTooLarge):
            workspace.policy(bounded=True)
        # Internal collection can still read authored state to replace or recover it.
        assert workspace.policy()["version"] == 1
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["receipt_validation", "mutation_request"])
async def test_receipt_authorization_bounds_stored_source_configs(setup, monkeypatch, entrypoint):
    service, ctx = setup
    try:
        # Given individually small disabled configs whose aggregate exceeds a page.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace, store = entry.workspace, entry.workspace.store
        for index in range(3):
            store.set_source(
                f"external:test{index}",
                {"include": ["x" * 4000]},
                expected_version=0,
                enabled=False,
            )
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        # When receipt authorization or the actual mutation service reads all grants.
        with pytest.raises(GraphResponseTooLarge):
            if entrypoint == "receipt_validation":
                service._validate_receipt_sources(workspace, {})
            else:
                await service.execute(ctx, "release_mutation", {"id": "missing"})
        # Then the request fails before mutation state exists, while collectors retain access.
        with store._connect() as db:
            assert (
                db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_mutations'").fetchone()
                is None
            )
        assert (
            len(
                [source for source in store.sources() if source["provider"].startswith("external:")]
            )
            == 3
        )
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["external_validation", "query_request"])
async def test_response_admission_bounds_policy_before_decode(setup, monkeypatch, entrypoint):
    service, ctx = setup
    try:
        # Given an oversized persisted policy, including a warm collector admission cache.
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        workspace, store = entry.workspace, entry.workspace.store
        with store._connect(write=True) as db:
            workspace._set(
                db, "policy", {"version": 1, "respect_gitignore": False, "exclude": ["x" * 10000]}
            )
        assert workspace.admission()("Assets/Other.prefab")
        monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        loads = json.loads

        def reject_oversized_decode(value, *args, **kwargs):
            if isinstance(value, str) and "x" * 10000 in value:
                pytest.fail("Response admission decoded an oversized stored policy")
            return loads(value, *args, **kwargs)

        # When the real response authorization path needs collection policy.
        with monkeypatch.context() as patch:
            patch.setattr(json, "loads", reject_oversized_decode)
            with pytest.raises(GraphResponseTooLarge):
                if entrypoint == "external_validation":
                    workspace.external_validation([], admission=True)
                else:
                    await service.execute(ctx, "query", {"node": "missing"})
        # Then response admission fails closed without disabling collector recovery.
        assert workspace.policy()["exclude"] == ["x" * 10000]
    finally:
        await service.aclose()
