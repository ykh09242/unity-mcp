"""Direct dependency publication retains bounded evidence and owns cancelled work."""

import asyncio
import threading
import weakref

import pytest

from services.reference_graph.store import Edge, RevisionConflict
from tests.test_reference_graph_reconcile import setup  # pylint: disable=unused-import
from tests.timing import await_signal, watchdog_timeout


class DependencyPages:
    def __init__(self, editor, count):
        self.editor = editor
        self.count = count
        self.version = "initial"
        self.offsets = []

    async def __call__(self, action, **params):
        if action != "dependencies":
            return await self.editor(action, **params)
        offset = params["offset"]
        self.offsets.append(offset)
        end = min(offset + params["page_size"], self.count)
        return {
            **self.editor.assets[params["guid"]],
            "snapshot": self.version,
            "fingerprint": self.version,
            "file_stamp": self.version,
            "targets": [f"{index + 2:032x}" for index in range(offset, end)],
            "next_offset": end if end < self.count else None,
        }


def evidence(store, owner):
    with store._connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT target FROM edges WHERE owner=? AND provider='unity' ORDER BY target",
                (owner,),
            )
        ]


@pytest.mark.asyncio
async def test_direct_dependency_edges_are_generated_off_loop_with_bounded_retention(
    setup, monkeypatch
):
    store, workspace, editor, engine = setup
    owner = editor.add(1, "Assets/Owner.asset")
    pages = DependencyPages(editor, 3000)
    engine._collector = pages
    live = weakref.WeakSet()
    peak = 0
    threads = set()

    class TrackedEdge(Edge):
        __slots__ = ("__weakref__",)

    def tracked_edge(*args):
        nonlocal peak
        edge = TrackedEdge(*args)
        live.add(edge)
        peak = max(peak, len(live))
        threads.add(threading.get_ident())
        return edge

    monkeypatch.setattr("services.reference_graph.reconcile.Edge", tracked_edge)
    assert await engine.reconcile()
    assert len(evidence(store, owner)) == 3000
    assert peak <= 4, "Dependency publication retained a complete Edge collection"
    assert threads and threading.get_ident() not in threads
    coverage = workspace.diagnostics(owner)["coverage"]
    assert any(row["coverage"] == "direct_asset_database_dependencies" for row in coverage)


@pytest.mark.asyncio
async def test_source_only_reparse_keeps_ready_dependency_generation_without_rebuilding_edges(
    setup, monkeypatch
):
    store, workspace, editor, engine = setup
    owner = editor.add(1, "Assets/Owner.cs", "class Owner {}")
    pages = DependencyPages(editor, 3000)
    engine._collector = pages
    assert await engine.reconcile()
    with store._connect() as connection:
        previous = tuple(
            connection.execute(
                "SELECT fingerprint,generation,state FROM observations WHERE owner=? AND provider='unity'",
                (owner,),
            ).fetchone()
        )
    assert workspace.acquire(engine.worker)
    workspace.dirty_compilation(engine.worker, [owner[6:]])
    workspace.release(engine.worker)
    editor.calls.clear()

    def unexpected_edge(*_args):
        pytest.fail("An unchanged ready dependency publication was rebuilt")

    monkeypatch.setattr("services.reference_graph.reconcile.Edge", unexpected_edge)
    assert await engine.reconcile()
    assert any(action == "source" for action, _ in editor.calls)
    assert len(evidence(store, owner)) == 3000
    with store._connect() as connection:
        current = tuple(
            connection.execute(
                "SELECT fingerprint,generation,state FROM observations WHERE owner=? AND provider='unity'",
                (owner,),
            ).fetchone()
        )
    assert current == previous


@pytest.mark.asyncio
async def test_cancelled_direct_publication_drains_worker_before_releasing_lease(
    setup, monkeypatch
):
    store, workspace, editor, engine = setup
    owner = editor.add(1, "Assets/Owner.asset")
    pages = DependencyPages(editor, 1)
    engine._collector = pages
    assert await engine.reconcile()
    previous = evidence(store, owner)
    pages.count, pages.version = 3000, "updated"
    pages.offsets.clear()
    editor.event("Assets/Owner.asset")
    entered = asyncio.Event()
    release = threading.Event()
    expired = threading.Event()
    loop = asyncio.get_running_loop()
    constructed = 0

    def paused_edge(*args):
        nonlocal constructed
        constructed += 1
        if constructed == 600:
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(watchdog_timeout(5)):
                expired.set()
        return Edge(*args)

    monkeypatch.setattr("services.reference_graph.reconcile.Edge", paused_edge)
    task = asyncio.create_task(engine.reconcile())
    try:
        await await_signal(entered, "dependency construction", producers=(task,))
        assert not expired.is_set(), "Dependency construction blocked the event loop"
        assert evidence(store, owner) == previous
        with store._connect() as connection:
            assert connection.execute("SELECT count(*) FROM graph_staging").fetchone()[0] >= 512
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Cancellation abandoned its running dependency worker"
        assert not workspace.acquire("replacement")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert constructed == 600
        assert evidence(store, owner) == previous
        with store._connect() as connection:
            assert connection.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM graph_worker").fetchone()[0] == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr("services.reference_graph.reconcile.Edge", Edge)
    pages.offsets.clear()
    assert await engine.reconcile()
    assert pages.offsets == [0, 1000, 2000]
    assert len(evidence(store, owner)) == 3000


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["snapshot", "fingerprint", "interrupted"])
async def test_incomplete_dependency_pages_preserve_publication_and_retry_from_zero(setup, failure):
    store, _, editor, engine = setup
    owner = editor.add(1, "Assets/Owner.asset")
    pages = DependencyPages(editor, 1)
    engine._collector = pages
    assert await engine.reconcile()
    previous = evidence(store, owner)
    pages.count, pages.version = 2001, "updated"
    editor.event("Assets/Owner.asset")

    async def interrupted(action, **params):
        page = await pages(action, **params)
        if action == "dependencies" and params["offset"] == 1000:
            if failure == "interrupted":
                raise OSError("dependency page unavailable")
            page[failure] = "changed"
        return page

    engine._collector = interrupted
    if failure == "interrupted":
        assert not await engine.reconcile()
    else:
        with pytest.raises(RevisionConflict, match="changed"):
            await engine.reconcile()
    assert evidence(store, owner) == previous
    pages.offsets.clear()
    engine._collector = pages
    editor.event("Assets/Owner.asset")
    assert await engine.reconcile()
    assert pages.offsets == [0, 1000, 2000]
    assert len(evidence(store, owner)) == 2001
