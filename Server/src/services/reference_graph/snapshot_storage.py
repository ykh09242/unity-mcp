"""Shared immutable evidence for full-graph snapshots; legacy snapshots keep their IDs."""

from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3
from typing import Final

from services.reference_graph.store import _hash, _json
from services.reference_graph import reclamation
from services.reference_graph.response_budget import MAX_REPLAY_BYTES, PageBudget, bounded_rows


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
        "CREATE INDEX IF NOT EXISTS graph_snapshot_contents_backing ON graph_snapshot_contents(backing)",
        "CREATE INDEX IF NOT EXISTS graph_snapshot_versions_backing ON graph_snapshot_versions(backing)",
    ):
        connection.execute(statement)


def begin(connection: sqlite3.Connection, identity: str, version: SnapshotVersion) -> None:
    row = connection.execute(
        "SELECT backing FROM graph_snapshot_versions v WHERE version=? AND NOT EXISTS (SELECT 1 FROM graph_reclamation r WHERE r.kind='snapshot' AND r.identity=v.backing)",
        (version.key,),
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
            "SELECT backing FROM graph_snapshot_contents c WHERE digest=? AND NOT EXISTS (SELECT 1 FROM graph_reclamation r WHERE r.kind='snapshot' AND r.identity=c.backing)",
            (row[2],),
        ).fetchone()
        if existing:
            shared = existing[0]
            connection.execute(
                "UPDATE graph_snapshot_refs SET backing=? WHERE snapshot=?", (shared, identity)
            )
            # The final work page owns only its page and the alias switch. Retire
            # duplicate evidence atomically; maintenance reclaims it in batches.
            retire(connection, identity)
        else:
            connection.execute(
                "INSERT OR REPLACE INTO graph_snapshot_contents VALUES (?,?)", (row[2], identity)
            )
    connection.execute(
        "INSERT OR REPLACE INTO graph_snapshot_versions VALUES (?,?)", (row[1], shared)
    )


def replay(connection: sqlite3.Connection, result: dict) -> dict:
    if "_snapshot_after" not in result:
        return result
    after, count = result.pop("_snapshot_after"), result.pop("_snapshot_count")
    budget, edges = PageBudget(max_bytes=MAX_REPLAY_BYTES), []
    for row in bounded_rows(
        connection,
        "SELECT payload FROM graph_snapshot_edges WHERE snapshot=? AND id>? ORDER BY id LIMIT ?",
        (backing(connection, result["snapshot"]["id"]), after, count),
        max_bytes=MAX_REPLAY_BYTES,
    ):
        # Replay cannot shorten a previously delivered page or advance its cursor.
        budget.require(row)
        edges.append(json.loads(row["payload"]))
    result["edges"] = edges
    return result


def retire(connection: sqlite3.Connection, shared: str) -> None:
    """Stop advertising backing before any of its evidence is reclaimed."""
    reclamation.enqueue(connection, "snapshot", shared)


def discard(connection: sqlite3.Connection, identity: str) -> bool:
    shared = backing(connection, identity)
    connection.execute("DELETE FROM graph_snapshot_refs WHERE snapshot=?", (identity,))
    if not connection.execute(
        "SELECT 1 FROM graph_snapshot_refs WHERE backing=? LIMIT 1", (shared,)
    ).fetchone():
        retire(connection, shared)
    return connection.execute("DELETE FROM graph_snapshots WHERE id=?", (identity,)).rowcount > 0
