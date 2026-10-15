"""Exact registry framing and bounded immutable derived read-model objects.

The host owns canonical authority, normal writer locking and fresh dependency
validation. This module never discovers a current object or mutates the registry.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
from typing import Final, Iterator, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
BODY_MAX_BYTES: Final = 8_388_608
HEADER_BYTES: Final = 80
RAW_MAX_BYTES: Final = BODY_MAX_BYTES + HEADER_BYTES
MODEL_MAX_BYTES: Final = 33_554_432
DATABASE_MAX_BYTES: Final = 134_217_728
_MAGIC: Final = b"FPRGDB\x00\n"
_HEADER: Final = struct.Struct(">8sHHI32s32s")
_ID: Final = re.compile(r"[0-9a-f]{64}")
_APPLICATION_ID: Final = 0x4650524D
_META_SQL: Final = (
    "CREATE TABLE store_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "storage_version INTEGER NOT NULL CHECK(storage_version=1), program_id TEXT NOT NULL)"
)
_OBJECT_SQL: Final = (
    "CREATE TABLE model_objects (object_id TEXT PRIMARY KEY COLLATE BINARY, "
    "payload BLOB NOT NULL, byte_length INTEGER NOT NULL, "
    "CHECK(length(object_id)=64 AND object_id NOT GLOB '*[^0-9a-f]*'), "
    "CHECK(typeof(payload)='blob'), CHECK(typeof(byte_length)='integer'), "
    "CHECK(byte_length BETWEEN 1 AND 33554432), CHECK(length(payload)=byte_length))"
)


class RegistryFormatError(ValueError):
    """A finite framing failure, never an instruction to reinterpret corrupt bytes."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ModelStoreError(RuntimeError):
    """A bounded store failure; the host must use its full correct read path."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RegistryObservation:
    """One exact physical capture and its independently identified logical BODY."""

    raw_bytes: bytes
    body_bytes: bytes
    body_sha256: str
    storage_sha256: str
    model_id: str | None
    format: str


def _valid_id(value: str) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def decode_registry(raw: bytes) -> RegistryObservation:
    """Unframe canonical bytes only; strict logical JSON parsing belongs to core."""
    if type(raw) is not bytes or not 1 <= len(raw) <= RAW_MAX_BYTES:
        raise RegistryFormatError("registry_size")
    physical = hashlib.sha256(raw).hexdigest()
    if not raw.startswith(_MAGIC):
        if _MAGIC.startswith(raw) or raw.startswith(b"FPRGDB"):
            raise RegistryFormatError("registry_header")
        if len(raw) > BODY_MAX_BYTES:
            raise RegistryFormatError("registry_size")
        return RegistryObservation(raw, raw, physical, physical, None, "json")
    if len(raw) < HEADER_BYTES:
        raise RegistryFormatError("registry_header")
    _, version, flags, length, model_hash, body_hash = _HEADER.unpack(raw[:HEADER_BYTES])
    if version != 1 or flags != 0:
        raise RegistryFormatError("registry_version")
    if not 1 <= length <= BODY_MAX_BYTES or len(raw) != HEADER_BYTES + length:
        raise RegistryFormatError("registry_size")
    body = raw[HEADER_BYTES:]
    if hashlib.sha256(body).digest() != body_hash:
        raise RegistryFormatError("registry_digest")
    return RegistryObservation(
        raw, body, body_hash.hex(), physical, model_hash.hex(), "envelope-v1"
    )


def encode_registry(body_bytes: bytes, model_id: str) -> bytes:
    """Preserve exact validated BODY bytes and bind one exact staged model digest."""
    if type(body_bytes) is not bytes or not 1 <= len(body_bytes) <= BODY_MAX_BYTES:
        raise RegistryFormatError("registry_size")
    if not _valid_id(model_id):
        raise RegistryFormatError("registry_model_id")
    return (
        _HEADER.pack(
            _MAGIC,
            1,
            0,
            len(body_bytes),
            bytes.fromhex(model_id),
            hashlib.sha256(body_bytes).digest(),
        )
        + body_bytes
    )


def _core():
    return importlib.import_module(("tools." if __package__ else "") + "feature_program")


def _store_path(repo: Path, program_dir: Path) -> Path:
    """Use freshly admitted roots, never a database path supplied by model content."""
    core = _core()
    try:
        if not repo.is_absolute() or not program_dir.is_absolute():
            raise ModelStoreError("invalid_path")
        if repo.as_posix().startswith("//") or program_dir.as_posix().startswith("//"):
            raise ModelStoreError("invalid_path")
        if not repo.is_dir() or not program_dir.is_dir():
            raise ModelStoreError("invalid_path")
        relative = program_dir.relative_to(repo).as_posix()
        core.safe_path(repo, relative)
        if program_dir.resolve() != program_dir.absolute():
            raise ModelStoreError("invalid_path")
        identity = {
            "repo": os.path.normcase(str(repo)).replace("\\", "/"),
            "program": os.path.normcase(str(program_dir)).replace("\\", "/"),
        }
        namespace = hashlib.sha256(_canonical(identity)).hexdigest()
        return core.safe_path(repo, f".work/read-models/{namespace}/read-model.sqlite3")
    except (OSError, ValueError, core.ProgramError) as error:
        raise ModelStoreError("invalid_path") from error


def _canonical(value: Record) -> bytes:
    pending: list[Json] = [value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, dict):
                if any(type(key) is not str for key in item):
                    raise ModelStoreError("invalid_model")
                pending.extend(item.values())
            else:
                pending.extend(item)
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ModelStoreError("invalid_model") from error


def _model(value: Record) -> Record:
    if (
        type(value) is not dict
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("kind") != "feature_program_read_model"
        or type(value.get("program_id")) is not str
        or not value["program_id"].strip()
    ):
        raise ModelStoreError("invalid_model")
    return value


def _pairs(pairs: list[tuple[str, Json]]) -> Record:
    result: Record = {}
    for key, value in pairs:
        if key in result:
            raise ModelStoreError("invalid_model")
        result[key] = value
    return result


def _constant(_value: str):
    raise ModelStoreError("invalid_model")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ModelStoreError("invalid_model")
    return number


def _decode(payload: bytes) -> Record:
    try:
        value = json.loads(
            payload, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float
        )
        return _model(value)
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ModelStoreError("invalid_model") from error


def _check_file(path: Path, *, creating: bool) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        linked = sidecar.is_symlink() or getattr(sidecar, "is_junction", lambda: False)()
        if not linked and not sidecar.exists():
            continue
        # Only an explicit locked writer may let SQLite recover a rollback journal.
        # SQLite validates/replays it; this module never deletes or interprets it.
        if (
            suffix != "-journal"
            or not creating
            or linked
            or not sidecar.is_file()
            or sidecar.stat().st_size > DATABASE_MAX_BYTES * 2
        ):
            raise ModelStoreError("store_sidecar")
    if not path.exists():
        if creating:
            return
        raise ModelStoreError("store_missing")
    if not path.is_file():
        raise ModelStoreError("store_size")
    size = path.stat().st_size
    if creating and size == 0:
        return
    if not 100 <= size <= DATABASE_MAX_BYTES:
        raise ModelStoreError("store_size")
    with path.open("rb") as stream:
        header = stream.read(100)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        raise ModelStoreError("store_format")
    if int.from_bytes(header[16:18], "big") != 4096:
        raise ModelStoreError("store_format")


def _schema(connection: sqlite3.Connection) -> str:
    if connection.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,):
        raise ModelStoreError("store_schema")
    if connection.execute("PRAGMA user_version").fetchone() != (1,):
        raise ModelStoreError("store_schema")
    rows = connection.execute(
        "SELECT substr(type,1,128),substr(name,1,128),substr(tbl_name,1,128),CASE WHEN length(sql)<=16384 THEN sql ELSE NULL END FROM sqlite_schema LIMIT 5"
    ).fetchall()
    expected = {
        ("table", "store_meta", "store_meta", _META_SQL),
        ("table", "model_objects", "model_objects", _OBJECT_SQL),
        ("index", "sqlite_autoindex_model_objects_1", "model_objects", None),
    }
    if sum(len(str(row)) for row in rows) > 16384 or set(rows) != expected:
        raise ModelStoreError("store_schema")
    meta = connection.execute(
        "SELECT singleton,storage_version,program_id FROM store_meta LIMIT 2"
    ).fetchall()
    if (
        len(meta) != 1
        or meta[0][:2] != (1, 1)
        or type(meta[0][2]) is not str
        or not meta[0][2].strip()
    ):
        raise ModelStoreError("store_schema")
    return meta[0][2]


@contextmanager
def _connection(
    path: Path, *, writing: bool, program_id: str | None = None
) -> Iterator[sqlite3.Connection]:
    """Close every connection; reads never create, recover, migrate or prune."""
    connection = None
    try:
        _check_file(path, creating=writing)
        fresh = not path.exists()
        if fresh and program_id is None:
            raise ModelStoreError("store_missing")
        if fresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Re-admit after directory creation, before SQLite touches the path.
            core = _core()
            core.safe_path(path.parent, path.name)
        options = (
            {"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL}
            if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL")
            else {}
        )
        connection = sqlite3.connect(
            path.as_uri() + ("?mode=rwc" if writing else "?mode=ro"),
            timeout=0.5 if writing else 0.1,
            detect_types=0,
            isolation_level=None,
            check_same_thread=True,
            uri=True,
            **options,
        )
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MODEL_MAX_BYTES + 4096)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 16)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        steps = 0

        def progress() -> int:
            nonlocal steps
            steps += 1
            return int(steps > 1000)

        connection.set_progress_handler(progress, 1000)
        connection.execute("PRAGMA trusted_schema=OFF")
        if not writing:
            connection.execute("PRAGMA query_only=ON")
        empty = connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is None
        initialize = writing and program_id is not None and empty
        if initialize:
            if connection.execute("PRAGMA application_id").fetchone() != (0,) or connection.execute(
                "PRAGMA user_version"
            ).fetchone() != (0,):
                raise ModelStoreError("store_schema")
            connection.execute("PRAGMA page_size=4096")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA max_page_count=32768")
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is not None
                or connection.execute("PRAGMA application_id").fetchone() != (0,)
                or connection.execute("PRAGMA user_version").fetchone() != (0,)
            ):
                raise ModelStoreError("store_schema")
            connection.execute(_META_SQL)
            connection.execute(_OBJECT_SQL)
            connection.execute("INSERT INTO store_meta VALUES (1,1,?)", (program_id,))
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("COMMIT")
        if writing:
            _check_file(path, creating=True)
            connection.execute("PRAGMA max_page_count=32768")
        yield connection
    except (sqlite3.Error, OSError) as error:
        raise ModelStoreError("store_unavailable") from error
    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()


def _read_object(connection: sqlite3.Connection, model_id: str, program_id: str) -> Record:
    meta = connection.execute(
        "SELECT typeof(payload),byte_length,length(payload) FROM model_objects WHERE object_id=? LIMIT 2",
        (model_id,),
    ).fetchall()
    if not meta:
        raise ModelStoreError("object_missing")
    if len(meta) != 1:
        raise ModelStoreError("object_corrupt")
    kind, length, actual = meta[0]
    if (
        kind != "blob"
        or type(length) is not int
        or not 1 <= length <= MODEL_MAX_BYTES
        or length != actual
    ):
        raise ModelStoreError("object_corrupt")
    payload = connection.execute(
        "SELECT payload FROM model_objects WHERE object_id=?", (model_id,)
    ).fetchone()[0]
    if type(payload) is not bytes or hashlib.sha256(payload).hexdigest() != model_id:
        raise ModelStoreError("object_corrupt")
    model = _decode(payload)
    if model["program_id"] != program_id:
        raise ModelStoreError("object_corrupt")
    return model


def store_model(repo: Path, program_dir: Path, model: Record) -> str:
    """Stage an immutable object under the host lock, before its canonical CAS."""
    _model(model)
    payload = _canonical(model)
    if not 1 <= len(payload) <= MODEL_MAX_BYTES:
        raise ModelStoreError("model_size")
    model_id = hashlib.sha256(payload).hexdigest()
    path = _store_path(repo, program_dir)
    with _connection(path, writing=True, program_id=model["program_id"]) as connection:
        connection.execute("BEGIN IMMEDIATE")
        program_id = _schema(connection)
        if program_id != model["program_id"]:
            raise ModelStoreError("invalid_model")
        found = connection.execute(
            "SELECT 1 FROM model_objects WHERE object_id=?", (model_id,)
        ).fetchone()
        if found is not None:
            _read_object(connection, model_id, program_id)
        else:
            if connection.execute("SELECT count(*) FROM model_objects").fetchone()[0] >= 2:
                raise ModelStoreError("store_retention_required")
            connection.execute(
                "INSERT INTO model_objects VALUES (?,?,?)", (model_id, payload, len(payload))
            )
        connection.execute("COMMIT")
    return model_id


def load_model(repo: Path, program_dir: Path, model_id: str) -> Record:
    """Load only one exact selected object, without any read-side mutation."""
    if not _valid_id(model_id):
        raise ModelStoreError("invalid_id")
    with _connection(_store_path(repo, program_dir), writing=False) as connection:
        connection.execute("BEGIN")
        return _read_object(connection, model_id, _schema(connection))


def retain_models(
    repo: Path,
    program_dir: Path,
    *,
    current_model_id: str | None,
    staged_model_id: str | None = None,
    expected_program_id: str | None = None,
) -> None:
    """Prune derived orphans only under host lock and fresh actual selection proof."""
    protected = {value for value in (current_model_id, staged_model_id) if value is not None}
    if any(not _valid_id(value) for value in protected):
        raise ModelStoreError("invalid_id")
    with _connection(_store_path(repo, program_dir), writing=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        program_id = _schema(connection)
        if expected_program_id is not None and (
            type(expected_program_id) is not str
            or not expected_program_id.strip()
            or program_id != expected_program_id
        ):
            raise ModelStoreError("store_program")
        for model_id in protected:
            _read_object(connection, model_id, program_id)
        if protected:
            placeholders = ",".join("?" for _ in protected)
            connection.execute(
                f"DELETE FROM model_objects WHERE object_id NOT IN ({placeholders})",
                tuple(sorted(protected)),
            )
        else:
            connection.execute("DELETE FROM model_objects")
        connection.execute("COMMIT")
