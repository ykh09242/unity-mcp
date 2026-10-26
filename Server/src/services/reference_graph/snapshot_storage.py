"""Shared immutable evidence for full-graph snapshots; legacy snapshots keep their IDs."""

from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3
from typing import Final

from services.reference_graph.store import _hash, _json


EVIDENCE_FORMAT: Final = 1


@dataclass(frozen=True, slots=True)
class SnapshotVersion:
    revision: int
    sources: dict[str, int]
    schema: int

    @property
    def key(self) -> str:
        return _hash((EVIDENCE_FORMAT, asdict(self)))

    @property
    def seed(self) -> str:
        return _hash((EVIDENCE_FORMAT, self.sources))


def initialize(connection: sqlite3.Connection) -> None:
    # Empty auxiliary tables avoid a history-wide migration. Mutation incident
    # snapshots are partial graphs and must never enter the full-graph index.
    for statement in (
        "CREATE TABLE IF NOT EXISTS graph_snapshot_refs(snapshot TEXT PRIMARY KEY,backing TEXT NOT NULL,version TEXT NOT NULL,digest TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS graph_snapshot_refs_backing ON graph_snapshot_refs(backing)",
        "CREATE TABLE IF NOT EXISTS graph_snapshot_contents(digest TEXT PRIMARY KEY,backing TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS graph_snapshot_versions(version TEXT PRIMARY KEY,backing TEXT NOT NULL)",
    ):
        connection.execute(statement)


def begin(connection: sqlite3.Connection, identity: str, version: SnapshotVersion) -> None:
    row = connection.execute(
        "SELECT backing FROM graph_snapshot_versions WHERE version=?", (version.key,)
    ).fetchone()
    connection.execute(
        "INSERT INTO graph_snapshot_refs VALUES (?,?,?,?)",
        (identity, row[0] if row else identity, version.key, version.seed),
    )


def backing(connection: sqlite3.Connection, identity: str) -> str:
    row = connection.execute(
        "SELECT backing FROM graph_snapshot_refs WHERE snapshot=?", (identity,)
    ).fetchone()
    return row[0] if row else identity


def append(connection: sqlite3.Connection, identity: str, edges: list[dict]) -> None:
    row = connection.execute(
        "SELECT backing,digest FROM graph_snapshot_refs WHERE snapshot=?", (identity,)
    ).fetchone()
    if row is not None and row[0] != identity:
        return
    # Legacy interrupted captures retain their original identity backing.
    digest = row[1] if row else ""
    for edge in edges:
        payload = _json(edge)
        connection.execute(
            "INSERT INTO graph_snapshot_edges VALUES (?,?,?)", (identity, edge["id"], payload)
        )
        if digest:
            digest = hashlib.sha256(bytes.fromhex(digest) + payload.encode("utf-8")).hexdigest()
    if row is not None:
        connection.execute(
            "UPDATE graph_snapshot_refs SET digest=? WHERE snapshot=?", (digest, identity)
        )


def finish(connection: sqlite3.Connection, identity: str) -> None:
    row = connection.execute(
        "SELECT backing,version,digest FROM graph_snapshot_refs WHERE snapshot=?", (identity,)
    ).fetchone()
    if row is None:
        return
    shared = row[0]
    if shared == identity:
        existing = connection.execute(
            "SELECT backing FROM graph_snapshot_contents WHERE digest=?", (row[2],)
        ).fetchone()
        if existing:
            shared = existing[0]
            connection.execute(
                "UPDATE graph_snapshot_refs SET backing=? WHERE snapshot=?", (shared, identity)
            )
            connection.execute("DELETE FROM graph_snapshot_edges WHERE snapshot=?", (identity,))
        else:
            connection.execute(
                "INSERT INTO graph_snapshot_contents VALUES (?,?)", (row[2], identity)
            )
    connection.execute(
        "INSERT OR REPLACE INTO graph_snapshot_versions VALUES (?,?)", (row[1], shared)
    )


def replay(connection: sqlite3.Connection, result: dict) -> dict:
    if "_snapshot_after" not in result:
        return result
    after, count = result.pop("_snapshot_after"), result.pop("_snapshot_count")
    result["edges"] = [
        json.loads(row[0])
        for row in connection.execute(
            "SELECT payload FROM graph_snapshot_edges WHERE snapshot=? AND id>? ORDER BY id LIMIT ?",
            (backing(connection, result["snapshot"]["id"]), after, count),
        )
    ]
    return result


def discard(connection: sqlite3.Connection, identity: str) -> bool:
    shared = backing(connection, identity)
    connection.execute("DELETE FROM graph_snapshot_refs WHERE snapshot=?", (identity,))
    if not connection.execute(
        "SELECT 1 FROM graph_snapshot_refs WHERE backing=? LIMIT 1", (shared,)
    ).fetchone():
        connection.execute("DELETE FROM graph_snapshot_versions WHERE backing=?", (shared,))
        connection.execute("DELETE FROM graph_snapshot_contents WHERE backing=?", (shared,))
        connection.execute("DELETE FROM graph_snapshot_edges WHERE snapshot=?", (shared,))
    return connection.execute("DELETE FROM graph_snapshots WHERE id=?", (identity,)).rowcount > 0
