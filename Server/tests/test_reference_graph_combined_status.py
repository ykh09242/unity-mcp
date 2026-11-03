import hashlib

import pytest

from services.reference_graph.store import RevisionConflict
from tests.test_reference_graph_service import setup


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", [False, True])
async def test_ignore_proof_reads_one_snapshot_and_releases_it_before_native_io(
    setup, monkeypatch, combined
):
    import sqlite3
    from contextlib import contextmanager

    service, sender, ctx = setup
    try:
        entry, status = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        store = entry.workspace.store
        snapshot = store.read_snapshot
        connect = sqlite3.connect
        collect = entry.engine._collector
        open_snapshot = False
        connections = 0

        @contextmanager
        def tracked_snapshot():
            nonlocal open_snapshot
            with snapshot():
                open_snapshot = True
                try:
                    yield
                finally:
                    open_snapshot = False

        def counted_connect(*args, **kwargs):
            nonlocal connections
            if args[0] == store.path:
                connections += 1
            return connect(*args, **kwargs)

        async def native(*args, **kwargs):
            assert not open_snapshot
            return await collect(*args, **kwargs)

        monkeypatch.setattr(store, "read_snapshot", tracked_snapshot)
        monkeypatch.setattr(sqlite3, "connect", counted_connect)
        monkeypatch.setattr(entry.engine, "_collector", native)
        status.update(
            ignore_current_validation=1,
            ignore_current_verified=True,
            ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
        )
        sender.calls.clear()
        assert await entry.engine.validate_ignore(status if combined else None)
        assert [args["action"] for args, *_ in sender.calls] == (
            [] if combined else ["ignore_rules"]
        )
        assert connections == 1
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "rotated", "changed"])
async def test_combined_query_proves_exact_snapshot_at_both_boundaries(setup, monkeypatch, change):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    observations = 0
    active = False

    async def send(instance, params):
        nonlocal observations
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                observations += 1
                token = (
                    "rotated" if active and change == "rotated" and observations == 2 else "ignore"
                )
                result.update(
                    ignore_current_verified=not (
                        active and change == "changed" and observations == 2
                    ),
                    ignore_snapshot_digest=hashlib.sha256(token.encode()).hexdigest(),
                )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        observations = 0
        active = True
        sender.calls.clear()
        if change is None:
            await service.execute(ctx, "query", {"node": "asset:fixture"})
            assert [call[0]["action"] for call in sender.calls] == ["status", "status"]
            assert all(call[0]["validate_ignore_current"] for call in sender.calls)
        else:
            with pytest.raises(RevisionConflict):
                await service.execute(ctx, "query", {"node": "asset:fixture"})
            assert not entry.workspace.ignore_status()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_legacy_editor_keeps_four_observations(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result["ignore_watchers"] = False
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert [call[0]["action"] for call in sender.calls] == [
            "status",
            "ignore_rules",
            "ignore_rules",
            "status",
        ]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_replacement_older_editor_receives_no_cached_scope_or_snapshot(setup, monkeypatch):
    from services.reference_graph.service import ReferenceCollectionError

    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    replaced = False

    async def send(instance, params):
        if replaced and params.get("validate_ignore_current"):
            sender.calls.append((params.copy(), None, None))
            raise ReferenceCollectionError("unsupported")
        result = await sender(instance, params)
        if params["action"] == "status":
            result["ignore_watchers"] = False
            if not replaced:
                result["ignore_current_validation"] = 1
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        sender.calls.clear()
        replaced = True
        sender.identity = "replacement"
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert sender.calls[0][0] == {"action": "status", "validate_ignore_current": True}
        assert sender.calls[1][0] == {"action": "status"}
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_combined_validation_hint_tracks_effective_policy(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                result.update(
                    ignore_current_verified=True,
                    ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_policy({"respect_gitignore": False}, expected_version=0)
        assert await entry.engine.reconcile()
        for _ in range(2):
            sender.calls.clear()
            await service.execute(ctx, "query", {"node": "asset:fixture"})
            assert all(not call[0].get("validate_ignore_current") for call in sender.calls)
            assert all(call[0]["action"] != "ignore_rules" for call in sender.calls)
        entry.workspace.set_policy({"respect_gitignore": True}, expected_version=1)
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert [call[0]["action"] for call in sender.calls] == ["status", "status"]
        assert all(call[0].get("validate_ignore_current") is True for call in sender.calls)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_file_change_between_observations_blocks_delivery(setup, monkeypatch, tmp_path):
    service, sender, ctx = setup
    rule = tmp_path / "fixture.gitignore"
    rule.write_text("Private.prefab\n", encoding="utf-8")
    expected_content = rule.read_bytes()
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                result.update(
                    ignore_current_verified=rule.read_bytes() == expected_content,
                    ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        monkeypatch.setattr(
            service,
            "start",
            lambda *args, **kwargs: rule.write_text("Changed.prefab\n", encoding="utf-8"),
        )
        with pytest.raises(RevisionConflict):
            await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert not entry.workspace.ignore_status()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_final_observation_falls_back_if_editor_loses_capability(setup, monkeypatch):
    from services.reference_graph.service import ReferenceCollectionError

    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    observations = 0
    fallback = False

    async def send(instance, params):
        nonlocal observations
        if params.get("validate_ignore_current"):
            observations += 1
            if fallback and observations == 2:
                sender.calls.append((params.copy(), None, None))
                raise ReferenceCollectionError("unsupported")
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                result.update(
                    ignore_current_verified=True,
                    ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        observations = 0
        fallback = True
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert [call[0]["action"] for call in sender.calls] == [
            "status",
            "status",
            "ignore_rules",
            "status",
        ]
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        {"ignore_current_validation": True},
        {"ignore_revision": True},
        {"revision": False},
        {"epoch": True},
        {"ignore_snapshot_digest": True},
    ],
)
async def test_malformed_combined_proof_never_certifies_delivery(setup, monkeypatch, malformed):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    count = 0
    active = False

    async def send(instance, params):
        nonlocal count
        result = await sender(instance, params)
        result["ignore_revision"] = 1
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                count += 1
                result.update(
                    ignore_current_verified=True,
                    ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                )
                if active and count == 2:
                    result.update(malformed)
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        count = 0
        active = True
        with pytest.raises(RevisionConflict):
            await service.execute(ctx, "query", {"node": "asset:fixture"})
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_boolean_capability_does_not_enable_combined_status(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=True)
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert [call[0]["action"] for call in sender.calls] == [
            "status",
            "ignore_rules",
            "ignore_rules",
            "status",
        ]
        assert all("validate_ignore_current" not in call[0] for call in sender.calls)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_legacy_final_status_detects_move_after_ignore_validation(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    active = False
    validations = 0
    revision = 0

    async def send(instance, params):
        nonlocal validations, revision
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, revision=revision)
        elif params["action"] == "ignore_rules" and active:
            validations += 1
            if validations == 2:
                revision = 1
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_policy({"exclude": ["Assets/Private"]}, expected_version=0)
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        active = True
        with pytest.raises(RevisionConflict):
            await service.execute(ctx, "query", {"node": "asset:fixture"})
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_external_policy_disable_clears_combined_hint_after_one_query(setup, monkeypatch):
    from services.reference_graph.store import GraphStore
    from services.reference_graph.workspace import GraphWorkspace

    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                result.update(
                    ignore_current_verified=True,
                    ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        store = entry.workspace.store
        other = GraphWorkspace(GraphStore(store.path, store.scope))
        other.set_policy({"respect_gitignore": False}, expected_version=0)
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert sum(bool(call[0].get("validate_ignore_current")) for call in sender.calls) <= 1
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert all(not call[0].get("validate_ignore_current") for call in sender.calls)
        assert all(call[0]["action"] != "ignore_rules" for call in sender.calls)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_legacy_validation_rejects_boolean_ignore_revision(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    malformed = False

    async def send(instance, params):
        result = await sender(instance, params)
        result["ignore_revision"] = True if malformed and params["action"] == "ignore_rules" else 1
        if params["action"] == "status":
            result["ignore_watchers"] = False
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        malformed = True
        assert not await entry.engine.validate_ignore()
        assert not entry.workspace.ignore_status()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_missing_combined_proof_uses_legacy_then_fresh_status(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    active = False
    observations = 0

    async def send(instance, params):
        nonlocal observations
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(ignore_watchers=False, ignore_current_validation=1)
            if params.get("validate_ignore_current"):
                observations += 1
                if not active or observations != 2:
                    result.update(
                        ignore_current_verified=True,
                        ignore_snapshot_digest=hashlib.sha256(b"ignore").hexdigest(),
                    )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        observations = 0
        active = True
        sender.calls.clear()
        await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert [call[0]["action"] for call in sender.calls] == [
            "status",
            "status",
            "ignore_rules",
            "status",
        ]
    finally:
        await service.aclose()
