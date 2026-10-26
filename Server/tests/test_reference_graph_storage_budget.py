"""Real process admission, crash recovery and SQLite spill-pressure regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.storage_budget import StorageBudgetError
from services.reference_graph.storage_budget import WriteMeter
from services.reference_graph import storage_budget


def test_crashed_reservation_and_concurrent_processes_cannot_bypass_scope_admission(
    tmp_path, monkeypatch
):
    # Given a process that exits after durable reservation but before database creation.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPES", "3")
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_SCOPES", "2")
    crashed = GraphScope("user", "crashed", "project")
    path = tmp_path / (crashed.key + ".sqlite")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import os,sys; "
            "from services.reference_graph.storage_budget import StorageBudget; "
            "StorageBudget(Path(sys.argv[1]),'user'); os._exit(23)",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23, result.stderr
    assert not path.exists()
    script = """
from pathlib import Path
import sys
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.storage_budget import StorageBudgetError
try:
    GraphStore.open(Path(sys.argv[1]), GraphScope('user', sys.argv[2], 'project'))
    print('accepted')
except StorageBudgetError:
    print('budget')
"""

    def open_scope(index):
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(index)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert child.returncode == 0, child.stderr
        return child.stdout.strip()

    # When independent processes rotate scope identities, then the crash still consumes its slot.
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(open_scope, range(8)))
    assert outcomes.count("accepted") == 1
    assert outcomes.count("budget") == 7
    assert GraphStore.open(tmp_path, crashed).status()["backend"] == "sqlite"
    GraphStore.open(tmp_path, GraphScope("other", "editor", "project"))
    with pytest.raises(StorageBudgetError):
        GraphStore.open(tmp_path, GraphScope("third", "editor", "project"))


def test_unknown_ledger_is_preserved_and_linked_ledger_is_rejected(tmp_path):
    # Given an unrelated database at the operator ledger path.
    ledger = tmp_path / ".storage.sqlite"
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("CREATE TABLE customer_data(value)")
        db.commit()
    before = ledger.read_bytes()

    # When a graph opens, then unrelated contents remain untouched.
    with pytest.raises(ValueError, match="not owned"):
        GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    assert ledger.read_bytes() == before
    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    os.link(ledger, linked_root / ".storage.sqlite")
    with pytest.raises(ValueError, match="regular"):
        GraphStore.open(linked_root, GraphScope("user", "editor", "project"))
    assert ledger.read_bytes() == before


def test_byte_reservations_apply_to_user_and_root_before_creating_files(tmp_path, monkeypatch):
    # Given 256 KiB main reservations (2.5 MiB including sidecar allowance),
    # 5 MiB per user and 49 MiB root including its 33 MiB ledger allowance.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES", str(256 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_BYTES", str(5 * 1024 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_BYTES", str(49 * 1024 * 1024))
    for user in ("first", "second", "third"):
        for editor in ("one", "two"):
            GraphStore.open(tmp_path, GraphScope(user, editor, "project"))
        with pytest.raises(StorageBudgetError):
            GraphStore.open(tmp_path, GraphScope(user, "extra", "project"))

    # When another principal requests admission, then the shared byte budget also refuses it.
    with pytest.raises(StorageBudgetError):
        GraphStore.open(tmp_path, GraphScope("fourth", "editor", "project"))
    assert len(list(tmp_path.glob("[!.]*.sqlite"))) == 6
    assert (
        GraphStore.open(tmp_path, GraphScope("first", "one", "project")).status()["backend"]
        == "sqlite"
    )


def test_storage_budget_does_not_read_the_collection_transfer_budget(tmp_path, monkeypatch):
    # Given independently configured transfer and persistent storage budgets.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_MAX_BYTES", "1")
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_BYTES", str(47 * 1024 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES", str(1024 * 1024))

    # When scopes open, storage admission uses its own 33 MiB + 2*7 MiB allowance.
    for editor in ("one", "two"):
        GraphStore.open(tmp_path, GraphScope("user", editor, "project"))
    with pytest.raises(StorageBudgetError):
        GraphStore.open(tmp_path, GraphScope("user", "three", "project"))
    assert os.environ["UNITY_MCP_REFERENCE_GRAPH_MAX_BYTES"] == "1"


def test_existing_queries_and_new_admission_do_not_rescan_the_state_directory(
    tmp_path, monkeypatch
):
    # Given an initialized durable ledger and a saved declaration.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)

    def unexpected_inventory(_):
        pytest.fail("An initialized ledger must not scan the state directory again")

    # When querying/reopening/admitting another scope, then no cache-wide inventory is performed.
    monkeypatch.setattr(Path, "iterdir", unexpected_inventory)
    assert store.query("owner")["edges"][0]["target"] == "target"
    assert GraphStore.open(tmp_path, store.scope).status()["backend"] == "sqlite"
    GraphStore.open(tmp_path, GraphScope("user", "another", "project"))


@pytest.mark.parametrize("interval", [1000, 5000, 10000])
def test_many_short_statements_cannot_commit_under_pinned_reader_pressure(
    tmp_path, monkeypatch, interval
):
    # Given saved graph evidence, a small main allowance, and a pinned reader.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES", str(1024 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPE_BYTES", str(1024 * 1024))
    monkeypatch.setattr(storage_budget, "PROGRESS_INSTRUCTIONS", interval)
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)
    queries = GraphQueries(store)
    saved = queries.snapshot()["snapshot"]["id"]
    with store._connect(write=True) as db:
        db.execute("CREATE TABLE spill_fixture(id INTEGER PRIMARY KEY,payload BLOB)")
        db.execute("INSERT INTO spill_fixture VALUES (1,?)", (b"a" * 200_000,))
    with closing(sqlite3.connect(store.path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM spill_fixture").fetchall()
        for index in range(100):
            try:
                with store._connect(write=True) as db:
                    db.execute(
                        "UPDATE spill_fixture SET payload=? WHERE id=1",
                        (bytes([index % 251]) * 200_000,),
                    )
            except StorageBudgetError:
                break
        else:
            pytest.fail("Pinned reader did not cause write backpressure")
        with store._connect() as db:
            prior = db.execute("SELECT payload FROM spill_fixture").fetchone()[0]

        # When many short updates run in one transaction, pre-commit sampling rejects it.
        with pytest.raises(StorageBudgetError, match="sidecar budget"):
            with store._connect(write=True) as db:
                db.execute("PRAGMA cache_size=-16")
                db.executemany(
                    "UPDATE spill_fixture SET payload=? WHERE id=1",
                    ((bytes([index % 251]) * 200_000,) for index in range(500)),
                )
        assert store.query("owner")["edges"][0]["target"] == "target"
        assert queries.diff(saved, saved)["changes"] == []
        with store._connect() as db:
            assert db.execute("SELECT payload FROM spill_fixture").fetchone()[0] == prior

    # Then reader drain/restart permits retry without losing the previous declaration/snapshot.
    reopened = GraphStore.open(tmp_path, store.scope)
    assert (
        reopened.put_declaration(
            "saved", "owner", Edge("recovered", "declared"), expected_version=1
        )
        == 2
    )
    assert GraphQueries(reopened).diff(saved, saved)["changes"] == []


@pytest.mark.parametrize("interval", [1000, 5000, 10000])
def test_progress_interruption_disables_callback_before_rollback(tmp_path, monkeypatch, interval):
    # Given a real transaction that must restore its earlier deletion on interruption.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    monkeypatch.setattr(storage_budget, "PROGRESS_INSTRUCTIONS", interval)
    store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)
    with store._connect(write=True) as db:
        db.execute("CREATE TABLE interruption_fixture(value INTEGER)")
    calls = []

    def interrupt(meter):
        calls.append(True)
        meter.exceeded = True
        return 1

    # When the VM callback detects pressure, then rollback restores the committed graph.
    with monkeypatch.context() as patch:
        patch.setattr(WriteMeter, "sample", interrupt)
        with pytest.raises(StorageBudgetError, match="sidecar budget"):
            with store._connect(write=True) as db:
                db.execute("DELETE FROM edges")
                db.execute(
                    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000) INSERT INTO interruption_fixture SELECT x FROM n"
                )
    assert calls
    assert store.query("owner")["edges"][0]["target"] == "target"
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM interruption_fixture").fetchone()[0] == 0


@pytest.mark.parametrize("interval", [1000, 5000, 10000])
def test_commit_flush_observation_does_not_report_committed_work_as_failed(
    tmp_path, monkeypatch, interval
):
    # Given a small write whose spill-free body fits before COMMIT.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    monkeypatch.setattr(storage_budget, "PROGRESS_INSTRUCTIONS", interval)
    samples = []

    def commit_flush(meter):
        samples.append(True)
        if len(samples) == 2:
            meter.exceeded = True
        return int(meter.exceeded)

    # When the post-COMMIT sample observes an overshoot, success remains unambiguous.
    with monkeypatch.context() as patch:
        patch.setattr(WriteMeter, "sample", commit_flush)
        assert (
            store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)
            == 1
        )
    assert len(samples) == 2
    assert store.query("owner")["edges"][0]["target"] == "target"
