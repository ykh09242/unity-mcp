"""Census retirement remains atomic while bounded Python and SQL work is cancelled."""

import asyncio
from contextlib import contextmanager
import threading

import pytest

from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import Edge, GraphScope, GraphStore, Observation
from services.reference_graph.workspace import GraphWorkspace


async def allowed():
    pass


async def empty_inventory(action, **params):
    assert action == "inventory"
    return {
        "assets": [],
        "snapshot": "snapshot",
        "epoch": "epoch",
        "revision": 7,
        "next_offset": None,
    }


def populated_workspace(tmp_path, count=600):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    engine = Reconciler(workspace, empty_inventory, allowed)
    assert workspace.acquire(engine.worker)
    workspace.record_page(
        engine.worker,
        [
            {"guid": f"{number:032x}", "path": f"Assets/{number}.asset", "keys": []}
            for number in range(count)
        ],
        {"after": 0},
        "old",
    )
    owner = "asset:" + "0" * 32
    generation = workspace.store.observe_batch([Observation(owner, "unity", "old")])[0]
    workspace.store.publish(owner, "unity", generation, [Edge("target", "dependency")])
    with workspace._write(engine.worker) as connection:
        workspace._set(connection, "change_page", {"after": 0, "pending": ["old"]})
    engine.progress = workspace.progress()
    return workspace, engine


def snapshot(workspace):
    with workspace.store._connect() as connection:
        return {
            "assets": [
                tuple(row) for row in connection.execute("SELECT * FROM graph_assets ORDER BY guid")
            ],
            "paths": [
                tuple(row)
                for row in connection.execute("SELECT * FROM graph_asset_paths ORDER BY guid")
            ],
            "nodes": [tuple(row) for row in connection.execute("SELECT * FROM nodes ORDER BY id")],
            "observations": [
                tuple(row)
                for row in connection.execute(
                    "SELECT owner,provider,generation,state FROM observations ORDER BY owner,provider"
                )
            ],
            "edges": [
                tuple(row)
                for row in connection.execute(
                    "SELECT owner,provider,target FROM edges ORDER BY owner,provider,target"
                )
            ],
            "state": [
                tuple(row) for row in connection.execute("SELECT * FROM graph_state ORDER BY key")
            ],
            "revision": workspace.store.revision(),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_census_cancellation_during_deletion_drains_rolls_back_and_retries(
    tmp_path, monkeypatch, repeat_cancel
):
    workspace, engine = populated_workspace(tmp_path / "cancelled")
    baseline, uninterrupted = populated_workspace(tmp_path / "baseline")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    before = []
    deleted = []
    original_delete, original_finish = workspace._delete, workspace.finish_census

    def pause_after_delete(connection, owner):
        original_delete(connection, owner)
        deleted.append(owner)
        if len(deleted) == 1:
            entered.set()
            assert release.wait(10), "test did not release the census worker"

    def finish(*args, **kwargs):
        before.append(snapshot(workspace))
        try:
            return original_finish(*args, **kwargs)
        finally:
            finished.set()

    with monkeypatch.context() as patch:
        patch.setattr(workspace, "_delete", pause_after_delete)
        patch.setattr(workspace, "finish_census", finish)
        task = asyncio.create_task(engine._census({"revision": 7}, force=True))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            if repeat_cancel:
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            assert not finished.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    assert finished.is_set()
    assert len(deleted) < 600
    assert snapshot(workspace) == before[0]
    assert workspace.progress()["census"] is not None
    engine.stopped.clear()
    await engine._census({"revision": 7}, force=True)
    await uninterrupted._census({"revision": 7}, force=True)
    assert snapshot(workspace) == snapshot(baseline)


@pytest.mark.asyncio
async def test_census_stop_inside_sql_delete_rolls_back_checkpoint(tmp_path, monkeypatch):
    workspace, engine = populated_workspace(tmp_path, count=1200)
    connect = workspace.store._connect
    deleted = []
    before = []
    original_finish = workspace.finish_census

    def stop_after_row():
        deleted.append(1)
        if len(deleted) == 20:
            engine.stopped.set()
        return 0

    @contextmanager
    def instrument(**kwargs):
        with connect(**kwargs) as connection:
            if kwargs.get("write"):
                connection.create_function("stop_after_row", 0, stop_after_row)
                connection.execute(
                    "CREATE TEMP TRIGGER interrupt_census AFTER DELETE ON graph_assets "
                    "BEGIN SELECT stop_after_row(); END"
                )
            yield connection

    def finish(*args, **kwargs):
        before.append(snapshot(workspace))
        return original_finish(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(workspace.store, "_connect", instrument)
        patch.setattr(workspace, "finish_census", finish)
        with pytest.raises(InterruptedError, match="cancelled"):
            await engine._census({"revision": 7}, force=True)
    assert 20 <= len(deleted) < 1200
    assert snapshot(workspace) == before[0]
    engine.stopped.clear()
    await engine._census({"revision": 7}, force=True)
    assert not workspace.catalog()
    assert workspace.progress()["initialized"]


def test_census_candidate_queries_return_only_bounded_pages(tmp_path, monkeypatch):
    workspace, engine = populated_workspace(tmp_path, count=1200)
    write = workspace._write
    page_sizes = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __iter__(self):
            count = 0
            for row in self.cursor:
                count += 1
                assert count <= 256, "census candidate query exceeded its bounded page"
                yield row
            page_sizes.append(count)

        def fetchall(self):
            return list(self)

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if sql.startswith("SELECT guid FROM graph_assets WHERE seen"):
                return Cursor(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def instrument(worker):
        with write(worker) as connection:
            yield Connection(connection)

    monkeypatch.setattr(workspace, "_write", instrument)
    workspace.finish_census(engine.worker, "new", {"initialized": True})
    assert max(page_sizes) == 256
    assert not workspace.catalog()
