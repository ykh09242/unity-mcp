"""Real file/SQLite boundary checks for registry framing and derived objects."""

from contextlib import closing
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import feature_program_storage as storage  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    repo = tmp_path / "repo"
    program = repo / ".work" / "programs" / "example"
    program.mkdir(parents=True)
    return repo, program


def model(sequence=1):
    return {
        "schema_version": 1,
        "kind": "feature_program_read_model",
        "program_id": "example",
        "sequence": sequence,
        "eligible": True,
        "snapshot": {"text": "한국어", "missing": None},
    }


def db_path(roots):
    return storage._store_path(*roots)


def update_db(roots, sql, parameters=()):
    with closing(sqlite3.connect(db_path(roots))) as connection:
        connection.execute(sql, parameters)
        connection.commit()


def test_legacy_registry_preserves_exact_body_identity():
    # Given noncanonical legacy whitespace, when captured, then no normalization occurs.
    raw = b'{ "a": 1 }\r\n'
    observed = storage.decode_registry(raw)
    assert observed.raw_bytes == observed.body_bytes == raw
    assert observed.body_sha256 == observed.storage_sha256 == hashlib.sha256(raw).hexdigest()
    assert observed.model_id is None and observed.format == "json"
    with pytest.raises(FrozenInstanceError):
        observed.format = "changed"


def test_envelope_roundtrip_separates_physical_selection_from_body():
    # Given identical logical bytes, when selectors change, then physical identity changes only.
    body = b'{"n": 1}\n'
    first = storage.decode_registry(storage.encode_registry(body, "a" * 64))
    second = storage.decode_registry(storage.encode_registry(body, "b" * 64))
    assert first.body_bytes == second.body_bytes == body
    assert first.body_sha256 == second.body_sha256
    assert first.storage_sha256 != second.storage_sha256
    assert first.model_id == "a" * 64 and second.model_id == "b" * 64
    assert len(first.raw_bytes) == 80 + len(body)
    assert first.format == "envelope-v1"


@pytest.mark.parametrize(
    "change",
    ["truncated_magic", "truncated_header", "version", "flags", "length", "digest", "trailing"],
)
def test_recognized_corrupt_envelope_never_becomes_legacy(change):
    # Given a valid frame, when one boundary is corrupted, then decoding fails closed.
    raw = bytearray(storage.encode_registry(b"{}", "a" * 64))
    if change == "truncated_magic":
        raw = raw[:5]
    elif change == "truncated_header":
        raw = raw[:79]
    elif change == "version":
        raw[8:10] = b"\x00\x02"
    elif change == "flags":
        raw[11] = 1
    elif change == "length":
        raw[12:16] = struct.pack(">I", 3)
    elif change == "digest":
        raw[-1] ^= 1
    else:
        raw += b"x"
    with pytest.raises(storage.RegistryFormatError):
        storage.decode_registry(bytes(raw))


@pytest.mark.parametrize("identifier", ["A" * 64, "a" * 63, "g" * 64, "a" * 65, None, 1])
def test_encoding_rejects_noncanonical_model_id(identifier):
    with pytest.raises(storage.RegistryFormatError, match="registry_model_id"):
        storage.encode_registry(b"{}", identifier)


def test_registry_size_bound_includes_fixed_overhead_without_widening_body():
    body = b" " * storage.BODY_MAX_BYTES
    assert storage.decode_registry(storage.encode_registry(body, "0" * 64)).body_bytes == body
    with pytest.raises(storage.RegistryFormatError):
        storage.decode_registry(body + b" ")
    with pytest.raises(storage.RegistryFormatError):
        storage.encode_registry(body + b" ", "0" * 64)
    with pytest.raises(storage.RegistryFormatError):
        storage.decode_registry(b"")


def test_store_stage_is_immutable_detached_and_does_not_select_canonical(roots):
    # Given an unchanged canonical file, when an object is staged, then only derived storage changes.
    canonical = roots[1] / "backlog.json"
    canonical.write_bytes(b'{"legacy":true}\n')
    original = model()
    identifier = storage.store_model(*roots, original)
    assert (
        identifier
        == hashlib.sha256(
            json.dumps(
                original, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode()
        ).hexdigest()
    )
    assert canonical.read_bytes() == b'{"legacy":true}\n'
    assert storage.store_model(*roots, model()) == identifier
    loaded = storage.load_model(*roots, identifier)
    loaded["snapshot"]["text"] = "mutated"
    assert storage.load_model(*roots, identifier) == original
    assert db_path(roots).parent.parent == roots[0] / ".work" / "read-models"
    assert not (roots[1] / "read-model.sqlite3").exists()


def test_read_missing_store_and_retention_do_not_create_database(roots):
    for action in (
        lambda: storage.load_model(*roots, "a" * 64),
        lambda: storage.retain_models(*roots, current_model_id=None),
    ):
        with pytest.raises(storage.ModelStoreError):
            action()
    assert not (roots[0] / ".work" / "read-models").exists()


def test_read_missing_selected_object_never_uses_other_success(roots):
    storage.store_model(*roots, model())
    before = db_path(roots).read_bytes()
    with pytest.raises(storage.ModelStoreError, match="object_missing"):
        storage.load_model(*roots, "a" * 64)
    assert db_path(roots).read_bytes() == before


def test_retention_preserves_actual_current_and_stage_then_reclaims_orphan(roots):
    # Given a stage committed before an aborted canonical CAS, host-proven retention protects the old selection.
    current = storage.store_model(*roots, model(1))
    staged = storage.store_model(*roots, model(2))
    storage.retain_models(*roots, current_model_id=current, staged_model_id=staged)
    assert storage.load_model(*roots, current)["sequence"] == 1
    assert storage.load_model(*roots, staged)["sequence"] == 2
    with pytest.raises(storage.ModelStoreError, match="store_retention_required"):
        storage.store_model(*roots, model(3))
    storage.retain_models(*roots, current_model_id=current)
    next_stage = storage.store_model(*roots, model(3))
    assert storage.load_model(*roots, current)["sequence"] == 1
    assert storage.load_model(*roots, next_stage)["sequence"] == 3
    with pytest.raises(storage.ModelStoreError, match="object_missing"):
        storage.load_model(*roots, staged)


def test_missing_protected_object_rolls_back_retention(roots):
    current = storage.store_model(*roots, model(1))
    staged = storage.store_model(*roots, model(2))
    with pytest.raises(storage.ModelStoreError, match="object_missing"):
        storage.retain_models(*roots, current_model_id=current, staged_model_id="a" * 64)
    assert storage.load_model(*roots, staged)["sequence"] == 2
    assert storage.load_model(*roots, current)["sequence"] == 1


@pytest.mark.parametrize(
    "change",
    [
        "extra_table",
        "view",
        "trigger",
        "version",
        "application",
        "program",
        "payload",
        "length",
        "blob_type",
    ],
)
def test_store_corruption_rejected_without_read_side_repair(roots, change):
    identifier = storage.store_model(*roots, model())
    mutations = {
        "extra_table": "CREATE TABLE foreign_data(x)",
        "view": "CREATE VIEW foreign_view AS SELECT 1",
        "trigger": "CREATE TRIGGER foreign_trigger AFTER INSERT ON model_objects BEGIN SELECT 1; END",
        "version": "PRAGMA user_version=2",
        "application": "PRAGMA application_id=1",
        "program": "UPDATE store_meta SET program_id='other'",
        "payload": "UPDATE model_objects SET payload=zeroblob(byte_length)",
        "length": "UPDATE model_objects SET byte_length=1",
        "blob_type": "UPDATE model_objects SET payload=CAST(payload AS TEXT)",
    }
    with closing(sqlite3.connect(db_path(roots))) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(mutations[change])
        connection.commit()
    before = db_path(roots).read_bytes()
    with pytest.raises(storage.ModelStoreError):
        storage.load_model(*roots, identifier)
    assert db_path(roots).read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1,"kind":"feature_program_read_model","program_id":"example"}',
        b'{"schema_version":true,"kind":"feature_program_read_model","program_id":"example"}',
        b'{"schema_version":1,"kind":"feature_program_read_model","program_id":"example","x":NaN}',
        b'{"schema_version":1,"kind":"feature_program_read_model","program_id":"example","x":1e999}',
    ],
)
def test_exact_hash_does_not_authorize_malformed_model(roots, payload):
    storage.store_model(*roots, model())
    identifier = hashlib.sha256(payload).hexdigest()
    update_db(
        roots, "INSERT INTO model_objects VALUES (?,?,?)", (identifier, payload, len(payload))
    )
    with pytest.raises(storage.ModelStoreError, match="invalid_model"):
        storage.load_model(*roots, identifier)


@pytest.mark.parametrize(
    "change", ["nonfinite", "bool_version", "kind", "empty_program", "other_program", "oversize"]
)
def test_invalid_staged_model_does_not_remove_selected_object(roots, monkeypatch, change):
    current = storage.store_model(*roots, model())
    invalid = model(2)
    if change == "nonfinite":
        invalid["value"] = float("inf")
    elif change == "bool_version":
        invalid["schema_version"] = True
    elif change == "kind":
        invalid["kind"] = "other"
    elif change == "empty_program":
        invalid["program_id"] = " "
    elif change == "other_program":
        invalid["program_id"] = "other"
    else:
        monkeypatch.setattr(storage, "MODEL_MAX_BYTES", 1)
    with pytest.raises(storage.ModelStoreError):
        storage.store_model(*roots, invalid)
    monkeypatch.undo()
    assert storage.load_model(*roots, current) == model()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_unexpected_sidecar_fails_read_without_recovery(roots, suffix):
    identifier = storage.store_model(*roots, model())
    sidecar = db_path(roots).with_name(db_path(roots).name + suffix)
    sidecar.write_bytes(b"unexpected")
    with pytest.raises(storage.ModelStoreError, match="store_sidecar"):
        storage.load_model(*roots, identifier)
    assert sidecar.read_bytes() == b"unexpected"


def test_linked_store_is_rejected(roots):
    identifier = storage.store_model(*roots, model())
    path = db_path(roots)
    target = path.with_name("other.sqlite3")
    path.rename(target)
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Local symlink privilege unavailable: {error}")
    with pytest.raises(storage.ModelStoreError, match="invalid_path"):
        storage.load_model(*roots, identifier)


def test_busy_writer_fails_without_changing_selected_object(roots):
    current = storage.store_model(*roots, model())
    with closing(sqlite3.connect(db_path(roots), isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(storage.ModelStoreError):
            storage.store_model(*roots, model(2))
        connection.execute("ROLLBACK")
    assert storage.load_model(*roots, current) == model()


@pytest.mark.parametrize("initial", ["zero_file", "empty_schema"])
def test_locked_writer_resumes_interrupted_empty_initialization(roots, initial):
    # Given an interrupted initial file, readonly refuses it and the explicit writer initializes it.
    path = db_path(roots)
    path.parent.mkdir(parents=True)
    if initial == "zero_file":
        path.touch()
    else:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=0")
    before = path.read_bytes()
    with pytest.raises(storage.ModelStoreError):
        storage.load_model(*roots, "a" * 64)
    assert path.read_bytes() == before
    identifier = storage.store_model(*roots, model())
    assert storage.load_model(*roots, identifier) == model()


def test_writer_does_not_reset_foreign_empty_database(roots):
    # Given an empty but explicitly foreign database, recovery cannot erase its identity.
    path = db_path(roots)
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA application_id=99")
    before = path.read_bytes()
    with pytest.raises(storage.ModelStoreError, match="store_schema"):
        storage.store_model(*roots, model())
    assert path.read_bytes() == before


def test_locked_writer_recovers_actual_child_crash_journal_while_reader_never_recovers(roots):
    # Given a real process exit mid-SQLite-write, readonly preserves the crash state.
    current = storage.store_model(*roots, model())
    path = db_path(roots)
    script = """
import hashlib, os, sqlite3, sys
connection=sqlite3.connect(sys.argv[1],isolation_level=None)
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
payload=b'x'*1048576
connection.execute('INSERT INTO model_objects VALUES (?,?,?)',(hashlib.sha256(payload).hexdigest(),payload,len(payload)))
os._exit(0)
"""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    journal = path.with_name(path.name + "-journal")
    assert journal.stat().st_size > 0
    before = (path.read_bytes(), journal.read_bytes())
    with pytest.raises(storage.ModelStoreError, match="store_sidecar"):
        storage.load_model(*roots, current)
    assert (path.read_bytes(), journal.read_bytes()) == before
    # When the explicit locked writer restarts, SQLite rolls back the uncommitted object.
    staged = storage.store_model(*roots, model(2))
    assert storage.load_model(*roots, current) == model()
    assert storage.load_model(*roots, staged) == model(2)
    assert not journal.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_writer_recovery_never_admits_wal_sidecars(roots, suffix):
    current = storage.store_model(*roots, model())
    sidecar = db_path(roots).with_name(db_path(roots).name + suffix)
    sidecar.write_bytes(b"foreign")
    with pytest.raises(storage.ModelStoreError, match="store_sidecar"):
        storage.store_model(*roots, model(2))
    assert sidecar.read_bytes() == b"foreign"
    sidecar.unlink()
    assert storage.load_model(*roots, current) == model()


def test_writer_recovery_rejects_linked_journal(roots):
    storage.store_model(*roots, model())
    path = db_path(roots)
    target = path.with_name("foreign-journal")
    target.write_bytes(b"foreign")
    journal = path.with_name(path.name + "-journal")
    try:
        journal.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Local symlink privilege unavailable: {error}")
    with pytest.raises(storage.ModelStoreError, match="store_sidecar"):
        storage.store_model(*roots, model(2))
    assert target.read_bytes() == b"foreign"


@pytest.mark.parametrize("protected", [None, "a" * 64])
def test_retention_rejects_foreign_program_before_protected_read_or_delete(roots, protected):
    # Given a valid foreign store at the expected path, identity mismatch cannot erase it.
    foreign = model()
    foreign["program_id"] = "other"
    identifier = storage.store_model(*roots, foreign)
    before = db_path(roots).read_bytes()
    with pytest.raises(storage.ModelStoreError, match="store_program"):
        storage.retain_models(*roots, current_model_id=protected, expected_program_id="example")
    assert db_path(roots).read_bytes() == before
    assert storage.load_model(*roots, identifier) == foreign


def test_retention_matching_program_preserves_current_and_removes_only_orphan(roots):
    current = storage.store_model(*roots, model())
    orphan = storage.store_model(*roots, model(2))
    storage.retain_models(*roots, current_model_id=current, expected_program_id="example")
    assert storage.load_model(*roots, current) == model()
    with pytest.raises(storage.ModelStoreError, match="object_missing"):
        storage.load_model(*roots, orphan)
