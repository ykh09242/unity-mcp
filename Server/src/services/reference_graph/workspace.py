"""Durable collector progress and derived analysis within one isolated graph DB."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
import json
import stat
import time

from services.reference_graph.analysis import Analysis
from services.reference_graph.external import _pattern
from services.reference_graph.store import GraphStore, Observation, RevisionConflict, _json


class GraphWorkspace:
    LEASE_SECONDS = 120

    def __init__(self, store: GraphStore):
        self.store = store
        with store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_workspace(version INTEGER NOT NULL)"
            )
            version = connection.execute("SELECT version FROM graph_workspace").fetchone()
            if version is not None and version[0] not in (1, 2, 3):
                raise ValueError("Unsupported graph workspace version")
            if version is None:
                for statement in (
                    "CREATE TABLE graph_worker(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires REAL NOT NULL)",
                    "CREATE TABLE graph_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
                    "CREATE TABLE graph_assets(guid TEXT PRIMARY KEY,path TEXT NOT NULL,payload TEXT NOT NULL,seen TEXT NOT NULL,dirty INTEGER NOT NULL)",
                    "CREATE INDEX graph_assets_path ON graph_assets(path)",
                    "CREATE INDEX graph_assets_dirty ON graph_assets(dirty,guid)",
                    "CREATE TABLE graph_documents(owner TEXT PRIMARY KEY,format TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL)",
                    "CREATE TABLE graph_analysis(owner TEXT NOT NULL,provider TEXT NOT NULL,coverage TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(owner,provider))",
                    "CREATE TABLE graph_issues(owner TEXT NOT NULL,provider TEXT NOT NULL,ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(owner,provider,ordinal))",
                    "CREATE TABLE graph_dependencies(owner TEXT NOT NULL,dependency TEXT NOT NULL,PRIMARY KEY(owner,dependency))",
                    "CREATE INDEX graph_dependents ON graph_dependencies(dependency,owner)",
                    "CREATE TABLE graph_changed_inputs(owner TEXT PRIMARY KEY)",
                    "CREATE TABLE graph_external(provider TEXT NOT NULL,owner TEXT NOT NULL,path TEXT NOT NULL,fingerprint TEXT NOT NULL,stamp TEXT NOT NULL,analysis_key TEXT NOT NULL,seen TEXT NOT NULL,PRIMARY KEY(provider,owner))",
                ):
                    connection.execute(statement)
                connection.execute("INSERT INTO graph_workspace VALUES (1)")
            if version is None or version[0] == 1:
                connection.execute(
                    "CREATE TABLE graph_asset_failures(guid TEXT PRIMARY KEY,attempts INTEGER NOT NULL,retry_at REAL NOT NULL)"
                )
                connection.execute("UPDATE graph_workspace SET version=2")
            if version is None or version[0] < 3:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_addressables(owner TEXT PRIMARY KEY,state TEXT NOT NULL,version TEXT,error TEXT)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS graph_addressables_state ON graph_addressables(state,version)"
                )
                for row in connection.execute("SELECT payload FROM graph_assets"):
                    self._addressables(connection, json.loads(row[0]))
                connection.execute("UPDATE graph_workspace SET version=3")

    @staticmethod
    def _addressables(connection, asset: dict) -> None:
        state = asset.get("addressables", "not_reported")
        error = asset.get("addressables_error")
        if error or state == "unavailable":
            state = (
                "unsupported"
                if error
                in {"MissingMethodException", "MissingFieldException", "MissingMemberException"}
                else "failed"
            )
        connection.execute(
            "INSERT OR REPLACE INTO graph_addressables VALUES (?,?,?,?)",
            ("asset:" + asset["guid"], state, asset.get("addressables_version"), error),
        )

    @staticmethod
    def _state(connection, key: str, fallback=None):
        row = connection.execute("SELECT value FROM graph_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else fallback

    @staticmethod
    def _set(connection, key: str, value) -> None:
        connection.execute(
            "INSERT INTO graph_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _json(value)),
        )

    def state(self, key: str, fallback=None):
        with self.store._connect() as connection:
            return self._state(connection, key, fallback)

    def progress(self) -> dict:
        return self.state("progress", {"phase": "not_initialized"})

    def ensure_analysis_version(self, worker: str, version: int) -> None:
        with self._write(worker) as connection:
            if self._state(connection, "analysis_version") == version:
                return
            connection.execute(
                "UPDATE graph_assets SET dirty=1 WHERE json_extract(payload,'$.source_format')<>'none'"
            )
            self._invalidate(
                connection,
                (row[0] for row in connection.execute("SELECT owner FROM graph_documents")),
            )
            self._set(connection, "analysis_version", version)
            self.store._bump(connection)

    def acquire(self, owner: str) -> bool:
        now = time.time()
        with self.store._connect(write=True) as connection:
            current = connection.execute(
                "SELECT owner,expires FROM graph_worker WHERE id=1"
            ).fetchone()
            if current and current[0] != owner and current[1] > now:
                return False
            connection.execute(
                "INSERT INTO graph_worker VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires=excluded.expires",
                (owner, now + self.LEASE_SECONDS),
            )
            return True

    def renew(self, owner: str) -> bool:
        with self.store._connect(write=True) as connection:
            return (
                connection.execute(
                    "UPDATE graph_worker SET expires=? WHERE id=1 AND owner=? AND expires>?",
                    (time.time() + self.LEASE_SECONDS, owner, time.time()),
                ).rowcount
                == 1
            )

    def release(self, owner: str) -> None:
        with self.store._connect(write=True) as connection:
            connection.execute("DELETE FROM graph_worker WHERE owner=?", (owner,))

    @contextmanager
    def _write(self, owner: str):
        with self.store._connect(write=True) as connection:
            row = connection.execute("SELECT owner,expires FROM graph_worker WHERE id=1").fetchone()
            if row is None or row[0] != owner or row[1] <= time.time():
                raise RevisionConflict("Reference reconciliation ownership changed")
            yield connection

    def set_progress(self, worker: str, value: dict) -> None:
        with self._write(worker) as connection:
            self._set(connection, "progress", value)

    def policy(self) -> dict:
        return self.state("policy", {"version": 0, "exclude": []})

    def admitted(self, path: str) -> bool:
        return not any(_pattern(pattern).fullmatch(path) for pattern in self.policy()["exclude"])

    def set_policy(self, policy: dict, *, expected_version: int) -> int:
        patterns = policy.get("exclude", [])
        if (
            set(policy) - {"exclude"}
            or not isinstance(patterns, list)
            or not all(isinstance(p, str) for p in patterns)
        ):
            raise ValueError("Policy accepts an exclude pattern list")
        for pattern in patterns:
            _pattern(pattern)
        with self.store._connect(write=True) as connection:
            current = self._state(connection, "policy", {"version": 0})
            if current["version"] != expected_version:
                raise RevisionConflict("Graph policy changed")
            self._set(connection, "policy", {"version": expected_version + 1, "exclude": patterns})
            # Retire the old worker before clearing its inputs. It cannot renew
            # or publish a pre-policy page while a fresh census is being admitted.
            connection.execute("DELETE FROM graph_worker")
            for table in (
                "graph_assets",
                "graph_documents",
                "graph_analysis",
                "graph_issues",
                "graph_dependencies",
                "graph_changed_inputs",
                "graph_external",
                "graph_asset_failures",
                "graph_addressables",
            ):
                connection.execute("DELETE FROM " + table)
            connection.execute("DELETE FROM edges WHERE provider<>'manual'")
            connection.execute(
                "UPDATE observations SET generation=generation+1,state='deleted',error=NULL WHERE provider<>'manual'"
            )
            self._set(connection, "progress", {"phase": "not_initialized"})
            self._set(connection, "analysis_dirty", True)
            self._set(
                connection, "catalog_revision", self._state(connection, "catalog_revision", 0) + 1
            )
            connection.execute("DELETE FROM graph_state WHERE key LIKE 'external_scan:%'")
            self.store._bump(connection)
            return expected_version + 1

    def rebuild(self) -> None:
        policy = self.policy()
        self.set_policy({"exclude": policy["exclude"]}, expected_version=policy["version"])

    def set_source(
        self, provider: str, config: dict, *, expected_version: int, enabled: bool = True
    ) -> int:
        with self.store._connect(write=True) as connection:
            version = self.store._set_source(
                connection, provider, config, expected_version, enabled
            )
            # Grant CAS, evidence retirement and worker fencing are one commit.
            # Old relative paths and diagnostics must never be reinterpreted under
            # a replacement root, even when both roots contain the same filename.
            connection.execute("DELETE FROM graph_worker")
            connection.execute(
                "UPDATE nodes SET present=0 WHERE id IN (SELECT owner FROM observations WHERE provider=?)",
                (provider,),
            )
            connection.execute(
                "UPDATE observations SET state='deleted' WHERE provider=?", (provider,)
            )
            for table in ("graph_analysis", "graph_issues", "graph_external"):
                connection.execute("DELETE FROM " + table + " WHERE provider=?", (provider,))
            connection.execute(
                "DELETE FROM graph_state WHERE key=?", ("external_scan:" + provider,)
            )
            progress = self._state(connection, "progress", {})
            progress["phase"] = "changes_pending"
            self._set(connection, "progress", progress)
            return version

    def observe(self, worker: str, observation: Observation) -> int:
        return self.observe_many(worker, [observation])[0]

    def observe_many(self, worker: str, observations: Iterable[Observation]) -> list[int]:
        with self._write(worker) as connection:
            generations = []
            for observation in observations:
                generations.append(self.store._observe(connection, observation))
                if observation.provider == "code":
                    self._set(connection, "analysis_dirty", True)
            return generations

    def observe_external(
        self, worker: str, observation: Observation, *, stamp: str, analysis_key: str, seen: str
    ) -> int:
        with self._write(worker) as connection:
            generation = self.store._observe(connection, observation)
            # Track an admitted owner before any later publication. A crash,
            # parse failure or grant retirement must not orphan its evidence.
            self._record_external(
                connection,
                [
                    (
                        observation.provider,
                        observation.owner,
                        observation.path,
                        observation.fingerprint,
                        stamp,
                        analysis_key,
                        seen,
                    )
                ],
            )
            return generation

    def record_page(
        self, worker: str, assets: Iterable[dict], progress: dict, seen: str, *, force: bool = True
    ) -> None:
        with self._write(worker) as connection:
            patterns = [
                _pattern(pattern)
                for pattern in self._state(connection, "policy", {"exclude": []})["exclude"]
            ]
            changed = []
            catalog_changed = False
            for asset in assets:
                if any(pattern.fullmatch(asset["path"]) for pattern in patterns):
                    continue
                guid, payload = asset["guid"], _json(asset)
                old = connection.execute(
                    "SELECT payload FROM graph_assets WHERE guid=?", (guid,)
                ).fetchone()
                dirty = force or old is None or json.loads(old[0])["path"] != asset["path"]
                if old is None or old[0] != payload:
                    catalog_changed = True
                    self._addressables(connection, asset)
                connection.execute(
                    "INSERT INTO graph_assets VALUES (?,?,?,?,?) ON CONFLICT(guid) DO UPDATE SET path=excluded.path,payload=excluded.payload,seen=excluded.seen,dirty=MAX(graph_assets.dirty,excluded.dirty)",
                    (guid, asset["path"], payload, seen, int(dirty)),
                )
                connection.execute(
                    "INSERT INTO nodes VALUES (?,?,?,1) ON CONFLICT(id) DO UPDATE SET path=excluded.path,kind=excluded.kind,present=1",
                    ("asset:" + guid, asset["path"], asset.get("kind", "asset")),
                )
                if dirty:
                    changed.append("asset:" + guid)
                    connection.execute(
                        "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE owner=? AND provider='unity'",
                        ("asset:" + guid,),
                    )
            if changed:
                self._invalidate(connection, changed)
            if catalog_changed:
                self._set(connection, "analysis_dirty", True)
                self._set(
                    connection,
                    "catalog_revision",
                    self._state(connection, "catalog_revision", 0) + 1,
                )
                connection.execute(
                    "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state='ready'"
                )
            if changed or catalog_changed:
                self.store._bump(connection)
            self._set(connection, "progress", progress)

    def _delete(self, connection, owner: str) -> None:
        connection.execute("DELETE FROM graph_addressables WHERE owner=?", (owner,))
        if owner.startswith("asset:"):
            connection.execute("DELETE FROM graph_asset_failures WHERE guid=?", (owner[6:],))
        connection.execute(
            "UPDATE observations SET generation=generation+1,state='deleted',error=NULL WHERE owner=? AND provider<>'manual'",
            (owner,),
        )
        connection.execute("DELETE FROM edges WHERE owner=? AND provider<>'manual'", (owner,))
        connection.execute("UPDATE nodes SET present=0 WHERE id=?", (owner,))
        connection.execute("DELETE FROM graph_documents WHERE owner=?", (owner,))
        connection.execute("DELETE FROM graph_analysis WHERE owner=?", (owner,))
        connection.execute("DELETE FROM graph_issues WHERE owner=?", (owner,))
        connection.execute("DELETE FROM graph_dependencies WHERE owner=?", (owner,))
        connection.execute("INSERT OR IGNORE INTO graph_changed_inputs VALUES (?)", (owner,))

    def finish_census(self, worker: str, seen: str, progress: dict) -> None:
        with self._write(worker) as connection:
            removed = [
                "asset:" + row[0]
                for row in connection.execute(
                    "SELECT guid FROM graph_assets WHERE seen<>?", (seen,)
                )
            ]
            self._invalidate(connection, removed)
            for owner in removed:
                self._delete(connection, owner)
            connection.execute("DELETE FROM graph_assets WHERE seen<>?", (seen,))
            if removed:
                self._set(connection, "analysis_dirty", True)
                self._set(
                    connection,
                    "catalog_revision",
                    self._state(connection, "catalog_revision", 0) + 1,
                )
                connection.execute(
                    "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state='ready'"
                )
                self.store._bump(connection)
            self._set(connection, "progress", progress)

    def remove_path(self, worker: str, path: str) -> None:
        with self._write(worker) as connection:
            rows = connection.execute(
                "SELECT guid FROM graph_assets WHERE path=? OR substr(path,1,?)=?",
                (path, len(path) + 1, path + "/"),
            ).fetchall()
            owners = ["asset:" + row[0] for row in rows]
            self._invalidate(connection, owners)
            for owner in owners:
                self._delete(connection, owner)
            connection.executemany(
                "DELETE FROM graph_assets WHERE guid=?", [(row[0],) for row in rows]
            )
            if rows:
                self._set(connection, "analysis_dirty", True)
                self._set(
                    connection,
                    "catalog_revision",
                    self._state(connection, "catalog_revision", 0) + 1,
                )
                connection.execute(
                    "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state='ready'"
                )
                self.store._bump(connection)

    def catalog(self) -> list[dict]:
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM graph_assets ORDER BY guid")
            ]

    def dirty_assets(self, limit: int = 100, *, after: str = "") -> list[dict]:
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT a.payload FROM graph_assets a LEFT JOIN graph_asset_failures f USING(guid) WHERE a.guid>? AND (a.dirty=1 OR f.retry_at<=?) ORDER BY a.guid LIMIT ?",
                    (after, time.time(), limit),
                )
            ]

    def clean_asset(self, worker: str, guid: str) -> None:
        with self._write(worker) as connection:
            connection.execute("UPDATE graph_assets SET dirty=0 WHERE guid=?", (guid,))
            if connection.execute(
                "DELETE FROM graph_asset_failures WHERE guid=?", (guid,)
            ).rowcount:
                # Callers created while this input was unavailable have no old
                # dependency edge. Resolve them against the recovered symbols.
                connection.execute(
                    "INSERT OR IGNORE INTO graph_changed_inputs VALUES (?)", ("asset:" + guid,)
                )
                self._set(connection, "analysis_dirty", True)

    @staticmethod
    def _failure(connection, owner: str, provider: str, error: Exception, reason: str) -> None:
        message = type(error).__name__ + ": " + str(error)
        connection.execute(
            "INSERT INTO observations VALUES (?,?, 'unavailable',1,'error',?) ON CONFLICT(owner,provider) DO UPDATE SET generation=generation+1,state='error',error=excluded.error",
            (owner, provider, message),
        )
        connection.execute(
            "INSERT INTO graph_analysis SELECT owner,provider,?,generation FROM observations WHERE owner=? AND provider=? ON CONFLICT(owner,provider) DO UPDATE SET coverage=excluded.coverage,generation=excluded.generation",
            (reason, owner, provider),
        )
        connection.execute(
            "DELETE FROM graph_issues WHERE owner=? AND provider=?", (owner, provider)
        )
        connection.execute(
            "INSERT INTO graph_issues VALUES (?,?,0,?)",
            (owner, provider, _json({"reason": reason, "error": message})),
        )

    def fail_asset(self, worker: str, asset: dict, error: Exception) -> None:
        with self._write(worker) as connection:
            guid, owner = asset["guid"], "asset:" + asset["guid"]
            old = connection.execute(
                "SELECT attempts FROM graph_asset_failures WHERE guid=?", (guid,)
            ).fetchone()
            attempts = old[0] + 1 if old else 1
            # Backoff bounds repeated failure cost, never an active job's lifetime.
            retry_at = time.time() + min(300, 5 * 2 ** min(attempts - 1, 6))
            connection.execute(
                "INSERT INTO graph_asset_failures VALUES (?,?,?) ON CONFLICT(guid) DO UPDATE SET attempts=excluded.attempts,retry_at=excluded.retry_at",
                (guid, attempts, retry_at),
            )
            connection.execute("UPDATE graph_assets SET dirty=0 WHERE guid=?", (guid,))
            self._invalidate(connection, [owner])
            self._failure(connection, owner, "unity", error, "collection_failed")
            self.store._bump(connection)

    def fail_external(
        self, worker: str, owner: str, provider: str, generation: int, error: Exception
    ) -> None:
        with self._write(worker) as connection:
            if not self.store._current(connection, owner, provider, generation):
                raise RevisionConflict("External source changed before failure publication")
            self._failure(connection, owner, provider, error, "analysis_failed")
            self.store._bump(connection)

    def blocked_analysis(self) -> set[str]:
        with self.store._connect() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "WITH RECURSIVE blocked(owner) AS (SELECT 'asset:'||guid FROM graph_asset_failures UNION SELECT d.owner FROM graph_dependencies d JOIN blocked b ON d.dependency=b.owner) SELECT owner FROM blocked"
                )
            }

    def save_document(
        self, worker: str, owner: str, format_: str, fingerprint: str, payload: dict
    ) -> bool:
        with self._write(worker) as connection:
            encoded = _json(payload)
            old = connection.execute(
                "SELECT fingerprint,format,payload FROM graph_documents WHERE owner=?", (owner,)
            ).fetchone()
            if (
                old is not None
                and old[0] == fingerprint
                and old[1] == format_
                and old[2] == encoded
            ):
                return False
            self._invalidate(connection, [owner])
            connection.execute(
                "INSERT INTO graph_documents VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET format=excluded.format,fingerprint=excluded.fingerprint,payload=excluded.payload",
                (owner, format_, fingerprint, encoded),
            )
            connection.execute("INSERT OR IGNORE INTO graph_changed_inputs VALUES (?)", (owner,))
            self._set(connection, "analysis_dirty", True)
            self.store._bump(connection)
            return True

    def documents(self) -> list[dict]:
        with self.store._connect() as connection:
            return [
                {
                    "owner": row[0],
                    "format": row[1],
                    "fingerprint": row[2],
                    "data": json.loads(row[3]),
                    "state": row[4],
                }
                for row in connection.execute(
                    "SELECT d.*,o.state FROM graph_documents d LEFT JOIN observations o ON o.owner=d.owner AND o.provider='code' ORDER BY d.owner"
                )
            ]

    def drop_document(self, worker: str, owner: str) -> None:
        with self._write(worker) as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM graph_documents WHERE owner=?", (owner,)
                ).fetchone()
                is None
            ):
                return
            self._invalidate(connection, [owner])
            connection.execute("DELETE FROM graph_documents WHERE owner=?", (owner,))
            connection.execute("DELETE FROM edges WHERE owner=? AND provider='code'", (owner,))
            connection.execute(
                "DELETE FROM graph_analysis WHERE owner=? AND provider='code'", (owner,)
            )
            connection.execute(
                "DELETE FROM graph_issues WHERE owner=? AND provider='code'", (owner,)
            )
            connection.execute("DELETE FROM graph_dependencies WHERE owner=?", (owner,))
            connection.execute(
                "UPDATE observations SET generation=generation+1,state='deleted',error=NULL WHERE owner=? AND provider='code'",
                (owner,),
            )
            connection.execute("INSERT OR IGNORE INTO graph_changed_inputs VALUES (?)", (owner,))
            self._set(connection, "analysis_dirty", True)
            self.store._bump(connection)

    def changed_inputs(self) -> set[str]:
        with self.store._connect() as connection:
            return {row[0] for row in connection.execute("SELECT owner FROM graph_changed_inputs")}

    @staticmethod
    def _invalidate(connection, owners: Iterable[str]) -> None:
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS graph_changed(id TEXT PRIMARY KEY)")
        connection.execute("DELETE FROM graph_changed")
        connection.executemany(
            "INSERT OR IGNORE INTO graph_changed VALUES (?)", [(owner,) for owner in owners]
        )
        connection.execute(
            "WITH RECURSIVE affected(id) AS (SELECT id FROM graph_changed UNION SELECT d.owner FROM graph_dependencies d JOIN affected a ON d.dependency=a.id) UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state<>'deleted' AND owner IN (SELECT id FROM affected)"
        )
        GraphWorkspace._set(connection, "analysis_dirty", True)

    def invalidate_dependents(self, worker: str, owners: Iterable[str]) -> None:
        with self._write(worker) as connection:
            self._invalidate(connection, owners)
            self._set(connection, "analysis_dirty", True)
            self.store._bump(connection)

    def publish(
        self, worker: str, owner: str, provider: str, generation: int, analysis: Analysis
    ) -> bool:
        return self.publish_many(worker, [(owner, provider, generation, analysis)])[0]

    def publish_many(self, worker: str, publications: Iterable[tuple]) -> list[bool]:
        with self._write(worker) as connection:
            return [self._publish(connection, *publication) for publication in publications]

    def _publish(
        self, connection, owner: str, provider: str, generation: int, analysis: Analysis
    ) -> bool:
        if not self.store._current(connection, owner, provider, generation):
            return False
        connection.execute("DELETE FROM edges WHERE owner=? AND provider=?", (owner, provider))
        for edge in analysis.edges:
            self.store._insert_edge(connection, owner, provider, edge)
        connection.execute(
            "UPDATE observations SET state='ready',error=NULL WHERE owner=? AND provider=?",
            (owner, provider),
        )
        connection.execute(
            "INSERT INTO graph_analysis VALUES (?,?,?,?) ON CONFLICT(owner,provider) DO UPDATE SET coverage=excluded.coverage,generation=excluded.generation",
            (owner, provider, analysis.coverage, generation),
        )
        connection.execute(
            "DELETE FROM graph_issues WHERE owner=? AND provider=?", (owner, provider)
        )
        connection.executemany(
            "INSERT INTO graph_issues VALUES (?,?,?,?)",
            ((owner, provider, i, _json(issue)) for i, issue in enumerate(analysis.unresolved)),
        )
        if provider == "code":
            connection.execute("DELETE FROM graph_dependencies WHERE owner=?", (owner,))
            connection.executemany(
                "INSERT OR IGNORE INTO graph_dependencies VALUES (?,?)",
                ((owner, dependency) for dependency in analysis.dependencies),
            )
        self.store._bump(connection)
        return True

    def finish_analysis(self, worker: str, rules_revision: int) -> None:
        with self._write(worker) as connection:
            if (
                connection.execute("SELECT rules_revision FROM graph_meta").fetchone()[0]
                != rules_revision
            ):
                raise RevisionConflict("Analysis rules changed during reconciliation")
            self._set(connection, "analysis_dirty", False)
            self._set(connection, "analyzed_rules_revision", rules_revision)
            connection.execute("DELETE FROM graph_changed_inputs")

    def diagnostics(
        self, owner: str, *, offset: int = 0, limit: int = 100, revision: int | None = None
    ) -> dict:
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Use nonnegative offset and page size from 1 to 1000")
        with self.store._connect() as connection:
            current = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            if (offset and revision is None) or (revision is not None and revision != current):
                raise RevisionConflict("Diagnostics changed; restart with offset zero")
            rows = connection.execute(
                "SELECT i.provider,i.payload FROM graph_issues i JOIN sources s USING(provider) WHERE i.owner=? AND s.enabled=1 ORDER BY i.provider,i.ordinal LIMIT ? OFFSET ?",
                (owner, limit + 1, offset),
            ).fetchall()
            return {
                "revision": connection.execute("SELECT revision FROM graph_meta").fetchone()[0],
                "coverage": [
                    dict(row)
                    for row in connection.execute(
                        "SELECT a.provider,a.coverage,o.state,o.error FROM graph_analysis a JOIN sources s USING(provider) LEFT JOIN observations o ON o.owner=a.owner AND o.provider=a.provider WHERE a.owner=? AND s.enabled=1 ORDER BY a.provider",
                        (owner,),
                    )
                ]
                + [
                    {"provider": "addressables", "coverage": "catalog_keys", **dict(row)}
                    for row in connection.execute(
                        "SELECT state,version,error FROM graph_addressables WHERE owner=?", (owner,)
                    )
                ],
                "issues": [{"provider": row[0], **json.loads(row[1])} for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None,
            }

    def counts(self) -> dict:
        with self.store._connect() as connection:
            return {
                "assets": connection.execute("SELECT count(*) FROM graph_assets").fetchone()[0],
                "dirty_assets": connection.execute(
                    "SELECT count(*) FROM graph_assets WHERE dirty=1"
                ).fetchone()[0],
                "failed_assets": connection.execute(
                    "SELECT count(*) FROM graph_asset_failures"
                ).fetchone()[0],
                "unresolved": connection.execute(
                    "SELECT count(*) FROM graph_issues i JOIN sources s USING(provider) WHERE s.enabled=1"
                ).fetchone()[0],
            }

    def external_inputs(self, provider: str) -> dict[str, dict]:
        with self.store._connect() as connection:
            return {
                row["path"]: dict(row)
                for row in connection.execute(
                    "SELECT e.*,o.state FROM graph_external e LEFT JOIN observations o USING(owner,provider) WHERE e.provider=?",
                    (provider,),
                )
            }

    def require_source(self, provider: str, version: int) -> None:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT enabled,version FROM sources WHERE provider=?", (provider,)
            ).fetchone()
            if row is None or not row[0] or row[1] != version:
                raise RevisionConflict("External source grant changed")

    @contextmanager
    def source_guard(self, provider: str, version: int):
        """Reuse a scoped handle, not a transaction or an authorization result."""
        with self.store._connect() as connection:
            connection.commit()
            connection.isolation_level = None
            identity = self.store.path.lstat()

            def require() -> None:
                current = self.store.path.lstat()
                if (
                    (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino)
                    or not stat.S_ISREG(current.st_mode)
                    or current.st_nlink > 1
                ):
                    raise RevisionConflict("Reference database changed during source scan")
                cursor = connection.execute(
                    "SELECT enabled,version FROM sources WHERE provider=?", (provider,)
                )
                try:
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
                if not rows or not rows[0][0] or rows[0][1] != version:
                    raise RevisionConflict("External source grant changed")

            yield require

    def external_input(self, provider: str, owner: str) -> dict | None:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM graph_external WHERE provider=? AND owner=?", (provider, owner)
            ).fetchone()
            return dict(row) if row else None

    def external_validation(self, pairs: Iterable[tuple[str, str]]) -> dict:
        """One request-local metadata snapshot, never a cached permission grant."""
        with self.store._connect() as connection:
            return {
                (row["provider"], row["owner"]): dict(row)
                for row in connection.execute(
                    "SELECT e.*,s.enabled,s.version,s.config FROM json_each(?) r JOIN graph_external e ON e.provider=json_extract(r.value,'$[0]') AND e.owner=json_extract(r.value,'$[1]') JOIN sources s USING(provider)",
                    (_json(list(pairs)),),
                )
            }

    def catalog_snapshot(self, known_revision: int | None = None) -> tuple[int, list[dict] | None]:
        with self.store._connect() as connection:
            revision = self._state(connection, "catalog_revision", 0)
            rows = (
                None
                if revision == known_revision
                else [
                    json.loads(row[0])
                    for row in connection.execute("SELECT payload FROM graph_assets ORDER BY guid")
                ]
            )
            return revision, rows

    def provider_status(self) -> dict:
        with self.store._connect() as connection:
            states, versions = {}, set()
            for state, version, count in connection.execute(
                "SELECT state,version,count(*) FROM graph_addressables GROUP BY state,version"
            ):
                states[state] = states.get(state, 0) + count
                if version is not None:
                    versions.add(version)
            result = {
                "addressables": {
                    "states": states,
                    "versions": sorted(versions),
                    "version_kind": "assembly",
                    "errors": states.get("failed", 0) + states.get("unsupported", 0),
                }
            }
            for provider, version in connection.execute(
                "SELECT provider,version FROM sources WHERE enabled=1 AND provider LIKE 'external:%'"
            ):
                state = self._state(connection, "external_scan:" + provider, {})
                if state.get("source_version") != version:
                    state = {}
                result[provider] = {
                    "state": "not_verified",
                    "last_verified_at": None,
                    **state,
                    "current": False,
                }
            return result

    def external_scan(self, worker: str, source: dict, values: dict) -> None:
        with self._write(worker) as connection:
            self._set(
                connection,
                "external_scan:" + source["provider"],
                {**values, "source_version": source["version"]},
            )

    def record_external(
        self,
        worker: str,
        provider: str,
        owner: str,
        path: str,
        fingerprint: str,
        stamp: str,
        analysis_key: str,
        seen: str,
    ) -> None:
        self.record_external_many(
            worker, [(provider, owner, path, fingerprint, stamp, analysis_key, seen)]
        )

    def record_external_many(self, worker: str, rows: Iterable[tuple]) -> None:
        with self._write(worker) as connection:
            self._record_external(connection, rows)

    @staticmethod
    def _record_external(connection, rows: Iterable[tuple]) -> None:
        connection.executemany(
            "INSERT INTO graph_external VALUES (?,?,?,?,?,?,?) ON CONFLICT(provider,owner) DO UPDATE SET path=excluded.path,fingerprint=excluded.fingerprint,stamp=excluded.stamp,analysis_key=excluded.analysis_key,seen=excluded.seen",
            rows,
        )

    def finish_external(self, worker: str, provider: str, seen: str) -> None:
        with self._write(worker) as connection:
            removed = connection.execute(
                "SELECT owner FROM graph_external WHERE provider=? AND seen<>?", (provider, seen)
            ).fetchall()
            for row in removed:
                self._delete(connection, row[0])
            connection.execute(
                "DELETE FROM graph_external WHERE provider=? AND seen<>?", (provider, seen)
            )
            if removed:
                self.store._bump(connection)
