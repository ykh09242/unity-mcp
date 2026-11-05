"""Explicit offline archival. Run with ``python -m services.reference_graph.maintenance``."""

import argparse
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

from services.reference_graph.storage_budget import (
    ARCHIVE_PENDING,
    LEDGER_BYTES,
    LIVE_PREFIX,
    SIDECARS,
    StorageBudgetError,
    _regular,
    file_lock,
)
from services.reference_graph.store import GraphStore, check_owned_path


MANIFEST = "archive.json"
MAX_MANIFEST_BYTES = 64 * 1024
_POLICY_RECOVERY = "New Editor scopes may require an explicit policy choice after archival"
_EVIDENCE = (
    "nodes",
    "edges",
    "observations",
    "declarations",
    "rules",
    "sources",
    "graph_snapshots",
    "graph_mutations",
    "graph_runtime_sessions",
    "graph_documents",
)


def _root(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Maintenance requires an absolute directory without parent traversal")
    path = check_owned_path(path)
    if not path.is_dir():
        raise ValueError("The state directory must already exist")
    return path


def _scope(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Select one exact lowercase 64-character scope digest")
    return value + ".sqlite"


def _destination(root: Path, destination: Path) -> Path:
    destination = Path(destination)
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("The archive destination must be an absolute directory")
    check_owned_path(destination)
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError("The archive destination must be outside the state directory")
    if not destination.parent.is_dir():
        raise ValueError("The archive destination parent must already exist")
    return destination


def _exact_file(path: Path, *, required: bool = False) -> bool:
    # Detect case aliases on case-sensitive filesystems as well as Windows.
    matches = [
        entry.name
        for entry in path.parent.iterdir()
        if entry.name.casefold() == path.name.casefold()
    ]
    if matches and matches != [path.name]:
        raise ValueError("Ambiguous maintenance filename; preserve the directory")
    _regular(path)
    if required and not path.exists():
        raise ValueError("Required maintenance file is missing; preserve the directory")
    return path.exists()


@contextmanager
def _offline(root: Path, all_servers_stopped: bool):
    if not all_servers_stopped:
        raise ValueError("Confirm all graph servers are stopped with --all-servers-stopped")
    with ExitStack() as stack:
        try:
            _exact_file(root / ".storage.lock")
            stack.enter_context(file_lock(root / ".storage.lock", wait=False))
            for entry in root.iterdir():
                if entry.name.startswith(LIVE_PREFIX):
                    if not re.fullmatch(r"\.graph-live-\d+-[a-f0-9]{32}\.lock", entry.name):
                        raise ValueError("Unknown live ownership file; preserve the directory")
                    stack.enter_context(file_lock(entry, wait=False))
        except (BlockingIOError, StorageBudgetError) as error:
            raise ValueError(
                "A live graph server or maintenance owner still uses this directory"
            ) from error
        yield stack


@contextmanager
def _ledger(root: Path, *, write: bool = False):
    path = root / ".storage.sqlite"
    for suffix in SIDECARS:
        _exact_file(Path(str(path) + suffix), required=not suffix)
    with closing(
        sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0)
    ) as db:
        db.row_factory = sqlite3.Row
        if (
            db.execute("PRAGMA application_id").fetchone()[0] != 0x554D5242
            or db.execute("PRAGMA user_version").fetchone()[0] != 3
        ):
            raise ValueError("Maintenance requires an owned ledger from an upgraded graph server")
        if write:
            db.execute("PRAGMA synchronous=FULL")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={LEDGER_BYTES // page_size}")
        yield db


def _load(path: Path) -> dict:
    _exact_file(path, required=True)
    with path.open("rb") as stream:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("Maintenance manifest exceeds its size limit; preserve it")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("Invalid maintenance manifest; preserve it")
    return result


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_new(path: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Maintenance manifest exceeds its size limit")
    _exact_file(path)
    if path.exists():
        raise ValueError("Maintenance metadata already exists; preserve it")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
    _sync_directory(path.parent)


def _digest(path: Path) -> dict:
    _exact_file(path, required=True)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Archive inputs must be unlinked regular files")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("Archive input changed during inspection; stop all writers")
    return {"name": path.name, "bytes": before.st_size, "sha256": digest.hexdigest()}


def _verify(path: Path, record: dict) -> None:
    if _digest(path) != record:
        raise ValueError("Archive file does not match its manifest; preserve both copies")


def _database_report(root: Path, name: str, reservation: dict) -> dict:
    path = root / name
    for suffix in SIDECARS:
        _exact_file(Path(str(path) + suffix), required=not suffix)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != GraphStore.APPLICATION_ID:
            raise ValueError("The selected database is not owned by the reference graph")
        if db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2, GraphStore.SCHEMA_VERSION):
            raise ValueError("Unsupported graph schema; preserve the database")
        rows = db.execute("SELECT scope FROM graph_meta LIMIT 2").fetchall()
        if rows != [(name[:-7],)]:
            raise ValueError("Stored scope identity does not match the selected file")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (
            "graph_worker" in tables
            and db.execute(
                "SELECT 1 FROM graph_worker WHERE expires>? LIMIT 1", (time.time(),)
            ).fetchone()
        ):
            raise ValueError("The selected scope still has a live worker lease; stop it and retry")
        evidence = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _EVIDENCE
            if table in tables
        }
        policy = {"version": 0, "exclusion_count": 0, "pending": False}
        if "graph_state" in tables:
            row = db.execute(
                "SELECT json_extract(value,'$.version'),json_array_length(value,'$.exclude') "
                "FROM graph_state WHERE key='policy'"
            ).fetchone()
            if row:
                if any(type(value) is not int or value < 0 for value in row):
                    raise ValueError("Stored policy summary is invalid; preserve the scope")
                policy.update(version=row[0], exclusion_count=row[1])
            policy["pending"] = bool(
                db.execute(
                    "SELECT 1 FROM graph_state WHERE key='restart_policy_pending' AND value='true'"
                ).fetchone()
            )
    with _ledger(root) as ledger:
        peers = ledger.execute(
            "SELECT name FROM scopes WHERE owner=? AND name<>? AND "
            "(project IS NULL OR ? IS NULL OR project=?)",
            (reservation["owner"], name, reservation["project"], reservation["project"]),
        ).fetchall()
    dependencies = 0
    unknown = 0
    for peer in peers:
        try:
            other = root / _scope(peer[0][:-7])
            for suffix in SIDECARS:
                _exact_file(Path(str(other) + suffix), required=not suffix)
            with closing(sqlite3.connect(other.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
                if db.execute("PRAGMA application_id").fetchone()[0] != GraphStore.APPLICATION_ID:
                    raise ValueError("Unknown peer")
                dependencies += bool(
                    db.execute(
                        "SELECT 1 FROM graph_state WHERE key='restart_policy_inputs' AND "
                        "json_type(value,?) IS NOT NULL",
                        ("$." + name[:-7],),
                    ).fetchone()
                )
        except (OSError, ValueError, sqlite3.DatabaseError):
            unknown += 1
    policy.update(
        dependent_scopes=dependencies,
        unknown_peer_scopes=unknown,
        recovery=_POLICY_RECOVERY,
    )
    return {
        "scope": name[:-7],
        "principal_digest": reservation["owner"],
        "project_digest": reservation["project"],
        "evidence": evidence,
        "policy": policy,
        "capacity_bytes": reservation["capacity"],
        "reserved_bytes": reservation["reserved"],
    }


def _selection_locks(stack: ExitStack, root: Path, name: str) -> None:
    try:
        stack.enter_context(file_lock(root / (name + ".lock"), wait=False))
        stack.enter_context(file_lock((root / name).with_suffix(".policy.lock"), wait=False))
    except (BlockingIOError, StorageBudgetError) as error:
        raise ValueError("The selected scope has a live writer or policy reader") from error


def list_scopes(
    root: Path, *, all_servers_stopped: bool = False, after: str = "", limit: int = 50
) -> dict:
    root = _root(root)
    if not 1 <= limit <= 100:
        raise ValueError("List limit must be between 1 and 100")
    if after:
        _scope(after)
    with _offline(root, all_servers_stopped), _ledger(root) as db:
        rows = db.execute(
            "SELECT name,owner,project,capacity,reserved FROM scopes WHERE name>? "
            "AND length(name)=71 AND substr(name,65)='.sqlite' "
            "AND substr(name,1,64) NOT GLOB '*[^0-9a-f]*' ORDER BY name LIMIT ?",
            (after + ".sqlite" if after else "", limit + 1),
        ).fetchall()
        scopes = [
            {
                "scope": row["name"][:-7],
                "principal_digest": row["owner"],
                "project_digest": row["project"],
                "reserved_bytes": row["reserved"],
            }
            for row in rows[:limit]
            if re.fullmatch(r"[a-f0-9]{64}\.sqlite", row["name"])
        ]
        return {
            "scopes": scopes,
            "next_after": rows[limit - 1]["name"][:-7] if len(rows) > limit else None,
            "recovery_required": (root / ARCHIVE_PENDING).exists(),
        }


def _validate_manifest(manifest: dict, root: Path, name: str, destination: Path) -> None:
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
        or manifest.get("root") != str(root)
        or manifest.get("name") != name
        or manifest.get("destination") != str(destination)
    ):
        raise ValueError(
            "Pending archival identity does not match; preserve it and resume its command"
        )
    records = manifest.get("files")
    if not isinstance(records, list) or not 1 <= len(records) <= len(SIDECARS):
        raise ValueError("Invalid archive file inventory")
    names = [record.get("name") for record in records if isinstance(record, dict)]
    if (
        len(names) != len(records)
        or any(not isinstance(item, str) for item in names)
        or len(set(names)) != len(names)
        or name not in names
        or any(item not in [name + suffix for suffix in SIDECARS] for item in names)
    ):
        raise ValueError("Invalid archive filenames; preserve the manifest")
    for record in records:
        if (
            set(record) != {"name", "bytes", "sha256"}
            or type(record["bytes"]) is not int
            or record["bytes"] < 0
            or not isinstance(record["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
        ):
            raise ValueError("Invalid archive checksum metadata")
    reservation = manifest.get("reservation")
    if (
        not isinstance(reservation, dict)
        or set(reservation) != {"owner", "project", "capacity", "reserved"}
        or not isinstance(reservation["owner"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", reservation["owner"])
        or (
            reservation["project"] is not None
            and (
                not isinstance(reservation["project"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", reservation["project"])
            )
        )
        or any(
            type(reservation[key]) is not int or reservation[key] <= 0
            for key in ("capacity", "reserved")
        )
    ):
        raise ValueError("Invalid archive reservation metadata")
    report = manifest.get("report")
    expected = {
        "scope": name[:-7],
        "principal_digest": reservation["owner"],
        "project_digest": reservation["project"],
        "files": records,
        "stored_bytes": sum(record["bytes"] for record in records),
        "capacity_bytes": reservation["capacity"],
        "reserved_bytes": reservation["reserved"],
    }
    if (
        not isinstance(report, dict)
        or set(report) != set(expected) | {"evidence", "policy"}
        or any(report[key] != value for key, value in expected.items())
    ):
        raise ValueError("Invalid archive inspection metadata")
    evidence, policy = report["evidence"], report["policy"]
    if (
        not isinstance(evidence, dict)
        or not set(evidence) <= set(_EVIDENCE)
        or any(type(value) is not int or value < 0 for value in evidence.values())
        or not isinstance(policy, dict)
        or set(policy)
        != {
            "version",
            "exclusion_count",
            "dependent_scopes",
            "unknown_peer_scopes",
            "pending",
            "recovery",
        }
        or any(
            type(policy[key]) is not int or policy[key] < 0
            for key in ("version", "exclusion_count", "dependent_scopes", "unknown_peer_scopes")
        )
        or type(policy["pending"]) is not bool
        or policy["recovery"] != _POLICY_RECOVERY
    ):
        raise ValueError("Invalid archive evidence or policy summary")


def _copy_file(source: Path, destination: Path, record: dict) -> None:
    if _exact_file(destination):
        _verify(destination, record)
        return
    _verify(source, record)
    temporary = destination.with_name(destination.name + ".partial")
    # Only our deterministic partial file is retryable; never overwrite a final copy.
    _exact_file(temporary)
    with source.open("rb") as source_stream, temporary.open("wb") as target:
        while chunk := source_stream.read(1024 * 1024):
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    _verify(temporary, {**record, "name": temporary.name})
    os.rename(temporary, destination)
    _sync_directory(destination.parent)


def _release_reservation(root: Path, manifest: dict) -> None:
    with _ledger(root, write=True) as db, db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT receipt FROM archived_scopes WHERE name=?", (manifest["name"],)
        ).fetchone()
        encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=True, allow_nan=False)
        if old:
            if old[0] != encoded:
                raise ValueError("Archive receipt conflicts with the ledger; preserve both")
            return
        row = db.execute(
            "SELECT owner,project,capacity,reserved FROM scopes WHERE name=?", (manifest["name"],)
        ).fetchone()
        if row is None or dict(row) != manifest["reservation"]:
            raise ValueError("Scope reservation changed; preserve the pending archive")
        db.execute(
            "INSERT INTO archived_scopes VALUES (?,?,?,?)",
            (manifest["name"], row["owner"], row["project"], encoded),
        )
        db.execute("DELETE FROM scopes WHERE name=?", (manifest["name"],))


def archive_scope(
    root: Path,
    scope: str,
    destination: Path,
    *,
    all_servers_stopped: bool = False,
    apply: bool = False,
) -> dict:
    """Inspect by default; applying or retrying requires a fully stopped directory."""
    root = _root(root)
    name = _scope(scope)
    destination = _destination(root, destination)
    with _offline(root, all_servers_stopped) as stack:
        _selection_locks(stack, root, name)
        pending = root / ARCHIVE_PENDING
        _exact_file(pending)
        if pending.exists():
            manifest = _load(pending)
        else:
            with _ledger(root) as db:
                archived = db.execute(
                    "SELECT receipt FROM archived_scopes WHERE name=?", (name,)
                ).fetchone()
                row = db.execute(
                    "SELECT owner,project,capacity,reserved FROM scopes WHERE name=?", (name,)
                ).fetchone()
            if archived:
                manifest = json.loads(archived[0])
                _validate_manifest(manifest, root, name, destination)
                for record in manifest["files"]:
                    _verify(destination / record["name"], record)
                return {
                    **manifest["report"],
                    "outcome": "already_archived",
                    "destination": str(destination),
                }
            if row is None or not re.fullmatch(r"[a-f0-9]{64}", row["owner"]):
                raise ValueError(
                    "Select an owned scope reservation; unknown files must be preserved"
                )
            reservation = dict(row)
            report = _database_report(root, name, reservation)
            if destination.exists():
                raise ValueError(
                    "Use a new archive directory; existing destination data is preserved"
                )
            files = [
                _digest(root / (name + suffix))
                for suffix in SIDECARS
                if (root / (name + suffix)).exists()
            ]
            report.update(files=files, stored_bytes=sum(item["bytes"] for item in files))
            manifest = {
                "version": 1,
                "root": str(root),
                "name": name,
                "destination": str(destination),
                "files": files,
                "reservation": reservation,
                "report": report,
            }
        _validate_manifest(manifest, root, name, destination)
        if not apply:
            return {
                **manifest["report"],
                "outcome": "recovery_required" if pending.exists() else "dry_run",
                "destination": str(destination),
            }
        if not pending.exists():
            _write_new(pending, manifest)
        destination.mkdir(mode=0o700, exist_ok=True)
        # Interrupted metadata writes and unknown files are preserved. Only the
        # exact manifest inventory and its named partial copies are ever changed.
        archive_manifest = destination / MANIFEST
        if archive_manifest.exists():
            if _load(archive_manifest) != manifest:
                raise ValueError("Archive manifest changed; preserve it")
        else:
            _write_new(archive_manifest, manifest)
        for record in manifest["files"]:
            _copy_file(root / record["name"], destination / record["name"], record)
        # No original is removed until every archive copy has been verified.
        for record in manifest["files"]:
            _verify(destination / record["name"], record)
        for record in manifest["files"]:
            source = root / record["name"]
            if _exact_file(source):
                _verify(source, record)
                source.unlink()
                _sync_directory(root)
        _release_reservation(root, manifest)
        pending.unlink()
        _sync_directory(root)
        return {**manifest["report"], "outcome": "archived", "destination": str(destination)}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("action", choices=("list", "inspect", "archive"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--scope")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--all-servers-stopped", action="store_true")
    parser.add_argument(
        "--apply", action="store_true", help="Commit the selected archival; default is dry-run"
    )
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=50)
    try:
        args = parser.parse_args(argv)
        if args.action == "list":
            if args.apply:
                raise ValueError("--apply is only valid for archive")
            result = list_scopes(
                args.directory,
                all_servers_stopped=args.all_servers_stopped,
                after=args.after,
                limit=args.limit,
            )
        else:
            if not args.scope or args.destination is None:
                raise ValueError("inspect/archive require --scope and --destination")
            if args.action == "inspect" and args.apply:
                raise ValueError("--apply is only valid for archive")
            result = archive_scope(
                args.directory,
                args.scope,
                args.destination,
                all_servers_stopped=args.all_servers_stopped,
                apply=args.apply,
            )
        print(json.dumps({"success": True, **result}, ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        message = (
            str(error)
            if isinstance(error, ValueError)
            else (
                "Filesystem or database operation failed; preserve all files and retry the same offline command"
            )
        )
        print(json.dumps({"success": False, "error": message}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
