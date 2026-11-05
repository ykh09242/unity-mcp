"""Path catalogs migrate once without rewriting evidence or rescanning on reopen."""

from contextlib import contextmanager

import pytest

from services.reference_graph.store import Edge, GraphScope, GraphStore, Observation
from services.reference_graph.workspace import GraphWorkspace


def workspace_with_history(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    assert workspace.acquire("worker")
    workspace.record_page(
        "worker",
        [
            {"guid": "a" * 32, "path": "Assets/Visible.asset", "keys": []},
            {"guid": "b" * 32, "path": "Assets/Private/Hidden.asset", "keys": []},
        ],
        {"phase": "collect", "after": 17},
        "scan",
    )
    workspace.record_external(
        "worker", "external:source", "external:owner", "items.json", "hash", "stamp", "key", "scan"
    )
    generation = workspace.store.observe_batch([Observation("owner", "code", "hash")])[0]
    workspace.store.publish("owner", "code", generation, [Edge("target", "load")])
    workspace.store.put_declaration("manual", "owner", Edge("other", "manual"), expected_version=0)
    with workspace.store._connect(write=True) as connection:
        connection.execute(
            "INSERT INTO graph_external_paths VALUES ('external:source','retained','retained.json')"
        )
        connection.execute("UPDATE graph_workspace SET version=11")
    return workspace


def persisted_rows(store, *, include_paths=False):
    with store._connect() as connection:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if row[0] != "graph_workspace"
            and (include_paths or row[0] not in {"graph_asset_paths", "graph_external_paths"})
        ]
        return {
            table: sorted(
                (tuple(row) for row in connection.execute('SELECT * FROM "' + table + '"')),
                key=repr,
            )
            for table in tables
        }


@pytest.mark.parametrize("missing_tables", [False, True])
def test_version_eleven_path_migration_preserves_history_and_existing_private_paths(
    tmp_path, missing_tables
):
    workspace = workspace_with_history(tmp_path)
    with workspace.store._connect(write=True) as connection:
        if missing_tables:
            connection.execute("DROP TABLE graph_asset_paths")
            connection.execute("DROP TABLE graph_external_paths")
        else:
            connection.execute("DELETE FROM graph_asset_paths WHERE guid=?", ("a" * 32,))
            connection.execute("DELETE FROM graph_external_paths WHERE owner='external:owner'")
    before = persisted_rows(workspace.store)
    restored = GraphWorkspace(workspace.store)
    assert persisted_rows(restored.store) == before
    with restored.store._connect() as connection:
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 12
        assert tuple(
            connection.execute(
                "SELECT path,seen FROM graph_asset_paths WHERE guid=?", ("a" * 32,)
            ).fetchone()
        ) == ("Assets/Visible.asset", "scan")
        assert (
            connection.execute(
                "SELECT path FROM graph_external_paths WHERE owner='external:owner'"
            ).fetchone()[0]
            == "items.json"
        )
        if not missing_tables:
            assert (
                connection.execute(
                    "SELECT path FROM graph_asset_paths WHERE guid=?", ("b" * 32,)
                ).fetchone()[0]
                == "Assets/Private/Hidden.asset"
            )
            assert (
                connection.execute(
                    "SELECT path FROM graph_external_paths WHERE owner='retained'"
                ).fetchone()[0]
                == "retained.json"
            )
    complete = persisted_rows(restored.store, include_paths=True)
    GraphWorkspace(restored.store)
    assert persisted_rows(restored.store, include_paths=True) == complete


@pytest.mark.parametrize("missing_tables", [False, True])
def test_interrupted_path_migration_rolls_back_both_backfills_and_version(
    tmp_path, monkeypatch, missing_tables
):
    workspace = workspace_with_history(tmp_path)
    connect = workspace.store._connect
    with connect(write=True) as connection:
        if missing_tables:
            connection.execute("DROP TABLE graph_asset_paths")
            connection.execute("DROP TABLE graph_external_paths")
        else:
            connection.execute("DELETE FROM graph_asset_paths WHERE guid=?", ("a" * 32,))
            connection.execute("DELETE FROM graph_external_paths WHERE owner='external:owner'")
    before = persisted_rows(workspace.store, include_paths=True)
    inserted_before_failure = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            if sql.startswith("INSERT OR IGNORE INTO graph_external_paths SELECT"):
                inserted_before_failure.extend(
                    row[0]
                    for row in self.connection.execute(
                        "SELECT guid FROM graph_asset_paths WHERE guid=?", ("a" * 32,)
                    )
                )
                raise OSError("interrupted between path backfills")
            return self.connection.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def interrupted(**kwargs):
        with connect(**kwargs) as connection:
            yield Connection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(workspace.store, "_connect", interrupted)
        with pytest.raises(OSError, match="between path backfills"):
            GraphWorkspace(workspace.store)
    assert inserted_before_failure == ["a" * 32]
    assert persisted_rows(workspace.store, include_paths=True) == before
    with connect() as connection:
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 11
    GraphWorkspace(workspace.store)
    with connect() as connection:
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 12
        expected_count = 1 if missing_tables else 2
        assert (
            connection.execute("SELECT count(*) FROM graph_asset_paths").fetchone()[0]
            == expected_count
        )
        assert (
            connection.execute("SELECT count(*) FROM graph_external_paths").fetchone()[0]
            == expected_count
        )


def test_current_workspace_reopens_do_not_scan_asset_or_external_backfills(tmp_path, monkeypatch):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    connect = workspace.store._connect
    counts, traces = [], []

    def measure_open():
        steps = 0
        statements = []

        def tick():
            nonlocal steps
            steps += 1
            return 0

        @contextmanager
        def measured(**kwargs):
            with connect(**kwargs) as connection:
                connection.set_trace_callback(statements.append)
                connection.set_progress_handler(tick, 1)
                try:
                    yield connection
                finally:
                    connection.set_progress_handler(None, 0)

        with monkeypatch.context() as patch:
            patch.setattr(workspace.store, "_connect", measured)
            GraphWorkspace(workspace.store)
        counts.append(steps)
        traces.extend(statements)

    measure_open()
    assert workspace.acquire("worker")
    workspace.record_page(
        "worker",
        [
            {"guid": f"{index:032x}", "path": f"Assets/{index}.asset", "keys": []}
            for index in range(3000)
        ],
        {},
        "scan",
    )
    workspace.record_external_many(
        "worker",
        [
            ("external:source", f"owner:{index}", f"{index}.json", "hash", "stamp", "key", "scan")
            for index in range(3000)
        ],
    )
    measure_open()
    measure_open()
    print(f"workspace reopen VM steps (empty, 6000 rows, repeat): {counts}")
    assert max(counts[1:]) <= counts[0] + 200, counts
    assert not any(
        "INSERT" in statement.upper()
        and "SELECT" in statement.upper()
        and ("graph_assets" in statement or "graph_external" in statement)
        for statement in traces
    ), traces
