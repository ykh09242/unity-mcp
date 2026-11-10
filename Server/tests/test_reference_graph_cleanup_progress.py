"""Synthetic histories exercise bounded traversal completion and cleanup selection."""

from contextlib import contextmanager

import pytest

from services.reference_graph.operations import cleanup
from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace


@contextmanager
def sql_steps(store, monkeypatch):
    counts = [0]
    connect = store._connect

    def progress():
        counts[0] += 1
        return 0

    @contextmanager
    def measured(*args, **kwargs):
        with connect(*args, **kwargs) as db:
            db.set_progress_handler(progress, 1)
            try:
                yield db
            finally:
                db.set_progress_handler(None, 0)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", measured)
        yield counts


@pytest.fixture
def workspace(tmp_path):
    result = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    result.queries()
    return result


@pytest.mark.parametrize("count", [1000, 10000])
def test_terminal_path_defers_scratch_reclamation(workspace, monkeypatch, count):
    store, queries = workspace.store, workspace.queries()
    store.put_declaration("first", "root", Edge("middle", "manual"), expected_version=0)
    store.put_declaration("last", "middle", Edge("destination", "manual"), expected_version=0)
    first = queries.path("root", "destination", direction="outgoing", limit=1)
    with store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO graph_query_nodes VALUES (?,?,0,NULL)",
            ((first["query_id"], f"scratch{index}") for index in range(count)),
        )
        db.executemany(
            "INSERT INTO graph_query_queue(job,node) VALUES (?,?)",
            ((first["query_id"], f"scratch{index}") for index in range(count)),
        )
    cursor = first["next_cursor"]
    with sql_steps(store, monkeypatch) as steps:
        route = queries.path("root", "destination", direction="outgoing", limit=1, cursor=cursor)
        terminal = queries.path(
            "root", "destination", direction="outgoing", limit=1, cursor=route["next_cursor"]
        )
    print(f"terminal count={count} sql_steps={steps[0]}")
    assert terminal["path_found"] and terminal["next_cursor"] is None
    assert steps[0] < 10000
    assert (
        queries.path(
            "root", "destination", direction="outgoing", limit=1, cursor=route["next_cursor"]
        )
        == terminal
    )
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_query_nodes").fetchone()[0] >= count
        assert db.execute("SELECT count(*) FROM graph_query_queue").fetchone()[0] >= count
    result = cleanup(workspace, before=10**12, limit=10)
    assert result["query_scratch_rows_removed"] == 10 and result["more"]


def seed_jobs(workspace, count, *, finished):
    with workspace.store._connect(write=True) as db:
        revision = db.execute("SELECT revision FROM graph_meta").fetchone()[0]
        db.executemany(
            "INSERT INTO graph_queries(id,kind,signature,revision,sequence,last,used,finished) VALUES (?,'impact','',?,0,'',?,?)",
            ((f"job{index:05}", revision, index + 1, finished) for index in range(count)),
        )


@pytest.mark.parametrize("count", [1000, 10000])
@pytest.mark.parametrize("finished", [0, 1])
def test_cleanup_candidate_sql_work_is_bounded(workspace, monkeypatch, count, finished):
    seed_jobs(workspace, count, finished=finished)
    with sql_steps(workspace.store, monkeypatch) as steps:
        result = cleanup(workspace, before=10**12, limit=10)
    print(f"cleanup count={count} finished={finished} sql_steps={steps[0]}")
    assert steps[0] < 5000
    assert result["completed_queries_removed"] == 10 * finished
    assert result["more"]


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_cleanup_scans_mixed_history_and_preserves_active_jobs(workspace, same_timestamp):
    seed_jobs(workspace, 25, finished=0)
    with workspace.store._connect(write=True) as db:
        if same_timestamp:
            db.execute("UPDATE graph_queries SET used=1")
        db.execute("UPDATE graph_queries SET finished=1 WHERE id='job00024'")
        db.execute("UPDATE graph_queries SET abandoned=1 WHERE id='job00022'")
        db.execute("UPDATE graph_queries SET revision=revision-1 WHERE id='job00023'")
        db.execute("UPDATE graph_queries SET finished=1,used=1000 WHERE id='job00021'")
    total = 0
    for _ in range(10):
        result = cleanup(workspace, before=100, limit=4)
        total += result["completed_queries_removed"] + result["obsolete_queries_removed"]
        if not result["more"]:
            break
    else:
        pytest.fail("Mixed-history cleanup made no progress")
    assert total == 3
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_queries").fetchone()[0] == 22


def test_cleanup_resumes_partial_job_after_reopening_and_expanding_cutoff(workspace):
    seed_jobs(workspace, 8, finished=0)
    with workspace.store._connect(write=True) as db:
        db.execute("UPDATE graph_queries SET finished=1 WHERE id IN ('job00003','job00007')")
        db.executemany(
            "INSERT INTO graph_query_nodes VALUES ('job00003',?,0,NULL)",
            ((str(index),) for index in range(7)),
        )
    assert cleanup(workspace, before=6, limit=2)["more"]
    partial = cleanup(workspace, before=6, limit=2)
    assert partial["query_scratch_rows_removed"] == 2 and partial["more"]
    workspace = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    removed, scratch = 0, partial["query_scratch_rows_removed"]
    for _ in range(10):
        result = cleanup(workspace, before=100, limit=2)
        removed += result["completed_queries_removed"]
        scratch += result["query_scratch_rows_removed"]
        assert result["query_scratch_rows_removed"] <= 2
        if not result["more"]:
            break
    else:
        pytest.fail("Partial cleanup did not resume after reopening")
    assert scratch == 7 and removed == 2
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_queries").fetchone()[0] == 6


@pytest.mark.parametrize("change", ["insert", "finish", "abandon", "move", "revision", "cutoff"])
def test_cleanup_revisits_newly_eligible_rows_behind_scan(workspace, change):
    seed_jobs(workspace, 15, finished=0)
    assert cleanup(workspace, before=100, limit=3)["more"]
    with workspace.store._connect(write=True) as db:
        if change == "insert":
            db.execute(
                "INSERT INTO graph_queries(id,kind,signature,revision,sequence,last,used,finished) VALUES ('new','impact','',0,0,'',0,1)"
            )
            target = "new"
        else:
            target = "job00000"
            if change == "revision":
                db.execute("UPDATE graph_meta SET revision=revision+1")
            elif change == "cutoff":
                db.execute("UPDATE graph_queries SET finished=1 WHERE id='job00000'")
            elif change == "move":
                db.execute("UPDATE graph_queries SET finished=1,used=0 WHERE id='job00014'")
                target = "job00014"
            else:
                column = "finished" if change == "finish" else "abandoned"
                db.execute(f"UPDATE graph_queries SET {column}=1 WHERE id='job00000'")
    for _ in range(10):
        result = cleanup(workspace, before=50 if change == "cutoff" else 100, limit=3)
        if not result["more"]:
            break
    else:
        pytest.fail("Cleanup did not complete after changing a prior candidate")
    with workspace.store._connect() as db:
        assert not db.execute("SELECT 1 FROM graph_queries WHERE id=?", (target,)).fetchone()


def test_query_schema_migration_preserves_replay_and_adds_bounded_cleanup(workspace, monkeypatch):
    queries, store = workspace.queries(), workspace.store
    store.put_declaration("edge", "root", Edge("leaf", "manual"), expected_version=0)
    first = queries.impact("root", direction="outgoing", limit=1)
    with store._connect(write=True) as db:
        for row in list(
            db.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'graph_query_cleanup%'")
        ):
            db.execute(f'DROP {row["type"]} "{row["name"]}"')
        db.execute("UPDATE graph_queries_schema SET version=3")
    reopened = GraphQueries(store)
    resumed = reopened.impact("root", direction="outgoing", limit=1, cursor=first["next_cursor"])
    assert (
        reopened.impact("root", direction="outgoing", limit=1, cursor=first["next_cursor"])
        == resumed
    )
    seed_jobs(workspace, 10000, finished=0)
    with sql_steps(store, monkeypatch) as steps:
        result = cleanup(workspace, before=10**12, limit=10)
    assert result["more"] and steps[0] < 5000
