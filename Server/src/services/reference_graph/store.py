"""Revision-checked SQLite evidence, scoped by authenticated owner and Editor.

Callers supply a trusted server state directory and resolved identity, never an
arbitrary client database path. Each operation owns/closes its connection; SQLite
serializes writers across processes. Rebuild preserves authored declarations.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


class RevisionConflict(ValueError):
    """Read the current version before retrying a mutation or page."""


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GraphScope:
    user_id: str
    instance_id: str
    project_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in asdict(self).values()):
            raise ValueError("Graph identity requires user, instance and project")

    @property
    def key(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True, slots=True)
class Observation:
    owner: str
    provider: str
    fingerprint: str
    path: str = ""
    kind: str = "asset"
    force: bool = False
    source_version: int | None = None
    rules_revision: int = 0
    provenance: dict | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    target: str
    kind: str
    key: str = ""
    line: int = 0
    confidence: str = "observed"
    evidence: str = ""
    source_pointer: str = ""
    target_file_id: str = ""
    subobject: str = ""
    subobject_guid: str = ""
    target_type: str = ""

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str)
            for v in (
                self.target,
                self.kind,
                self.key,
                self.confidence,
                self.evidence,
                self.source_pointer,
                self.target_file_id,
                self.subobject,
                self.subobject_guid,
                self.target_type,
            )
        ):
            raise ValueError("Edge text fields must be strings")
        if not self.target or not self.kind or type(self.line) is not int or self.line < 0:
            raise ValueError("Edges require target, kind and nonnegative source line")


def check_owned_path(path: Path) -> Path:
    """Reject linked/reparse ancestors before touching server-owned state."""
    path = path.absolute()
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError("Reference graph state cannot use a linked path")
    return path


class GraphStore:
    APPLICATION_ID = 0x554D5247
    SCHEMA_VERSION = 2

    @classmethod
    def open(cls, root: Path, scope: GraphScope) -> GraphStore:
        return cls(Path(root) / (scope.key + ".sqlite"), scope)

    def __init__(self, path: Path, scope: GraphScope):
        if not isinstance(scope, GraphScope):
            raise ValueError("A resolved graph identity is required")
        self.scope = scope
        self.path = check_owned_path(Path(path))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        check_owned_path(self.path)
        if not self.path.exists():
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        self._initialize()

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = check_owned_path(Path(str(self.path) + suffix))
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                # SQLite can remove its sidecars when the last other handle closes.
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise ValueError("Reference graph files must be owned regular files")
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect(write=True) as connection:
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if application != self.APPLICATION_ID and (application != 0 or tables):
                raise ValueError("This database is not owned by the reference graph")
            if application == self.APPLICATION_ID:
                if connection.execute("PRAGMA user_version").fetchone()[0] not in (
                    1,
                    self.SCHEMA_VERSION,
                ):
                    raise ValueError("Unsupported reference graph schema; preserve the database")
                if (
                    connection.execute("SELECT scope FROM graph_meta").fetchone()[0]
                    != self.scope.key
                ):
                    raise ValueError("Reference graph identity does not match")
            else:
                statements = (
                    "CREATE TABLE graph_meta(scope TEXT NOT NULL, revision INTEGER NOT NULL, rules_revision INTEGER NOT NULL)",
                    "CREATE TABLE sources(provider TEXT PRIMARY KEY, version INTEGER NOT NULL, enabled INTEGER NOT NULL, config TEXT NOT NULL)",
                    "CREATE TABLE nodes(id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, present INTEGER NOT NULL)",
                    "CREATE TABLE observations(owner TEXT NOT NULL, provider TEXT NOT NULL, fingerprint TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, error TEXT, PRIMARY KEY(owner,provider))",
                    "CREATE TABLE edges(id TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)",
                    "CREATE INDEX edges_outgoing ON edges(owner,id)",
                    "CREATE INDEX edges_incoming ON edges(target,id)",
                    "CREATE INDEX observations_pending ON observations(state,provider,owner)",
                    "CREATE TABLE declarations(id TEXT PRIMARY KEY, version INTEGER NOT NULL, owner TEXT NOT NULL, payload TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE rules(id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)",
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute("INSERT INTO graph_meta VALUES (?,0,0)", (self.scope.key,))
                connection.executemany(
                    "INSERT INTO sources VALUES (?,1,1,'{}')",
                    [(p,) for p in ("unity", "code", "manual")],
                )
                connection.execute(f"PRAGMA application_id={self.APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS evidence_context(owner TEXT NOT NULL,provider TEXT NOT NULL,generation INTEGER NOT NULL,pending TEXT NOT NULL,published TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(owner,provider))"
            )
            connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
        self._ensure_wal()

    def _ensure_wal(self) -> None:
        # A journal-mode change can report SQLITE_BUSY immediately despite a busy
        # timeout when another opener begins a transaction after schema creation.
        # Existing WAL stores need no mode transition. Retry only this initial
        # transition with no active transaction/lock held by this connection.
        connection = sqlite3.connect(self.path, timeout=0)
        deadline = time.monotonic() + 30
        try:
            while True:
                try:
                    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                        connection.execute("PRAGMA journal_mode=WAL")
                    return
                except sqlite3.OperationalError as error:
                    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                    if (
                        code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.01)
        finally:
            connection.close()

    @staticmethod
    def _bump(connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE graph_meta SET revision=revision+1")

    def observe(
        self,
        owner: str,
        provider: str,
        fingerprint: str,
        *,
        path: str = "",
        kind: str = "asset",
        force: bool = False,
        source_version: int | None = None,
        rules_revision: int = 0,
    ) -> int:
        return self.observe_batch(
            [
                Observation(
                    owner, provider, fingerprint, path, kind, force, source_version, rules_revision
                )
            ]
        )[0]

    def observe_batch(self, observations: Iterable[Observation]) -> list[int]:
        with self._connect(write=True) as connection:
            return [self._observe(connection, observation) for observation in observations]

    def _observe(self, connection: sqlite3.Connection, observation: Observation) -> int:
        owner, provider, fingerprint = (
            observation.owner,
            observation.provider,
            observation.fingerprint,
        )
        path, kind, force = observation.path, observation.kind, observation.force
        if not owner or provider == "manual" or not fingerprint:
            raise ValueError("Generated observations require owner, provider and fingerprint")
        source = connection.execute(
            "SELECT enabled,version FROM sources WHERE provider=?", (provider,)
        ).fetchone()
        if source is None or not source[0]:
            raise ValueError("The source is not enabled")
        if provider.startswith("external:") and source[1] != observation.source_version:
            raise RevisionConflict("The external source grant changed before observation")
        if (
            provider != "unity"
            and not provider.startswith("runtime:")
            and connection.execute("SELECT rules_revision FROM graph_meta").fetchone()[0]
            != observation.rules_revision
        ):
            raise RevisionConflict("Analysis rules changed before observation")
        old = connection.execute(
            "SELECT * FROM observations WHERE owner=? AND provider=?", (owner, provider)
        ).fetchone()
        node = connection.execute("SELECT path,kind FROM nodes WHERE id=?", (owner,)).fetchone()
        moved = node is not None and ((path and node["path"] != path) or node["kind"] != kind)
        if (
            old is not None
            and old["fingerprint"] == fingerprint
            and old["state"] != "deleted"
            and not force
            and not moved
        ):
            return old["generation"]
        generation = old["generation"] + 1 if old is not None else 1
        if observation.provenance is not None:
            self._context(
                connection,
                owner,
                provider,
                generation,
                {**observation.provenance, **({"path": path} if path else {})},
            )
        connection.execute(
            "INSERT INTO observations VALUES (?,?,?,?,'pending',NULL) ON CONFLICT(owner,provider) DO UPDATE SET fingerprint=excluded.fingerprint,generation=excluded.generation,state='pending',error=NULL",
            (owner, provider, fingerprint, generation),
        )
        connection.execute(
            "INSERT INTO nodes VALUES (?,?,?,1) ON CONFLICT(id) DO UPDATE SET path=CASE WHEN excluded.path='' THEN nodes.path ELSE excluded.path END,kind=excluded.kind,present=1",
            (owner, path, kind),
        )
        self._bump(connection)
        return generation

    @staticmethod
    def _context(connection, owner: str, provider: str, generation: int, provenance: dict) -> None:
        connection.execute(
            "INSERT INTO evidence_context(owner,provider,generation,pending) VALUES (?,?,?,?) ON CONFLICT(owner,provider) DO UPDATE SET generation=excluded.generation,pending=excluded.pending",
            (owner, provider, generation, _json(provenance)),
        )

    @staticmethod
    def _publish_context(connection, owner: str, provider: str, generation: int) -> None:
        connection.execute(
            "UPDATE evidence_context SET published=CASE WHEN generation=? THEN pending ELSE '{}' END WHERE owner=? AND provider=?",
            (generation, owner, provider),
        )

    @staticmethod
    def _current(
        connection: sqlite3.Connection, owner: str, provider: str, generation: int
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM observations o JOIN sources s USING(provider) WHERE o.owner=? AND o.provider=? AND o.generation=? AND o.state IN ('pending','error') AND s.enabled=1",
                (owner, provider, generation),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _insert_edge(
        connection: sqlite3.Connection,
        owner: str,
        provider: str,
        edge: Edge,
        identity: str | None = None,
    ) -> None:
        payload = _json(asdict(edge))
        connection.execute(
            "INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?,?)",
            (
                identity or _hash((owner, provider, payload)),
                owner,
                provider,
                edge.target,
                edge.kind,
                payload,
            ),
        )

    def publish(self, owner: str, provider: str, generation: int, edges: Iterable[Edge]) -> bool:
        return self.publish_batch([(owner, provider, generation, edges)])[0]

    def publish_batch(
        self, publications: Iterable[tuple[str, str, int, Iterable[Edge]]]
    ) -> list[bool]:
        with self._connect(write=True) as connection:
            results = []
            for owner, provider, generation, edges in publications:
                current = self._current(connection, owner, provider, generation)
                results.append(current)
                if current:
                    connection.execute(
                        "DELETE FROM edges WHERE owner=? AND provider=?", (owner, provider)
                    )
                    for edge in edges:
                        self._insert_edge(connection, owner, provider, edge)
                    connection.execute(
                        "UPDATE observations SET state='ready',error=NULL WHERE owner=? AND provider=?",
                        (owner, provider),
                    )
                    self._publish_context(connection, owner, provider, generation)
                    self._bump(connection)
            return results

    def fail(self, owner: str, provider: str, generation: int, error: str) -> bool:
        with self._connect(write=True) as connection:
            if not self._current(connection, owner, provider, generation):
                return False
            connection.execute(
                "UPDATE observations SET state='error',error=? WHERE owner=? AND provider=?",
                (error, owner, provider),
            )
            self._bump(connection)
            return True

    def delete(self, owner: str) -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "UPDATE observations SET generation=generation+1,state='deleted',error=NULL WHERE owner=? AND provider<>'manual'",
                (owner,),
            )
            connection.execute("DELETE FROM edges WHERE owner=? AND provider<>'manual'", (owner,))
            connection.execute(
                "INSERT INTO nodes VALUES (?,'','unknown',0) ON CONFLICT(id) DO UPDATE SET present=0",
                (owner,),
            )
            self._bump(connection)

    def pending(self, limit: int = 100) -> list[dict]:
        if limit < 1:
            raise ValueError("Page size must be positive")
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT o.*,n.path,n.kind,s.version AS source_version,m.rules_revision FROM observations o JOIN sources s USING(provider) LEFT JOIN nodes n ON n.id=o.owner CROSS JOIN graph_meta m WHERE o.state='pending' AND s.enabled=1 ORDER BY o.provider,o.owner LIMIT ?",
                    (limit,),
                )
            ]

    def status(self) -> dict:
        with self._connect() as connection:
            revision = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            counts = dict(
                connection.execute(
                    "SELECT o.state,count(*) FROM observations o JOIN sources s USING(provider) WHERE s.enabled=1 GROUP BY o.state"
                )
            )
            return {
                "revision": revision,
                "pending": counts.get("pending", 0),
                "errors": counts.get("error", 0),
                "ready": counts.get("ready", 0),
                "queue_idle": not (counts.get("pending", 0) or counts.get("error", 0)),
                "initialized": bool(counts),
                "backend": "sqlite",
            }

    def query(
        self, node: str, *, direction: str = "outgoing", limit: int = 100, cursor: str | None = None
    ) -> dict:
        if direction not in ("outgoing", "incoming") or not 1 <= limit <= 10000:
            raise ValueError("Use incoming/outgoing and a page size from 1 to 10000")
        query_hash = _hash((self.scope.key, node, direction))
        position = None
        if cursor is not None:
            try:
                if len(cursor) > 1024:
                    raise ValueError("Cursor is too long")
                position = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if position["query"] != query_hash or not isinstance(position["last"], str):
                    raise ValueError("Cursor belongs to another query")
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid reference graph cursor") from error
        with self._connect() as connection:
            revision = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            if position is not None and position.get("revision") != revision:
                raise RevisionConflict("Graph changed between pages; restart the query")
            column = "owner" if direction == "outgoing" else "target"
            rows = connection.execute(
                f"SELECT e.*,o.state,n.present,c.published AS provenance FROM edges e JOIN sources s ON s.provider=e.provider LEFT JOIN observations o ON o.owner=e.owner AND o.provider=e.provider LEFT JOIN evidence_context c ON c.owner=e.owner AND c.provider=e.provider LEFT JOIN nodes n ON n.id=e.target WHERE e.{column}=? AND e.id>? AND s.enabled=1 ORDER BY e.id LIMIT ?",
                (node, position["last"] if position else "", limit + 1),
            ).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            edges = [
                {
                    **json.loads(row["payload"]),
                    "id": row["id"],
                    "owner": row["owner"],
                    "provider": row["provider"],
                    "provenance": json.loads(row["provenance"] or "{}"),
                    "stale": row["state"] != "ready" and row["provider"] != "manual",
                    "target_exists": None if row["present"] is None else bool(row["present"]),
                }
                for row in rows
            ]
            next_cursor = (
                base64.urlsafe_b64encode(
                    _json(
                        {"query": query_hash, "revision": revision, "last": rows[-1]["id"]}
                    ).encode("utf-8")
                ).decode("ascii")
                if more
                else None
            )
            return {"revision": revision, "edges": edges, "next_cursor": next_cursor}

    def set_source(
        self, provider: str, config: dict, *, expected_version: int, enabled: bool = True
    ) -> int:
        with self._connect(write=True) as connection:
            return self._set_source(connection, provider, config, expected_version, enabled)

    def _set_source(self, connection, provider, config, expected_version, enabled) -> int:
        if not provider.startswith("external:") or provider == "external:":
            raise ValueError("Only named external sources can be configured")
        payload = _json(config)
        old = connection.execute(
            "SELECT version FROM sources WHERE provider=?", (provider,)
        ).fetchone()
        if (old[0] if old else 0) != expected_version:
            raise RevisionConflict("Source configuration changed")
        version = expected_version + 1
        connection.execute(
            "INSERT INTO sources VALUES (?,?,?,?) ON CONFLICT(provider) DO UPDATE SET version=excluded.version,enabled=excluded.enabled,config=excluded.config",
            (provider, version, int(enabled), payload),
        )
        # A changed grant may narrow paths. Do not expose the previous grant's
        # cached data, even as stale evidence, while reconciliation runs.
        connection.execute("DELETE FROM edges WHERE provider=?", (provider,))
        connection.execute(
            "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider=? AND state<>'deleted'",
            (provider,),
        )
        self._bump(connection)
        return version

    def sources(self) -> list[dict]:
        with self._connect() as connection:
            return [
                {
                    "provider": row["provider"],
                    "version": row["version"],
                    "enabled": bool(row["enabled"]),
                    "config": json.loads(row["config"]),
                }
                for row in connection.execute("SELECT * FROM sources ORDER BY provider")
            ]

    def put_declaration(
        self, identity: str, owner: str, edge: Edge, *, expected_version: int
    ) -> int:
        if not identity or not owner:
            raise ValueError("Declaration identity and owner are required")
        edge = replace(edge, confidence="declared")
        with self._connect(write=True) as connection:
            old = connection.execute(
                "SELECT version FROM declarations WHERE id=?", (identity,)
            ).fetchone()
            if (old[0] if old else 0) != expected_version:
                raise RevisionConflict("Declaration changed")
            version = expected_version + 1
            connection.execute(
                "INSERT INTO declarations VALUES (?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET version=excluded.version,owner=excluded.owner,payload=excluded.payload,deleted=0",
                (identity, version, owner, _json(asdict(edge))),
            )
            self._insert_edge(connection, owner, "manual", edge, "declared:" + identity)
            self._bump(connection)
            return version

    def remove_declaration(self, identity: str, *, expected_version: int) -> int:
        with self._connect(write=True) as connection:
            old = connection.execute(
                "SELECT version,deleted FROM declarations WHERE id=?", (identity,)
            ).fetchone()
            if old is None or old[0] != expected_version or old[1]:
                raise RevisionConflict("Declaration changed or was removed")
            connection.execute(
                "UPDATE declarations SET version=version+1,deleted=1 WHERE id=?", (identity,)
            )
            connection.execute("DELETE FROM edges WHERE id=?", ("declared:" + identity,))
            self._bump(connection)
            return expected_version + 1

    def declarations(self, *, include_deleted: bool = False) -> list[dict]:
        with self._connect() as connection:
            return [
                {
                    "id": row["id"],
                    "owner": row["owner"],
                    "version": row["version"],
                    "deleted": bool(row["deleted"]),
                    "edge": json.loads(row["payload"]),
                }
                for row in connection.execute(
                    "SELECT * FROM declarations WHERE deleted=0 OR ? ORDER BY id",
                    (include_deleted,),
                )
            ]

    def put_rule(self, identity: str, payload: dict, *, expected_version: int) -> int:
        with self._connect(write=True) as connection:
            old = connection.execute("SELECT version FROM rules WHERE id=?", (identity,)).fetchone()
            if (old[0] if old else 0) != expected_version:
                raise RevisionConflict("Analysis rule changed")
            version = expected_version + 1
            connection.execute(
                "INSERT INTO rules VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload",
                (identity, version, _json(payload)),
            )
            connection.execute(
                "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider NOT IN ('manual','unity') AND provider NOT LIKE 'runtime:%' AND state<>'deleted'"
            )
            connection.execute("UPDATE graph_meta SET rules_revision=rules_revision+1")
            self._bump(connection)
            return version

    def rules(self) -> list[dict]:
        return self.rules_state()["rules"]

    def rules_state(self) -> dict:
        with self._connect() as connection:
            revision = connection.execute("SELECT rules_revision FROM graph_meta").fetchone()[0]
            rules = [
                {"id": row["id"], "version": row["version"], "config": json.loads(row["payload"])}
                for row in connection.execute("SELECT * FROM rules ORDER BY id")
            ]
            return {"revision": revision, "rules": rules}

    def rebuild(self) -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "DELETE FROM edges WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%'"
            )
            connection.execute(
                "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%' AND state<>'deleted'"
            )
            self._bump(connection)
