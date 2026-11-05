"""Offline scope archival preserves history and makes interruption recoverable."""

from contextlib import closing
import gc
import importlib.util
import json
from pathlib import Path
import sqlite3
import os
import subprocess
import sys
import time

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph.restart import open_workspace
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.storage_budget import StorageBudgetError


def _maintenance():
    assert importlib.util.find_spec("services.reference_graph.maintenance"), (
        "Reference graph scopes need an explicit inspect/archive maintenance entry point"
    )
    from services.reference_graph import maintenance

    return maintenance


def _seed(root, editor):
    scope = GraphScope("owner", editor, "project")
    workspace = open_workspace(root, scope)
    workspace.set_policy(
        {"exclude": ["Assets/Private/**"]}, expected_version=workspace.policy()["version"]
    )
    workspace.store.put_declaration("saved", "owner", Edge("target", "manual"), expected_version=0)
    with workspace.store._connect(write=True) as db:
        db.execute("UPDATE graph_admission SET ready=1 WHERE id=1")
    snapshot = GraphQueries(workspace.store).snapshot()["snapshot"]["id"]
    del workspace
    gc.collect()
    return scope, snapshot


def _count(root):
    with closing(sqlite3.connect(root / ".storage.sqlite")) as db:
        return db.execute("SELECT count(*) FROM scopes").fetchone()[0]


def test_turnover_archival_recovers_quota_and_preserves_policies_and_history(tmp_path, monkeypatch):
    maintenance = _maintenance()
    root = tmp_path / "state"
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_SCOPES", "2")
    old, snapshot = _seed(root, "old")
    retained, retained_snapshot = _seed(root, "retained")
    with pytest.raises(StorageBudgetError):
        GraphStore.open(root, GraphScope("owner", "new", "project"))
    gc.collect()
    unknown = root / "operator-notes.txt"
    unknown.write_text("preserve", encoding="utf-8")
    before = (root / (old.key + ".sqlite")).read_bytes()
    destination = tmp_path / "archive"
    report = maintenance.archive_scope(root, old.key, destination, all_servers_stopped=True)
    assert report["outcome"] == "dry_run"
    assert report["scope"] == old.key
    assert report["evidence"]["graph_snapshots"] == 1
    assert report["policy"]["exclusion_count"] == 1
    assert report["policy"]["dependent_scopes"] == 1
    assert not destination.exists()
    assert _count(root) == 2
    result = maintenance.archive_scope(
        root, old.key, destination, all_servers_stopped=True, apply=True
    )
    assert result["outcome"] == "archived"
    assert _count(root) == 1
    assert (destination / (old.key + ".sqlite")).read_bytes() == before
    assert unknown.read_text(encoding="utf-8") == "preserve"
    with closing(sqlite3.connect(destination / (old.key + ".sqlite"))) as db:
        assert db.execute("SELECT id FROM graph_snapshots").fetchone()[0] == snapshot
        assert (
            "Assets/Private/**"
            in db.execute("SELECT value FROM graph_state WHERE key='policy'").fetchone()[0]
        )
    current = open_workspace(root, GraphScope("owner", "new", "project"))
    assert current.restart_policy_pending
    keep = GraphStore.open(root, retained)
    assert GraphQueries(keep).diff(retained_snapshot, retained_snapshot)["changes"] == []
    assert keep.query("owner")["edges"][0]["target"] == "target"
    assert open_workspace(root, retained).policy()["exclude"] == ["Assets/Private/**"]


def test_live_store_refused_and_last_handle_releases_ownership(tmp_path):
    maintenance = _maintenance()
    scope, _ = _seed(tmp_path / "state", "one")
    first = GraphStore.open(tmp_path / "state", scope)
    second = GraphStore.open(tmp_path / "state", scope)
    del first
    gc.collect()
    with pytest.raises(ValueError, match="live"):
        maintenance.archive_scope(
            tmp_path / "state",
            scope.key,
            tmp_path / "archive",
            all_servers_stopped=True,
            apply=True,
        )
    assert second.query("owner")["edges"][0]["target"] == "target"
    del second
    gc.collect()
    assert (
        maintenance.archive_scope(
            tmp_path / "state",
            scope.key,
            tmp_path / "archive",
            all_servers_stopped=True,
            apply=True,
        )["outcome"]
        == "archived"
    )


def test_last_policy_archive_requires_explicit_recovery_in_new_scope(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    maintenance.archive_scope(
        root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
    )
    workspace = open_workspace(root, GraphScope("owner", "new", "project"))
    assert workspace.restart_policy_pending
    with pytest.raises(StorageBudgetError, match="archived"):
        GraphStore.open(root, scope)


@pytest.mark.parametrize("scope", ["../escape", "A" * 64, "a" * 64 + ".sqlite"])
def test_ambiguous_scope_input_is_rejected_without_changes(tmp_path, scope):
    maintenance = _maintenance()
    root = tmp_path / "state"
    _seed(root, "one")
    with pytest.raises(ValueError):
        maintenance.archive_scope(
            root, scope, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    assert _count(root) == 1
    assert not (tmp_path / "archive").exists()


def test_archive_requires_explicit_offline_assertion_and_outside_destination(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    with pytest.raises(ValueError, match="stopped"):
        maintenance.archive_scope(root, scope.key, tmp_path / "archive", apply=True)
    with pytest.raises(ValueError, match="outside"):
        maintenance.archive_scope(
            root, scope.key, root / "archive", all_servers_stopped=True, apply=True
        )
    assert _count(root) == 1


@pytest.mark.parametrize("failure", ["copy", "remove", "ledger", "receipt"])
def test_interrupted_archive_blocks_open_then_retry_preserves_exact_bytes(
    tmp_path, monkeypatch, failure
):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    source = root / (scope.key + ".sqlite")
    original = source.read_bytes()
    archive = tmp_path / "archive"
    original_unlink = Path.unlink
    original_release = maintenance._release_reservation
    with monkeypatch.context() as injected:
        if failure == "copy":

            def copy_failure(*_args):
                raise OSError("injected full volume")

            injected.setattr(maintenance, "_copy_file", copy_failure)
        elif failure == "remove":

            def remove_failure(path, *args, **kwargs):
                original_unlink(path, *args, **kwargs)
                if path == source:
                    raise OSError("injected interruption after source removal")

            injected.setattr(Path, "unlink", remove_failure)
        elif failure == "ledger":

            def ledger_failure(*_args):
                raise sqlite3.OperationalError("injected ledger failure")

            injected.setattr(maintenance, "_release_reservation", ledger_failure)
        else:

            def receipt_failure(*args):
                original_release(*args)
                raise OSError("injected interruption after ledger commit")

            injected.setattr(maintenance, "_release_reservation", receipt_failure)
        with pytest.raises((OSError, sqlite3.DatabaseError)):
            maintenance.archive_scope(
                root, scope.key, archive, all_servers_stopped=True, apply=True
            )
    assert _count(root) == (0 if failure == "receipt" else 1)
    with pytest.raises(StorageBudgetError, match="incomplete"):
        GraphStore.open(root, GraphScope("owner", "new", "project"))
    gc.collect()
    report = maintenance.archive_scope(root, scope.key, archive, all_servers_stopped=True)
    assert report["outcome"] == "recovery_required"
    assert (
        maintenance.archive_scope(root, scope.key, archive, all_servers_stopped=True, apply=True)[
            "outcome"
        ]
        == "archived"
    )
    assert (archive / source.name).read_bytes() == original
    assert _count(root) == 0
    assert (
        maintenance.archive_scope(root, scope.key, archive, all_servers_stopped=True, apply=True)[
            "outcome"
        ]
        == "already_archived"
    )
    assert _count(root) == 0


def test_hardlinked_scope_and_destination_are_rejected(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    source = root / (scope.key + ".sqlite")
    alias = tmp_path / "alias.sqlite"
    os.link(source, alias)
    with pytest.raises(ValueError, match="regular"):
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    assert source.exists() and alias.exists()
    assert _count(root) == 1


def test_current_worker_lease_is_refused_without_a_process_marker(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    with closing(sqlite3.connect(root / (scope.key + ".sqlite"))) as db, db:
        db.execute("INSERT INTO graph_worker VALUES (1,'legacy-worker',9999999999)")
    with pytest.raises(ValueError, match="live worker"):
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    assert _count(root) == 1


def test_cli_inspection_is_bounded_json_and_does_not_emit_stored_payloads(tmp_path, capsys):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    args = [
        "inspect",
        "--directory",
        str(root),
        "--scope",
        scope.key,
        "--destination",
        str(tmp_path / "archive"),
        "--all-servers-stopped",
    ]
    assert maintenance.main(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["outcome"] == "dry_run"
    assert "Assets/Private" not in output
    assert len(output) < 8192
    assert maintenance.main([*args, "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["success"] is False


def test_real_process_crash_releases_live_lock_and_incomplete_copy_resumes(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    code = """
import os, sys
from pathlib import Path
from services.reference_graph import maintenance
original = maintenance._copy_file
def interrupted(*args):
    original(*args)
    os._exit(23)
maintenance._copy_file = interrupted
maintenance.archive_scope(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]),
                          all_servers_stopped=True, apply=True)
"""
    archive = tmp_path / "archive"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), scope.key, str(archive)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 23, result.stderr
    assert (root / (scope.key + ".sqlite")).exists()
    assert (
        maintenance.archive_scope(root, scope.key, archive, all_servers_stopped=True, apply=True)[
            "outcome"
        ]
        == "archived"
    )


def _environment():
    return {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}


def _ready(path, child):
    deadline = time.monotonic() + 10
    while not path.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), child.communicate(timeout=5)


def test_other_process_live_ownership_refuses_archive_until_process_exit(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    ready = tmp_path / "ready"
    code = """
import os, sys
from pathlib import Path
from services.reference_graph.store import GraphStore, GraphScope
store = GraphStore.open(Path(sys.argv[1]), GraphScope('owner','one','project'))
Path(sys.argv[2]).write_text('ready')
sys.stdin.readline()
os._exit(23)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(ready)],
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _ready(ready, child)
        with pytest.raises(ValueError, match="live"):
            maintenance.archive_scope(
                root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
            )
        assert _count(root) == 1
    finally:
        child.communicate("stop\n", timeout=10)
    assert child.returncode == 23
    assert (
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )["outcome"]
        == "archived"
    )


def test_new_opener_waits_for_maintenance_gate_then_uses_reclaimed_quota(tmp_path, monkeypatch):
    maintenance = _maintenance()
    root = tmp_path / "state"
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_SCOPES", "1")
    scope, _ = _seed(root, "one")
    ready = tmp_path / "attempting"
    code = """
import sys
from pathlib import Path
from services.reference_graph.store import GraphStore, GraphScope
Path(sys.argv[2]).write_text('attempting')
store = GraphStore.open(Path(sys.argv[1]), GraphScope('owner','new','project'))
print(store.status()['backend'])
"""
    original = maintenance._copy_file
    children = []

    def raced_copy(*args):
        if not children:
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(root), str(ready)],
                env=_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
            _ready(ready, child)
            assert child.poll() is None
        original(*args)

    monkeypatch.setattr(maintenance, "_copy_file", raced_copy)
    try:
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    finally:
        output, errors = children[0].communicate(timeout=15)
    assert children[0].returncode == 0, errors
    assert output.strip() == "sqlite"
    assert _count(root) == 1
    assert not (root / (scope.key + ".sqlite")).exists()


def test_unknown_files_and_lock_inodes_are_preserved(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    extra = root / (scope.key + ".sqlite.user-backup")
    extra.write_bytes(b"history")
    locks = [
        root / ".storage.lock",
        root / (scope.key + ".sqlite.lock"),
        root / (scope.key + ".policy.lock"),
    ]
    before = {path: path.stat().st_ino for path in locks}
    maintenance.archive_scope(
        root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
    )
    assert {path: path.stat().st_ino for path in locks} == before
    assert extra.read_bytes() == b"history"


def test_tampered_recovery_path_never_removes_external_file(tmp_path, monkeypatch):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    with monkeypatch.context() as injected:

        def fail_copy(*_args):
            raise OSError("injected")

        injected.setattr(maintenance, "_copy_file", fail_copy)
        with pytest.raises(OSError):
            maintenance.archive_scope(
                root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
            )
    pending = root / ".archive-pending.json"
    manifest = json.loads(pending.read_text(encoding="utf-8"))
    manifest["files"][0]["name"] = "../victim"
    pending.write_text(json.dumps(manifest), encoding="utf-8")
    victim = tmp_path / "victim"
    victim.write_bytes(b"history")
    with pytest.raises(ValueError, match="filenames"):
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    assert victim.read_bytes() == b"history"
    assert _count(root) == 1


def test_source_changed_after_copy_is_never_removed(tmp_path, monkeypatch):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    original = maintenance._copy_file

    def changed_copy(source, destination, record):
        original(source, destination, record)
        with source.open("ab") as stream:
            stream.write(b"changed")

    monkeypatch.setattr(maintenance, "_copy_file", changed_copy)
    with pytest.raises(ValueError, match="does not match"):
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    assert (root / (scope.key + ".sqlite")).read_bytes().endswith(b"changed")
    assert _count(root) == 1


def test_list_paging_excludes_unknown_reservations_and_cli_parser_errors_are_json(tmp_path, capsys):
    maintenance = _maintenance()
    root = tmp_path / "state"
    first, _ = _seed(root, "one")
    second, _ = _seed(root, "two")
    with closing(sqlite3.connect(root / ".storage.sqlite")) as db, db:
        db.execute("INSERT INTO scopes VALUES ('notes.sqlite','',1,1,NULL)")
    result = maintenance.list_scopes(root, all_servers_stopped=True, limit=1)
    following = maintenance.list_scopes(
        root, all_servers_stopped=True, limit=1, after=result["next_after"]
    )
    assert {result["scopes"][0]["scope"], following["scopes"][0]["scope"]} == {
        first.key,
        second.key,
    }
    assert following["next_after"] is None
    assert maintenance.main(["list", "--directory", str(root), "--limit", "invalid"]) == 1
    assert json.loads(capsys.readouterr().out)["success"] is False


def test_crashed_sqlite_wal_and_shm_are_archived_without_losing_committed_rows(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute('CREATE TABLE committed_history(value TEXT)')
db.execute("INSERT INTO committed_history VALUES ('retained')")
db.commit()
os._exit(23)
"""
    source = root / (scope.key + ".sqlite")
    child = subprocess.run(
        [sys.executable, "-c", code, str(source)],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert child.returncode == 23, child.stderr
    assert Path(str(source) + "-wal").exists()
    assert Path(str(source) + "-shm").exists()
    result = maintenance.archive_scope(
        root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
    )
    assert {item["name"] for item in result["files"]} == {
        source.name,
        source.name + "-wal",
        source.name + "-shm",
    }
    archived = tmp_path / "archive" / source.name
    with closing(sqlite3.connect(archived.as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("SELECT value FROM committed_history").fetchone()[0] == "retained"
    assert not source.exists()
    assert not Path(str(source) + "-wal").exists()


def test_linked_destination_parent_is_refused(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS account cannot create directory symlinks")
    with pytest.raises(ValueError, match="linked"):
        maintenance.archive_scope(
            root, scope.key, linked / "archive", all_servers_stopped=True, apply=True
        )
    assert not (target / "archive").exists()
    assert _count(root) == 1


@pytest.mark.parametrize("kind", ["writer", "policy"])
def test_legacy_scope_lock_refuses_archival_without_waiting(tmp_path, kind):
    maintenance = _maintenance()
    from services.reference_graph.storage_budget import file_lock

    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    lock = root / (scope.key + (".sqlite.lock" if kind == "writer" else ".policy.lock"))
    with file_lock(lock):
        with pytest.raises(ValueError, match="live writer or policy reader"):
            maintenance.archive_scope(
                root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
            )
    assert _count(root) == 1
    assert not (tmp_path / "archive").exists()


def test_corrupt_manifest_types_fail_as_one_json_error(tmp_path, monkeypatch, capsys):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    archive = tmp_path / "archive"
    with monkeypatch.context() as injected:

        def fail_copy(*_args):
            raise OSError("injected")

        injected.setattr(maintenance, "_copy_file", fail_copy)
        with pytest.raises(OSError):
            maintenance.archive_scope(
                root, scope.key, archive, all_servers_stopped=True, apply=True
            )
    pending = root / ".archive-pending.json"
    manifest = json.loads(pending.read_text(encoding="utf-8"))
    manifest["files"][0]["name"] = ["invalid"]
    pending.write_text(json.dumps(manifest), encoding="utf-8")
    assert (
        maintenance.main(
            [
                "archive",
                "--directory",
                str(root),
                "--scope",
                scope.key,
                "--destination",
                str(archive),
                "--all-servers-stopped",
                "--apply",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["success"] is False
    assert _count(root) == 1


def test_directory_aliases_share_lifetime_owner_without_locking_out_same_process(tmp_path):
    code = """
import gc, sys
from pathlib import Path
from services.reference_graph.store import GraphStore, GraphScope
root = Path(sys.argv[1])
first = GraphStore.open(root, GraphScope('owner','one','project'))
(root / 'child').mkdir()
second = GraphStore.open(root / 'child' / '..', first.scope)
del first
gc.collect()
print(second.status()['backend'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "state")],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sqlite"


def test_copy_promotion_failure_preserves_partial_and_original_for_retry(tmp_path, monkeypatch):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    source = root / (scope.key + ".sqlite")
    original = source.read_bytes()
    archive = tmp_path / "archive"
    rename = os.rename
    with monkeypatch.context() as injected:

        def fail_promotion(before, after):
            if Path(before).name == source.name + ".partial":
                raise PermissionError("injected archive rename denial")
            return rename(before, after)

        injected.setattr(os, "rename", fail_promotion)
        with pytest.raises(PermissionError):
            maintenance.archive_scope(
                root, scope.key, archive, all_servers_stopped=True, apply=True
            )
    assert source.read_bytes() == original
    assert (archive / (source.name + ".partial")).read_bytes() == original
    assert _count(root) == 1
    maintenance.archive_scope(root, scope.key, archive, all_servers_stopped=True, apply=True)
    assert (archive / source.name).read_bytes() == original
    assert _count(root) == 0


def test_ledger_transaction_failure_rolls_back_receipt_and_reservation_together(tmp_path):
    maintenance = _maintenance()
    root = tmp_path / "state"
    scope, _ = _seed(root, "one")
    with closing(sqlite3.connect(root / ".storage.sqlite")) as db, db:
        db.execute(
            "CREATE TRIGGER fail_archive BEFORE DELETE ON scopes BEGIN "
            "SELECT RAISE(ABORT,'injected ledger failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError, match="injected ledger"):
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )
    with closing(sqlite3.connect(root / ".storage.sqlite")) as db, db:
        assert db.execute("SELECT count(*) FROM scopes").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM archived_scopes").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_archive")
    assert (
        maintenance.archive_scope(
            root, scope.key, tmp_path / "archive", all_servers_stopped=True, apply=True
        )["outcome"]
        == "archived"
    )
    assert _count(root) == 0
