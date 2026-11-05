"""Synthetic external change streams exercise full-sweep and incremental boundaries."""

import asyncio
from functools import partial
import hashlib
import json
from queue import Empty, Queue
from threading import Event

import pytest
from watchfiles import Change

from services.reference_graph.external import ExternalSource
from tests.test_reference_graph_reconcile import setup  # noqa: F401
from tests.test_reference_graph_service import setup as service_setup  # noqa: F401
from tests.timing import watchdog_timeout


class Producer:
    def __init__(self):
        self.batches = Queue()
        self.directories = []
        self.closed = 0
        self.before_ready = lambda: None

    def watch(self, *directories, **options):
        assert options["recursive"] is False
        assert options["watch_filter"] is None
        assert options["ignore_permission_denied"] is False
        self.directories.append(set(directories))
        try:
            self.before_ready()
            yield set()
            while not options["stop_event"].is_set():
                try:
                    changes, observed = self.batches.get(timeout=0.01)
                except Empty:
                    yield set()
                    continue
                try:
                    yield changes
                finally:
                    observed.set()
        finally:
            self.closed += 1

    def send(self, *events, ordered=False):
        observed = Event()
        batch = [(change, str(path)) for change, path in events]
        self.batches.put((batch if ordered else set(batch), observed))
        assert observed.wait(10)


@pytest.fixture
def tracked(setup, tmp_path):
    from services.reference_graph.external_changes import ExternalChanges

    _, workspace, _, engine = setup
    root = tmp_path / "settings"
    root.mkdir()
    provider = "external:fixture"
    config = {"root": str(root), "include": ["**/*.json"], "exclude": ["private/**"]}
    workspace.set_source(provider, config, expected_version=0)
    producer, clock = Producer(), [10.0]
    engine.external_changes = ExternalChanges(
        workspace, watch=producer.watch, clock=lambda: clock[0]
    )
    try:
        yield engine, workspace, provider, root, producer, clock
    finally:
        engine.external_changes.close()
        assert producer.closed == len(producer.directories)


@pytest.mark.asyncio
async def test_idle_delta_and_registration_gap(tracked, monkeypatch):
    engine, workspace, provider, root, producer, clock = tracked
    for i in range(16):
        (root / f"{i}.json").write_text("{}")
    for name in ("nested", "private", ".hidden", "ignored"):
        (root / name).mkdir()
    (root / ".gitignore").write_text("ignored/\n")
    counts = {"enumerations": 0, "stamps": 0, "reads": 0}
    for name, method in (("enumerations", "files"), ("stamps", "stamp"), ("reads", "read")):
        original = getattr(ExternalSource, method)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(ExternalSource, method, counted)
    producer.before_ready = lambda: (root / "gap.json").write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll()
    assert await poll()
    assert producer.directories == [{root, root / "nested"}]
    assert "gap.json" in workspace.external_inputs(provider)
    before = counts.copy()
    verified = workspace.provider_status()[provider]["last_verified_at"]
    assert await poll()
    assert counts == before
    assert workspace.provider_status()[provider]["last_verified_at"] == verified
    (root / "0.json").write_text('{"edit": true}')
    (root / "1.json").rename(root / "renamed.json")
    producer.send(
        (Change.modified, root / "0.json"),
        (Change.deleted, root / "1.json"),
        (Change.added, root / "renamed.json"),
    )
    assert await poll()
    assert counts["enumerations"] == before["enumerations"]
    assert counts["reads"] - before["reads"] == 2
    assert counts["stamps"] - before["stamps"] == 4
    assert set(workspace.external_inputs(provider)) == {p.name for p in root.glob("*.json")}
    assert workspace.provider_status()[provider]["last_verified_at"] == verified
    (root / "silent.json").write_text("{}")
    clock[0] += 61
    assert await poll()
    assert "silent.json" in workspace.external_inputs(provider)
    (root / "explicit.json").write_text("{}")
    assert await engine.reconcile()
    assert "explicit.json" in workspace.external_inputs(provider)


@pytest.mark.asyncio
async def test_watch_diagnostics_count_full_delta_and_fallback_without_paths(tracked, monkeypatch):
    from services.reference_graph import external_changes

    engine, workspace, provider, root, _, _ = tracked
    (root / "one.json").write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll() and await poll()
    status = workspace.provider_status()[provider]["watch"]
    assert {
        key: status[key]
        for key in ("active", "reason", "full_scans", "delta_scans", "fallback_full_scans")
    } == {
        "active": True,
        "reason": "active",
        "full_scans": 2,
        "delta_scans": 1,
        "fallback_full_scans": 0,
    }
    assert status["last_full_scan_at"] <= status["last_delta_scan_at"]
    assert status["last_processed_files"] == 0 and status["processed_files"] == 2
    engine.external_changes.close()
    monkeypatch.setattr(external_changes, "MAX_POPULATION", 0)
    assert await poll()
    status = workspace.provider_status()[provider]["watch"]
    assert {
        key: status[key]
        for key in ("active", "reason", "full_scans", "delta_scans", "fallback_full_scans")
    } == {
        "active": False,
        "reason": "population_limit",
        "full_scans": 3,
        "delta_scans": 1,
        "fallback_full_scans": 1,
    }
    assert status["last_full_scan_at"] >= status["last_delta_scan_at"]
    assert status["last_processed_files"] == 1 and status["processed_files"] == 3
    assert root.name not in json.dumps(status) and "one.json" not in json.dumps(status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,reason",
    [
        ("MAX_PROVIDERS", "provider_limit"),
        ("MAX_DIRECTORIES", "directory_limit"),
        ("MAX_POPULATION", "population_limit"),
        (None, "watch_backend_failed"),
    ],
)
async def test_watch_fallback_reasons_preserve_full_sweep(tracked, monkeypatch, limit, reason):
    from services.reference_graph import external_changes

    engine, workspace, provider, root, producer, _ = tracked
    (root / "one.json").write_text("{}")
    if limit is not None:
        monkeypatch.setattr(external_changes, limit, 0)
    else:

        def fail():
            raise OSError("synthetic watcher failure containing " + str(root / "private.json"))

        producer.before_ready = fail
    assert await engine.reconcile(external_mode="tracked")
    watch = workspace.provider_status()[provider]["watch"]
    assert not watch["active"] and watch["reason"] == reason
    assert watch["full_scans"] == watch["fallback_full_scans"] == 1
    assert watch["processed_files"] == 1
    assert set(workspace.external_inputs(provider)) == {"one.json"}
    assert root.name not in json.dumps(watch) and "private.json" not in json.dumps(watch)


@pytest.mark.asyncio
async def test_failed_scan_preserves_diagnostic_totals_through_recovery(tracked, monkeypatch):
    engine, workspace, provider, root, _, clock = tracked
    (root / "one.json").write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll() and await poll()
    before = workspace.provider_status()[provider]["watch"]
    clock[0] += 61

    def fail(*_args, **_kwargs):
        raise OSError("synthetic traversal failure")

    with monkeypatch.context() as patch:
        patch.setattr(ExternalSource, "files", fail)
        assert not await poll()
    failed = workspace.provider_status()[provider]["watch"]
    assert failed["full_scans"] == before["full_scans"]
    assert failed["processed_files"] == before["processed_files"]
    assert failed["last_full_scan_at"] == before["last_full_scan_at"]
    assert await poll()
    recovered = workspace.provider_status()[provider]["watch"]
    assert recovered["full_scans"] == before["full_scans"] + 1
    assert recovered["delta_scans"] == before["delta_scans"]
    assert recovered["processed_files"] == before["processed_files"] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_first", [False, True])
@pytest.mark.parametrize("interrupt", [False, True])
async def test_policy_gap_rehashes_admitted_same_stamp_content(
    tracked, monkeypatch, rule_first, interrupt
):
    import os
    from services.reference_graph.external_changes import ExternalChanges

    engine, workspace, provider, root, producer, clock = tracked
    path, denied = root / "receipt.json", root / "private-after-edit.json"
    for candidate in (path, denied):
        candidate.write_text('{"value":1}')
    unchanged = root / "unchanged.json"
    unchanged.write_text("{}")
    ignore = root / ".gitignore"
    ignore.write_text("old-name.json\n")
    stamp = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    verified = workspace.provider_status()[provider]["last_verified_at"]
    old = workspace.external_inputs(provider)
    observe = workspace.observe_external

    def observe_changed(worker, observation, **kwargs):
        assert observation.owner != old[unchanged.name]["owner"], (
            "unchanged analysis was already verified"
        )
        return observe(worker, observation, **kwargs)

    monkeypatch.setattr(workspace, "observe_external", observe_changed)
    for candidate in (path, denied):
        stat = candidate.stat()
        candidate.write_text('{"value":2}')
        os.utime(candidate, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ignore.write_text(denied.name + "\n")
    events = [(Change.modified, path), (Change.modified, denied), (Change.modified, ignore)]
    registration = engine.external_changes.registrations[provider]
    producer.send(*(reversed(events) if rule_first else events), ordered=True)
    registration.thread.join(10)
    assert not registration.thread.is_alive()
    with workspace.store._connect() as db:
        journal = db.execute(
            "SELECT path FROM graph_external_events WHERE provider=?", (provider,)
        ).fetchall()
    assert all(denied.name not in row[0] for row in journal)
    reads = []
    read = ExternalSource.read

    def record(adapter, name):
        assert name != denied.name
        reads.append(name)
        return read(adapter, name)

    monkeypatch.setattr(ExternalSource, "read", record)
    if interrupt:

        def interrupted(adapter, name):
            assert name != denied.name
            raise RuntimeError("lost process before content recovery")

        # A replacement tracker on the same durable store must inherit the obligation.
        with monkeypatch.context() as patch:
            patch.setattr(ExternalSource, "read", interrupted)
            with pytest.raises(RuntimeError, match="before content recovery"):
                await poll()
        assert workspace.provider_status()[provider]["last_verified_at"] == verified
        engine.external_changes.close()
        engine.external_changes = ExternalChanges(
            workspace, watch=producer.watch, clock=lambda: clock[0]
        )
    assert await poll()
    rows = workspace.external_inputs(provider)
    assert path.name in reads
    assert rows[path.name]["fingerprint"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert rows[unchanged.name]["fingerprint"] == old[unchanged.name]["fingerprint"]
    assert denied.name not in rows
    assert await poll()
    reads.clear()
    clock[0] += 61
    assert await poll() and not reads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loss",
    ["batch_cap", "journal_cap", "unreadable_journal", "failed_journal", "registration_window"],
)
async def test_lost_content_hint_recovers_without_persisting_names(tracked, monkeypatch, loss):
    import os
    from contextlib import contextmanager
    from threading import current_thread
    from services.reference_graph import external_changes as changes

    engine, workspace, provider, root, producer, _ = tracked
    path = root / "value.json"
    path.write_text('{"v":1}')
    before = path.stat()
    stamp = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")

    def edit():
        path.write_text('{"v":2}')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    if loss == "registration_window":
        producer.before_ready = edit
        assert await poll() and await poll()
    else:
        assert await poll() and await poll()
        old = engine.external_changes.registrations[provider]
        edit()
        if loss == "failed_journal":
            connect = workspace.store._connect

            @contextmanager
            def unavailable(**options):
                if options.get("write") and current_thread().name == "graph-external-watch":
                    raise OSError("event and gap writes both failed")
                with connect(**options) as db:
                    yield db

            with monkeypatch.context() as patch:
                patch.setattr(workspace.store, "_connect", unavailable)
                producer.send((Change.modified, path))
                old.thread.join(10)
            engine.external_changes.close()
            # No in-memory loss marker survives this restart.
            engine.external_changes = changes.ExternalChanges(workspace, watch=producer.watch)
        elif loss == "unreadable_journal":
            producer.send((Change.modified, path))
            monkeypatch.setattr(changes, "MAX_PATH_BYTES", 1)
        else:
            monkeypatch.setattr(changes, "MAX_PATHS", 1)
            events = [(Change.modified, path), (Change.modified, root / "other.json")]
            if loss == "batch_cap":
                producer.send(*events)
            else:
                for event in events:
                    producer.send(event)
            old.thread.join(10)
        assert await poll()
    assert (
        workspace.external_inputs(provider)[path.name]["fingerprint"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.asyncio
async def test_older_full_scan_cannot_acknowledge_another_producers_later_gap(tracked, monkeypatch):
    import os
    from services.reference_graph.external_changes import Batch, ExternalChanges

    engine, workspace, provider, root, producer, _ = tracked
    path = root / "value.json"
    path.write_text('{"v":1}')
    before = path.stat()
    stamp = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    owner = engine.external_changes
    registration = owner.registrations[provider]
    # A second entry owns the reconciler lease but has no handle to the first
    # entry's idle native watcher. Both use the same durable graph.
    engine.external_changes = ExternalChanges(workspace, watch=producer.watch)
    acknowledge = Batch.acknowledge

    def later_loss(batch, *args, **kwargs):
        path.write_text('{"v":2}')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        producer.send((Change.modified, path), (Change.modified, root / ".gitignore"))
        registration.thread.join(10)
        acknowledge(batch, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Batch, "acknowledge", later_loss)
            assert await engine.reconcile()
        assert await engine.reconcile()
        assert (
            workspace.external_inputs(provider)[path.name]["fingerprint"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
    finally:
        engine.external_changes.close()
        engine.external_changes = owner


@pytest.mark.asyncio
async def test_newer_same_path_event_survives_commit_and_crash_replay(tracked, monkeypatch):
    engine, workspace, provider, root, producer, _ = tracked
    path = root / "file.json"
    path.write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    registration = engine.external_changes.registrations[provider]
    original = registration.acknowledge
    path.write_text('{"value":1}')
    producer.send((Change.modified, path))

    def later_event(worker, batch, **kwargs):
        path.write_text('{"value":2}')
        producer.send((Change.modified, path))
        original(worker, batch, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(registration, "acknowledge", later_event)
        assert await poll()
    assert registration.batch().paths == (path.name,)
    assert await poll()
    assert not registration.batch().paths
    assert (
        workspace.external_inputs(provider)[path.name]["fingerprint"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    path.unlink()
    producer.send((Change.deleted, path))

    def interrupted(*args, **kwargs):
        raise RuntimeError("before acknowledgment")

    monkeypatch.setattr(registration, "acknowledge", interrupted)
    with pytest.raises(RuntimeError, match="before acknowledgment"):
        await poll()
    with workspace.store._connect() as db:
        assert (
            db.execute(
                "SELECT path FROM graph_external_events WHERE provider=?", (provider,)
            ).fetchone()[0]
            == path.name
        )
    assert await poll()
    assert not workspace.external_inputs(provider)


@pytest.mark.asyncio
async def test_topology_ignore_policy_regrant_and_disappearing_root(tracked):
    engine, workspace, provider, root, producer, _ = tracked
    visible = root / "nested"
    visible.mkdir()
    (visible / "file.json").write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    ignore = root / ".gitignore"
    ignore.write_text("nested/\n")
    producer.send((Change.added, ignore))
    assert await poll() and await poll()
    assert producer.directories[-1] == {root}
    assert not workspace.external_inputs(provider)
    visible.rename(root / "moved")
    producer.send((Change.deleted, visible), (Change.added, root / "moved"))
    assert await poll() and await poll()
    assert producer.directories[-1] == {root, root / "moved"}
    assert set(workspace.external_inputs(provider)) == {"moved/file.json"}
    workspace.set_policy({"exclude": ["moved/**"]}, expected_version=0)
    assert await poll() and await poll()
    assert producer.directories[-1] == {root}
    assert not workspace.external_inputs(provider)
    replacement = root.parent / "replacement"
    replacement.mkdir()
    (replacement / "new.json").write_text("{}")
    workspace.set_source(
        provider, {"root": str(replacement), "include": ["*.json"]}, expected_version=1
    )
    assert await poll() and await poll()
    assert producer.directories[-1] == {replacement}
    assert set(workspace.external_inputs(provider)) == {"new.json"}
    replacement.rename(root.parent / "away")
    assert not await poll()
    assert producer.closed == len(producer.directories)
    replacement.mkdir()
    (replacement / "restored.json").write_text("{}")
    assert await poll() and await poll()
    assert set(workspace.external_inputs(provider)) == {"restored.json"}

    def revoked(_config):
        raise PermissionError("revoked")

    engine.authorize_source = revoked
    assert not await poll()
    assert producer.closed == len(producer.directories)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cause", ["paths", "bytes", "directory_cap", "population_cap", "write_error", "restart"]
)
async def test_gap_recovery_bounds_and_restart(tracked, monkeypatch, cause):
    from contextlib import contextmanager
    from threading import current_thread
    from services.reference_graph import external_changes as changes

    engine, workspace, provider, root, producer, clock = tracked
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    old = engine.external_changes.registrations[provider]
    hidden_event = root / "lost.json"
    hidden_event.write_text("{}")
    if cause in {"paths", "bytes"}:
        monkeypatch.setattr(changes, "MAX_PATHS" if cause == "paths" else "MAX_PATH_BYTES", 1)
        producer.send((Change.added, root / "a.json"))
        if cause == "paths":
            producer.send((Change.added, root / "b.json"))
        assert old.stop.wait(10)
    elif cause in {"directory_cap", "population_cap"}:
        monkeypatch.setattr(
            changes, "MAX_DIRECTORIES" if cause == "directory_cap" else "MAX_POPULATION", 1
        )
        (root / "folder").mkdir()
        producer.send((Change.added, root / "folder"))
    elif cause == "write_error":
        original = workspace.store._connect

        @contextmanager
        def failing(**kwargs):
            if kwargs.get("write") and current_thread().name == "graph-external-watch":
                raise OSError("journal and gap writes unavailable")
            with original(**kwargs) as connection:
                yield connection

        with monkeypatch.context() as patch:
            patch.setattr(workspace.store, "_connect", failing)
            producer.send((Change.added, root / "event.json"))
            old.thread.join(10)
            assert old.stop.is_set()
            with original() as db:
                assert db.execute("SELECT gap FROM graph_external_watch").fetchone()[0] == 0
    else:
        engine.external_changes.close()
        engine.external_changes = changes.ExternalChanges(
            workspace, watch=producer.watch, clock=lambda: clock[0]
        )
    assert await poll()
    assert hidden_event.name in workspace.external_inputs(provider)
    if cause in {"directory_cap", "population_cap"}:
        assert not engine.external_changes.registrations
        (root / "still.json").write_text("{}")
        assert await poll()
        assert "still.json" in workspace.external_inputs(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop", ["watch_off", "shutdown", "park", "retire_credential", "source_replace"]
)
async def test_service_joins_owned_watch_handles(service_setup, tmp_path, monkeypatch, stop):
    from core.config import config
    from services.credential_lease import (
        CredentialLease,
        bind_credential_lease,
        current_credential_lease,
    )
    from services.reference_graph.external_changes import ExternalChanges
    from tests.test_reference_graph_service import Authority

    service, _, ctx = service_setup
    root = tmp_path / "settings"
    root.mkdir()
    producer = Producer()
    completed = asyncio.Event()
    lease = None
    if stop == "retire_credential":
        monkeypatch.setattr(config, "http_remote_hosted", True)
        monkeypatch.setenv(
            "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"fixture": [str(root)]})
        )
        lease = CredentialLease(Authority(), object(), "fixture")

    with bind_credential_lease(lease):
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_source(
            "external:fixture", {"root": str(root), "include": ["*.json"]}, expected_version=0
        )
        entry.engine.external_changes = ExternalChanges(entry.workspace, watch=producer.watch)
        original = entry.engine.reconcile
        attempts = []

        async def observe(**options):
            result = await original(**options)
            attempts.append(asyncio.current_task())
            completed.set()
            return result

        monkeypatch.setattr(entry.engine, "reconcile", observe)
        producer.before_ready = lambda: assert_neutral(current_credential_lease())
        service.start(entry)
        task = entry.task
        try:
            await asyncio.wait_for(completed.wait(), 10)
            assert producer.directories == [{root}]
            if stop == "watch_off":
                await service.execute(ctx, "watch", {"enabled": False})
            elif stop == "shutdown":
                await service.aclose()
            elif stop == "park":
                monkeypatch.setattr(
                    service, "_now", lambda: entry.last_used + service.IDLE_SECONDS + 1
                )
                entry.wake.set()
            elif stop == "source_replace":
                await service.execute(
                    ctx, "set_source", {"id": "fixture", "enabled": False, "expected_version": 1}
                )
            else:
                assert lease.service.owners == 1
                lease.service.revoked = True
                attempts[-1].cancel()
            if stop != "source_replace":
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 10)
            assert producer.closed == 1
            assert not entry.engine.external_changes.registrations
        finally:
            await service.aclose()
    if lease is not None:
        assert lease.service.owners == 0


def assert_neutral(lease):
    assert lease is None


@pytest.mark.asyncio
async def test_directory_modification_checks_identity_and_case_only_rename(tracked, monkeypatch):
    engine, workspace, provider, root, producer, _ = tracked
    folder = root / "folder"
    folder.mkdir()
    before = folder / "UPPER.json"
    before.write_text("{}")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    producer.send((Change.modified, folder), (Change.modified, root))
    with monkeypatch.context() as patch:
        patch.setattr(
            ExternalSource,
            "files",
            lambda **_: pytest.fail("benign directory modification enumerated the source"),
        )
        assert await poll()
    after = folder / "upper.json"
    before.rename(after)
    producer.send((Change.deleted, before), (Change.added, after))
    assert await poll()
    assert set(workspace.external_inputs(provider)) == {"folder/upper.json"}
    folder.rename(root / "old")
    folder.mkdir()
    (folder / "new.json").write_text("{}")
    producer.send((Change.modified, folder))
    assert await poll()
    assert set(workspace.external_inputs(provider)) == {"folder/new.json", "old/upper.json"}


@pytest.mark.asyncio
async def test_cancel_during_native_registration_drains_thread(tracked):
    engine, _, _, _, producer, _ = tracked
    entering, release = Event(), Event()

    def hold():
        entering.set()
        assert release.wait(10)

    producer.before_ready = hold
    attempt = asyncio.create_task(engine.reconcile(external_mode="tracked"))
    try:
        assert await asyncio.to_thread(entering.wait, 10)
        attempt.cancel()
        await asyncio.sleep(0)
        assert not attempt.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    assert producer.closed == 1
    assert not engine.external_changes.registrations


def test_concurrent_source_and_entry_retirement_both_join_the_handle():
    from threading import Thread
    from services.reference_graph.external_changes import ExternalChanges

    entered, release, second_entered, second_returned = (Event() for _ in range(4))

    class Registration:
        def close(self):
            entered.set()
            assert release.wait(10)

    tracker = ExternalChanges(None)
    tracker.registrations["external:fixture"] = Registration()
    by_source = Thread(target=tracker.close, args=("external:fixture",))

    def close_entry():
        second_entered.set()
        tracker.close()
        second_returned.set()

    by_entry = Thread(target=close_entry)
    by_source.start()
    try:
        assert entered.wait(10)
        by_entry.start()
        assert second_entered.wait(10)
        assert not second_returned.wait(0.05)
    finally:
        release.set()
        by_source.join()
        if by_entry.ident is not None:
            by_entry.join()
    assert second_returned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["modified", "deleted", "replaced"])
async def test_mixed_case_ignore_notification_invalidates_registration(tracked, operation):
    engine, workspace, provider, root, producer, _ = tracked
    folder = root / "data"
    folder.mkdir()
    (folder / "value.json").write_text("{}")
    rules = root / ".gitignore"
    rules.write_text("data/\n" if operation == "deleted" else "")
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    old = engine.external_changes.registrations[provider]
    alias = root / ".GiTiGnOrE"
    if operation == "deleted":
        rules.unlink()
        producer.send((Change.deleted, alias))
    elif operation == "replaced":
        replacement = root / ".new-ignore"
        replacement.write_text("data/\n")
        replacement.replace(rules)
        producer.send((Change.deleted, alias), (Change.added, alias))
    else:
        rules.write_text("data/\n")
        producer.send((Change.modified, alias))
    assert old.stop.is_set()
    assert await poll()
    assert (folder in producer.directories[-1]) == (operation == "deleted")
    assert ("data/value.json" in workspace.external_inputs(provider)) == (operation == "deleted")


@pytest.mark.asyncio
@pytest.mark.parametrize("full", ["periodic", "explicit", "restart"])
async def test_full_sweep_rehashes_captured_same_stamp_edit(tracked, monkeypatch, full):
    import os

    engine, workspace, provider, root, producer, clock = tracked
    path = root / "edit.json"
    path.write_text('{"value":1}')
    before = path.stat()
    stamp = ExternalSource.stamp
    # ctime need not change on a same-file Windows rewrite. Keep the actual
    # path/inode, size, and mtime checks for this case on every test platform.
    monkeypatch.setattr(
        ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], before.st_ctime_ns)
    )
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()

    def edit(value):
        path.write_text(json.dumps({"value": value}, separators=(",", ":")))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        producer.send((Change.modified, path))

    edit(2)
    captured = hashlib.sha256(path.read_bytes()).hexdigest()
    if full == "periodic":
        registration = engine.external_changes.registrations[provider]
        acknowledge = registration.acknowledge

        def newer_edit(worker, batch, *, full):
            if full:
                edit(3)
            acknowledge(worker, batch, full=full)

        monkeypatch.setattr(registration, "acknowledge", newer_edit)
        clock[0] += 61
        assert await poll()
        assert registration.batch().paths == (path.name,)
    elif full == "restart":
        engine.external_changes.close()
        assert await poll()
    else:
        assert await engine.reconcile()
    assert workspace.external_inputs(provider)[path.name]["fingerprint"] == captured
    assert await poll()
    assert (
        workspace.external_inputs(provider)[path.name]["fingerprint"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation,permanent", [(False, False), (True, False), (False, True)])
async def test_scheduler_retries_transient_credential_capacity_and_bounds_it(
    service_setup, monkeypatch, rotation, permanent
):
    from core.config import config
    from services.credential_lease import (
        CredentialLease,
        CredentialLeaseError,
        bind_credential_lease,
        current_credential_lease,
    )
    from tests.test_reference_graph_service import Authority

    service, _, ctx = service_setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(service, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(service, "PARK_FAILURES", 3)

    class Busy(Authority):
        attempts = 0

        def acquire_lease_owner(self, lease):
            self.attempts += 1
            if permanent or self.attempts == 1:
                raise CredentialLeaseError(overloaded=True)
            return super().acquire_lease_owner(lease)

    original = CredentialLease(Authority(), object(), "fixture")
    busy = CredentialLease(Busy(), object(), "fixture")
    with bind_credential_lease(original if rotation else busy):
        entry, _ = await service.resolve(ctx, "status")
    collected = asyncio.Queue()

    async def observe(**options):
        collected.put_nowait((current_credential_lease(), options))
        return True

    monkeypatch.setattr(entry.engine, "reconcile", observe)
    try:
        if rotation:
            monkeypatch.setattr(service, "POLL_SECONDS", 30)
            service.start(entry)
            assert (await asyncio.wait_for(collected.get(), watchdog_timeout()))[0] is original
            with bind_credential_lease(busy):
                await service.resolve(ctx, "status")
            monkeypatch.setattr(service, "POLL_SECONDS", 0.01)
        service.start(entry, full_external=True, force=True)
        if permanent:
            await asyncio.wait_for(entry.task, watchdog_timeout())
            assert collected.empty()
            assert busy.service.attempts == service.PARK_FAILURES
            assert entry.force and entry.full_external
        else:
            lease, options = await asyncio.wait_for(collected.get(), watchdog_timeout())
            assert lease is busy
            assert options == {"force_external": True, "external_mode": "full"}
            assert busy.service.attempts == 2
        assert original.service.owners == busy.service.owners == 0
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["periodic", "explicit", "unicode", "tracked_parent"])
async def test_reconciliation_rehashes_hint_using_filesystem_spelling(tracked, monkeypatch, mode):
    import os
    import unicodedata

    engine, workspace, provider, root, producer, clock = tracked
    stored = root / "receipts" / ("caf\u00e9.json" if mode == "unicode" else "receipt.json")
    if mode == "tracked_parent":
        reported = root / "RECEIPTS" / stored.name
    elif mode == "unicode":
        reported = stored.with_name(unicodedata.normalize("NFD", stored.name))
    else:
        reported = stored.with_name("RECEIPT.json")
    stored.parent.mkdir()
    stored.write_text('{"value":1}')
    if not reported.exists():
        # On a case-sensitive filesystem these can be distinct admitted files.
        reported.parent.mkdir(exist_ok=True)
        reported.write_text('{"value":0}')
    expected = (
        next(path for path in root.rglob("*.json") if path.samefile(reported))
        .relative_to(root)
        .as_posix()
    )
    before = reported.stat()
    stamp = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    owners = {key: row["owner"] for key, row in workspace.external_inputs(provider).items()}
    reported.write_text('{"value":2}')
    os.utime(reported, ns=(before.st_atime_ns, before.st_mtime_ns))
    producer.send((Change.modified, reported))
    read = ExternalSource.read
    reads = []

    def record(adapter, name):
        reads.append(name)
        return read(adapter, name)

    monkeypatch.setattr(ExternalSource, "read", record)
    if mode in {"periodic", "unicode"}:
        clock[0] += 61
    if mode == "explicit":
        assert await engine.reconcile()
    else:
        assert await poll()
    rows = workspace.external_inputs(provider)
    assert reads == [expected]
    assert rows[expected]["fingerprint"] == hashlib.sha256(reported.read_bytes()).hexdigest()
    assert {name: row["owner"] for name, row in rows.items()} == owners
    with workspace.store._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM graph_external_events WHERE provider=?", (provider,)
        ).fetchone()


@pytest.mark.parametrize("error,rehash", [(FileNotFoundError, False), (PermissionError, True)])
def test_spelling_probe_handles_missing_and_inaccessible_alias_without_reading_it(
    tmp_path, monkeypatch, error, rehash
):
    from services.reference_graph import external

    path = tmp_path / "receipt.json"
    path.write_text("{}")
    adapter = ExternalSource({"root": str(tmp_path), "include": ["*.json"]})
    stamp = adapter.stamp(path.name)
    probed = []

    def unavailable(candidate):
        probed.append(candidate.name)
        raise error("probe unavailable")

    monkeypatch.setattr(external, "check_owned_path", unavailable)
    matches = adapter.hint_matcher(("RECEIPT.json",))
    assert matches("unrelated/folder.json", stamp) is False
    assert not probed
    assert matches(path.name, stamp) is rehash
    assert probed == ["RECEIPT.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event", ["excluded_alias", "included_alias", "denied_spelling", "tombstone"]
)
async def test_tracked_alias_never_becomes_admission_authority(tracked, monkeypatch, event):
    import os
    from types import SimpleNamespace
    from services.reference_graph import external

    engine, workspace, provider, root, producer, _ = tracked
    canonical = root / "receipt.json"
    alias = root / "RECEIPT.json"
    canonical.write_text('{"value":1}')
    same_file = alias.exists() and alias.samefile(canonical)
    if not same_file:
        alias.write_text('{"value":0}')
    exclude = (
        [canonical.name]
        if event == "excluded_alias"
        else [alias.name]
        if event == "denied_spelling"
        else []
    )
    workspace.set_source(
        provider, {"root": str(root), "include": ["*.json"], "exclude": exclude}, expected_version=1
    )
    # Keep the actual fixture FS. Exercise the portable branch even on Windows.
    monkeypatch.setattr(external, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    stamp = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*stamp(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    before = alias.stat()
    if event == "tombstone":
        alias.unlink()
    else:
        alias.write_text('{"value":2}')
        os.utime(alias, ns=(before.st_atime_ns, before.st_mtime_ns))
    reads = []
    read = ExternalSource.read

    def observe(adapter, name):
        reads.append(name)
        return read(adapter, name)

    monkeypatch.setattr(ExternalSource, "read", observe)
    producer.send((Change.deleted if event == "tombstone" else Change.modified, alias))
    if event == "denied_spelling":
        with workspace.store._connect() as db:
            assert not db.execute(
                "SELECT 1 FROM graph_external_events WHERE path=?", (alias.name,)
            ).fetchone()
    assert await poll()
    rows = workspace.external_inputs(provider)
    expected = {p.name: p for p in root.iterdir() if p.name not in exclude}
    assert set(rows) == set(expected)
    assert all(name in expected for name in reads)
    assert all(
        rows[name]["fingerprint"] == hashlib.sha256(p.read_bytes()).hexdigest()
        for name, p in expected.items()
    )
    if same_file and event == "excluded_alias":
        assert not reads


@pytest.mark.parametrize("replace", ["file", "parent"])
def test_targeted_file_and_parent_identity_are_checked_again_before_read(tmp_path, replace):
    from services.reference_graph.store import RevisionConflict

    root = tmp_path / "source"
    parent = root / "data"
    parent.mkdir(parents=True)
    path = parent / "file.json"
    path.write_text("{}")
    adapter = ExternalSource({"root": str(root), "include": ["**/*.json"]})
    directories = {str(p): adapter.directory_identity(p) for p in (root, parent)}
    names = ["data/file.json"]
    assert adapter.changed_files(names, directories, {}, lambda: None) == names
    if replace == "file":
        replacement = parent / "replacement.json"
        replacement.write_text('{"replacement":true}')
        replacement.replace(path)
    else:
        retained = tmp_path / "old-parent"
        parent.rename(retained)
        parent.mkdir()
        (retained / path.name).rename(path)
    with pytest.raises(RevisionConflict, match="directory entry"):
        adapter.read(names[0])


def test_targeted_discovery_lists_each_affected_directory_once(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    from services.reference_graph import external

    parent = tmp_path / "data"
    parent.mkdir()
    sibling = tmp_path / "unrelated"
    sibling.mkdir()
    for name in ("a.json", "b.json"):
        (parent / name).write_text("{}")
    adapter = ExternalSource({"root": str(tmp_path), "include": ["**/*.json"]})
    directories = {str(p): adapter.directory_identity(p) for p in (tmp_path, parent, sibling)}
    listed = []

    def counted(directory):
        listed.append(directory)
        return os.scandir(directory)

    monkeypatch.setattr(external, "os", SimpleNamespace(**{**vars(os), "scandir": counted}))
    files = ["data/a.json", "data/b.json"]
    assert adapter.changed_files(files, directories, {}, lambda: None) == files
    assert listed == [tmp_path, parent]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["targeted", "directory_sweep", "lost_batch"])
@pytest.mark.parametrize("mutation", ["rename", "ancestor", "ignore", "included"])
async def test_event_content_requires_current_entry_and_ancestor_rules(
    tracked, monkeypatch, mode, mutation
):
    import os
    from contextlib import contextmanager
    from services.reference_graph import external
    from services.reference_graph.store import RevisionConflict

    engine, workspace, provider, root, producer, _ = tracked
    ancestor = root / "a"
    parent = ancestor / "b"
    parent.mkdir(parents=True)
    ignore = ancestor / ".gitignore"
    ignore.write_text("z/\n")
    canonical = parent / "receipt.json"
    denied = parent / "RECEIPT.json"
    workspace.set_source(
        provider,
        {"root": str(root), "include": ["**/*.json"], "exclude": ["**/RECEIPT.json"]},
        expected_version=1,
    )
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    canonical.write_text('{"value":1}')
    identity = canonical.stat()
    reads = []
    bytes_read = ExternalSource._read_bytes
    listed = []
    observe_entries = ExternalSource._entry_inodes

    def count_entries(adapter, directory, wanted, check):
        listed.append(directory)
        return observe_entries(adapter, directory, wanted, check)

    def count_content(adapter, stream):
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) == (identity.st_dev, identity.st_ino):
            reads.append(opened.st_size)
        return bytes_read(adapter, stream)

    reading = False
    read, descriptor = ExternalSource.read, external._regular_descriptor

    def on_read(adapter, name):
        nonlocal reading
        reading = True
        try:
            return read(adapter, name)
        finally:
            reading = False

    raced = False

    @contextmanager
    def race(path, before):
        nonlocal raced
        if reading and str(path) == str(canonical) and not raced:
            raced = True
            if mutation == "rename":
                canonical.rename(denied)
            elif mutation == "ancestor":
                retained = root.parent / "old-ancestor"
                ancestor.rename(retained)
                ancestor.mkdir()
                ignore.write_text("b/\n")
                (retained / parent.name).rename(parent)
            elif mutation == "ignore":
                ignore.write_text("b/\n")
        with descriptor(path, before) as fd:
            yield fd

    monkeypatch.setattr(ExternalSource, "_read_bytes", count_content)
    monkeypatch.setattr(ExternalSource, "_entry_inodes", count_entries)
    monkeypatch.setattr(ExternalSource, "read", on_read)
    monkeypatch.setattr(external, "_regular_descriptor", race)
    if mode == "lost_batch":
        registration = engine.external_changes.registrations[provider]
        producer.send((Change.added, canonical), (Change.modified, ignore))
        registration.thread.join(10)
    else:
        producer.send((Change.added, canonical if mode == "targeted" else denied))
    if mutation == "included":
        assert await poll()
        assert reads == [identity.st_size]
        assert set(workspace.external_inputs(provider)) == {"a/b/receipt.json"}
        assert listed == [root, ancestor, parent] * (3 if mode == "targeted" else 2)
    else:
        with pytest.raises(RevisionConflict):
            await poll()
        assert reads == []
        assert workspace.external_inputs(provider) == {}
        assert await poll()
        assert workspace.external_inputs(provider) == {}
    assert raced


@pytest.mark.asyncio
async def test_ignore_change_after_admitted_read_prevents_ready_publication(tracked, monkeypatch):
    from services.reference_graph.store import RevisionConflict

    engine, workspace, provider, root, producer, _ = tracked
    path = root / "new.json"
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    path.write_text("{}")
    producer.send((Change.added, path))
    publish = workspace.publish_events

    def change_policy(*args, **kwargs):
        if kwargs.get("provider") == provider:
            (root / ".gitignore").write_text(path.name + "\n")
        return publish(*args, **kwargs)

    monkeypatch.setattr(workspace, "publish_events", change_policy)
    with pytest.raises(RevisionConflict, match="ignore rules"):
        await poll()
    assert all(row["state"] != "ready" for row in workspace.external_inputs(provider).values())
    assert await poll()
    assert workspace.external_inputs(provider) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["json", "csv"])
@pytest.mark.parametrize("mode", ["targeted", "periodic", "explicit"])
async def test_real_short_name_hint_rehashes_only_through_admitted_spelling(
    tracked, monkeypatch, request, extension, mode
):
    import os
    from pathlib import Path

    if os.name != "nt":
        pytest.skip("Windows alternate filename fixture")
    import ctypes
    from tempfile import TemporaryDirectory

    engine, workspace, provider, _, producer, clock = tracked
    scratch = TemporaryDirectory()
    request.addfinalizer(scratch.cleanup)
    root = Path(scratch.name)
    workspace.set_source(provider, {"root": str(root), "include": ["*"]}, expected_version=1)
    path = root / ("long-receipt-filename-for-change-tracking." + extension)
    contents = ('{"v":1}', '{"v":2}') if extension == "json" else ("v\n1", "v\n2")
    path.write_text(contents[0])
    short = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), short, len(short)):
        pytest.skip("No Windows short-name lookup on fixture volume")
    alias = root / Path(short.value).name
    if alias.name.casefold() == path.name.casefold() or not alias.samefile(path):
        pytest.skip("Short names disabled on fixture volume")
    original = ExternalSource.stamp
    monkeypatch.setattr(ExternalSource, "stamp", lambda self, name: (*original(self, name)[:-1], 0))
    poll = partial(engine.reconcile, external_mode="tracked")
    assert await poll() and await poll()
    before = path.stat()
    alias.write_text(contents[1])
    os.utime(alias, ns=(before.st_atime_ns, before.st_mtime_ns))
    producer.send((Change.modified, alias))
    if mode == "periodic":
        clock[0] += 61
    assert await (engine.reconcile() if mode == "explicit" else poll())
    rows = workspace.external_inputs(provider)
    assert set(rows) == {path.name}
    assert rows[path.name]["fingerprint"] == hashlib.sha256(path.read_bytes()).hexdigest()
