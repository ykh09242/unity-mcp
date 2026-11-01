"""Policy replacement remains bounded, atomic and available for legacy repair."""

from contextlib import contextmanager
import json

import pytest

from models.response_limits import MAX_RESPONSE_BYTES
from services.reference_graph import response_budget
from services.reference_graph.http import handle
from services.reference_graph.response_budget import GraphResponseTooLarge
from services.reference_graph.store import RevisionConflict, _json
from tests.test_reference_graph_operations import setup  # noqa: F401
from tests.test_reference_graph_response_budget import OperationsRequest
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_policy", "import_config"])
async def test_oversized_policy_rejection_preserves_previous_version_and_state(setup, action):
    service, ctx = setup
    try:
        # Given a valid request below the wire ceiling but above the real page budget.
        entry, _ = await service.resolve(ctx, "status")
        entry.watching = True
        workspace = entry.workspace
        workspace.store._budget.grow()
        workspace.set_policy({"exclude": ["Assets/Kept/**"]}, expected_version=0)
        assert await entry.engine.reconcile()
        previous = workspace.policy(bounded=True)
        revision = workspace.store.revision()
        assert workspace.acquire("collector")
        options = {"expected_version": 1, "config": {"exclude": ["a" * 1023] * 8200}}
        if action == "import_config":
            options["kind"] = "policy"
        assert len(_json(options).encode()) < MAX_RESPONSE_BYTES
        assert len(_json(options["config"]).encode()) > response_budget.MAX_PAGE_BYTES

        # When the production service rejects a replacement it could not subsequently read.
        with pytest.raises(GraphResponseTooLarge):
            await service.execute(ctx, action, options)

        # Then neither policy/CAS state nor collection ownership has been committed.
        assert workspace.policy(bounded=True) == previous
        assert workspace.store.revision() == revision
        assert workspace.renew("collector")
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_policy", "import_config"])
async def test_legacy_oversized_policy_can_be_replaced_without_materializing_old_value(
    setup, monkeypatch, action
):
    service, ctx = setup
    try:
        # Given legacy persisted policy larger than the unchanged production budget.
        entry, _ = await service.resolve(ctx, "status")
        entry.watching = True
        workspace = entry.workspace
        workspace.store._budget.grow()
        assert await entry.engine.reconcile()
        with workspace.store._connect(write=True) as db:
            workspace._set(db, "policy", {"version": 7, "exclude": ["a" * 1023] * 8200})
        connect = workspace.store._connect

        @contextmanager
        def bounded_values(**kwargs):
            with connect(**kwargs) as db:
                row_factory = db.row_factory

                def reject_large_scalar(cursor, row):
                    assert all(
                        not isinstance(value, str)
                        or len(value.encode()) <= response_budget.MAX_PAGE_BYTES
                        for value in row
                    ), "Legacy policy was materialized across the SQLite boundary"
                    return row_factory(cursor, row)

                db.row_factory = reject_large_scalar
                yield db

        monkeypatch.setattr(workspace.store, "_connect", bounded_values)
        options = {"expected_version": 7, "config": {"exclude": ["Assets/Private/**"]}}
        if action == "import_config":
            options["kind"] = "policy"

        # When replacement uses the tool service or the authenticated operations handler.
        if action == "import_config":
            response = await handle(OperationsRequest(action, options), service)
            assert response.status_code == 200
            result = json.loads(response.body)
        else:
            result = await service.execute(ctx, action, options)

        # Then the new policy is readable and increments the original optimistic version.
        assert result["data"]["version"] == 8
        assert workspace.policy(bounded=True) == {
            "version": 8,
            "exclude": ["Assets/Private/**"],
            "respect_gitignore": True,
        }
        assert await entry.engine.reconcile()
        assert (await service.execute(ctx, "policy", {}))["data"]["version"] == 8
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_version", [0, 6, 8])
async def test_legacy_policy_repair_rejects_stale_version_without_changes(setup, expected_version):
    service, ctx = setup
    try:
        # Given a legacy policy whose payload cannot pass bounded reads.
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        with workspace.store._connect(write=True) as db:
            workspace._set(db, "policy", {"version": 7, "exclude": ["a" * 1023] * 8200})
        revision = workspace.store.revision()

        # When the caller proposes a replacement against a different version.
        with pytest.raises(RevisionConflict):
            await service.execute(
                ctx, "set_policy", {"expected_version": expected_version, "config": {}}
            )

        # Then the stored value and graph revision remain unchanged.
        with workspace.store._connect() as db:
            version, size = db.execute(
                "SELECT json_extract(value,'$.version'),length(value) FROM graph_state WHERE key='policy'"
            ).fetchone()
        assert version == 7 and size > response_budget.MAX_PAGE_BYTES
        assert workspace.store.revision() == revision
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unauthenticated", "wrong_editor"])
async def test_legacy_policy_repair_preserves_authentication_and_scope(setup, failure):
    service, ctx = setup
    try:
        # Given an oversized policy in the target authenticated Editor scope.
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        with workspace.store._connect(write=True) as db:
            workspace._set(db, "policy", {"version": 7, "exclude": ["a" * 1023] * 8200})
        request = OperationsRequest(
            "import_config", {"kind": "policy", "expected_version": 7, "config": {}}
        )
        if failure == "unauthenticated":
            setattr(request.state, AUTHENTICATED_LOCAL_STATE, False)
        else:
            request.body["editor_id"] = "different-editor"

        # When repair is attempted without authentication or with the wrong Editor fence.
        response = await handle(request, service)

        # Then no replacement bypasses the existing authority boundaries.
        assert response.status_code == (401 if failure == "unauthenticated" else 409)
        with workspace.store._connect() as db:
            assert (
                db.execute(
                    "SELECT json_extract(value,'$.version') FROM graph_state WHERE key='policy'"
                ).fetchone()[0]
                == 7
            )
    finally:
        await service.aclose()
