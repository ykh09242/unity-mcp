"""Durable collector progress and derived analysis within one isolated graph DB."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from itertools import chain
import json
import stat
import time
import threading

from services.reference_graph.analysis import Analysis, Catalog, CatalogLookups
from services.reference_graph import catalog_delta, document_delta, external_changes
from services.reference_graph.external import _excluded, _pattern
from services.reference_graph.ignore import GitIgnoreRules
from services.reference_graph.response_budget import PageBudget, bounded_rows
from services.reference_graph.storage_budget import file_lock
from services.reference_graph.store import (
    Edge,
    GraphStore,
    Observation,
    RevisionConflict,
    _hash,
    _json,
    check_graph_work,
)


class GraphWorkspace:
    LEASE_SECONDS = 120
    RESTART_POLICY_HINT = (
        "The previous collection exclusions could not be safely inherited. "
        "Use set_policy with expected_version=0 and the intended exclusions."
    )

    def __init__(self, store: GraphStore):
        self.store = store
        self.restart_policy_pending = False
        self._queries = None
        self._mutations = None
        self._admission_cache = None
        self._key_cache = None
        self._key_cache_lock = threading.Lock()
        with store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_workspace(version INTEGER NOT NULL)"
            )
            version = connection.execute("SELECT version FROM graph_workspace").fetchone()
            if version is not None and version[0] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
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
            if version is None or version[0] < 4:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_staging(worker TEXT NOT NULL,owner TEXT NOT NULL,generation INTEGER NOT NULL,identity TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,ordinal INTEGER NOT NULL,PRIMARY KEY(worker,owner,kind,identity))"
                )
                connection.execute("UPDATE graph_workspace SET version=4")
            if version is None or version[0] < 5:
                connection.execute(
                    "ALTER TABLE graph_documents ADD COLUMN compilation_signature TEXT"
                )
                connection.execute(
                    "UPDATE graph_documents SET compilation_signature=json_extract(payload,'$.build.binding_revision') WHERE format='csharp'"
                )
                connection.execute("UPDATE graph_workspace SET version=5")
            if version is None or version[0] < 6:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_collected_versions(guid TEXT PRIMARY KEY,payload TEXT NOT NULL)"
                )
                connection.execute("UPDATE graph_workspace SET version=6")
            if version is None or version[0] < 7:
                # Older publications did not record failed or excluded lookups.
                # Keep their evidence visible until the first catalog change.
                connection.execute(
                    "INSERT OR IGNORE INTO graph_dependencies SELECT owner,? FROM observations WHERE provider='code' AND state<>'deleted'",
                    (CatalogLookups.ANY,),
                )
                connection.execute("UPDATE graph_workspace SET version=7")
            if version is None or version[0] < 8:
                # Older idle graphs may have already admitted a target without
                # restoring its incoming dependencies. Repair them once.
                if version is not None and self._requeue_native_owners(connection):
                    connection.execute("DELETE FROM graph_worker")
                    self.store._bump(connection)
                connection.execute("UPDATE graph_workspace SET version=8")
            if version is None or version[0] < 9:
                catalog_delta.initialize(connection)
                connection.execute("UPDATE graph_workspace SET version=9")
            if version is None or version[0] < 10:
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS graph_asset_failures_retry ON graph_asset_failures(retry_at,guid)"
                )
                connection.execute("UPDATE graph_workspace SET version=10")
            if version is None or version[0] < 11:
                document_delta.initialize(connection)
                connection.execute("UPDATE graph_workspace SET version=11")
            if version is None or version[0] < 12:
                # Older schemas may already have private path-only identities.
                # Preserve them and backfill missing public inputs exactly once.
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_asset_paths(guid TEXT PRIMARY KEY,path TEXT NOT NULL,seen TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS graph_asset_paths_path ON graph_asset_paths(path,guid)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO graph_asset_paths SELECT guid,path,seen FROM graph_assets"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_blocked_keys(guid TEXT NOT NULL,scheme TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(guid,scheme,value))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS graph_external_paths(provider TEXT NOT NULL,owner TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(provider,owner))"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS graph_external_paths_owner ON graph_external_paths(owner)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO graph_external_paths SELECT provider,owner,path FROM graph_external"
                )
                connection.execute("UPDATE graph_workspace SET version=12")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_external_checkpoints(provider TEXT NOT NULL,owner TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(provider,owner))"
            )
            external_changes.initialize(connection)
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_runtime_schema'"
            ).fetchone():
                from services.reference_graph.runtime import initialize_bindings

                previous_runtime = connection.execute(
                    "SELECT version FROM graph_runtime_schema"
                ).fetchone()[0]
                if previous_runtime not in (1, 2):
                    raise ValueError("Unsupported runtime observation schema")
                initialize_bindings(connection)
                if previous_runtime < 2:
                    connection.execute("UPDATE graph_admission SET ready=0 WHERE id=1")
                    self.store._bump(connection)

    def mutations(self):
        """Receipt state belongs to the same user/Editor database as evidence."""
        if self._mutations is None:
            from services.reference_graph.mutations import MutationReceipts

            self._mutations = MutationReceipts(self)
        return self._mutations

    def require_restart_policy(self) -> None:
        if self.restart_policy_pending and self.state("restart_policy_pending"):
            raise RevisionConflict(self.RESTART_POLICY_HINT)

    def queries(self):
        """Lazily create scoped exploration state; the handle owns no connection."""
        if self._queries is None:
            from services.reference_graph.queries import GraphQueries

            self._queries = GraphQueries(self.store)
        return self._queries

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
    def _state(connection, key: str, fallback=None, *, bounded: bool = False):
        if bounded:
            row = bounded_rows(
                connection, "SELECT value AS payload FROM graph_state WHERE key=?", (key,)
            ).fetchone()
            if row is not None:
                PageBudget().require(row)
        else:
            row = connection.execute("SELECT value FROM graph_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else fallback

    @staticmethod
    def _set(connection, key: str, value) -> None:
        connection.execute(
            "INSERT INTO graph_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _json(value)),
        )

    def state(self, key: str, fallback=None, *, bounded: bool = False):
        with self.store._connect() as connection:
            return self._state(connection, key, fallback, bounded=bounded)

    def progress(self, *, bounded: bool = False) -> dict:
        return self.state("progress", {"phase": "not_initialized"}, bounded=bounded)

    def ensure_analysis_version(self, worker: str, version: int) -> None:
        with self._write(worker) as connection:
            previous = self._state(connection, "analysis_version")
            if previous == version:
                return
            if previous in (7, 8, 9, 10) and previous < version <= 11:
                # Versions 8 through 11 change C# lexical analysis only. Failed YAML
                # scans were never published and remain in the retry queue.
                connection.execute(
                    "UPDATE graph_assets SET dirty=1 WHERE json_extract(payload,'$.source_format')='csharp'"
                )
                owners = connection.execute(
                    "SELECT 'asset:' || guid FROM graph_assets WHERE json_extract(payload,'$.source_format')='csharp'"
                )
            else:
                connection.execute(
                    "UPDATE graph_assets SET dirty=1 WHERE json_extract(payload,'$.source_format')<>'none'"
                )
                owners = connection.execute("SELECT owner FROM graph_documents")
            self._invalidate(connection, (row[0] for row in owners))
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
            connection.execute("DELETE FROM graph_staging WHERE worker<>?", (owner,))
            return True

    def ensure_compilation_context(
        self, worker: str, context: dict | None, *, checked: bool = False
    ) -> None:
        with self._write(worker) as connection:
            if self._state(connection, "compilation_context") == context:
                return
            if not checked:
                connection.execute(
                    "UPDATE graph_assets SET dirty=1 WHERE json_extract(payload,'$.source_format')='csharp'"
                )
                connection.execute(
                    "DELETE FROM graph_asset_failures WHERE guid IN (SELECT guid FROM graph_assets WHERE json_extract(payload,'$.source_format')='csharp')"
                )
                self._invalidate(
                    connection,
                    (
                        row[0]
                        for row in connection.execute(
                            "SELECT owner FROM graph_documents WHERE format='csharp'"
                        )
                    ),
                )
            self._set(connection, "compilation_context", context)
            self.store._bump(connection)

    def compilation_candidates(self, after: str = "") -> list[dict]:
        with self.store._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT a.guid,a.path,d.compilation_signature AS signature FROM graph_assets a LEFT JOIN graph_documents d ON d.owner='asset:'||a.guid WHERE a.guid>? AND a.dirty=0 AND json_extract(a.payload,'$.source_format')='csharp' ORDER BY a.guid LIMIT 256",
                    (after,),
                )
            ]

    def dirty_compilation(self, worker: str, guids: list[str]) -> None:
        with self._write(worker) as connection:
            for guid in guids:
                connection.execute("UPDATE graph_assets SET dirty=1 WHERE guid=?", (guid,))
                connection.execute("DELETE FROM graph_asset_failures WHERE guid=?", (guid,))
            self._invalidate(connection, ("asset:" + guid for guid in guids))
            self.store._bump(connection)

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
            connection.execute("DELETE FROM graph_staging WHERE worker=?", (owner,))
            connection.execute("DELETE FROM graph_worker WHERE owner=?", (owner,))

    @contextmanager
    def _write(self, owner: str):
        with self.store._connect(write=True) as connection:
            self._require_worker(connection, owner)
            yield connection

    @staticmethod
    def _require_worker(connection, owner: str) -> None:
        row = connection.execute("SELECT owner,expires FROM graph_worker WHERE id=1").fetchone()
        if row is None or row[0] != owner or row[1] <= time.time():
            raise RevisionConflict("Reference reconciliation ownership changed")

    def set_progress(self, worker: str, value: dict) -> None:
        with self._write(worker) as connection:
            self._set(connection, "progress", value)

    def record_error(self, worker: str, error: str, *, defer_folder_proof: bool) -> dict:
        with self._write(worker) as connection:
            progress = self._state(connection, "progress", {})
            progress.update(phase="error", error=error)
            self._set(connection, "progress", progress)
            scan = self._state(connection, "folder_scan")
            if scan:
                scan["defer_partial_proof"] = defer_folder_proof
                self._set(connection, "folder_scan", scan)
            return progress

    def policy(self, *, bounded: bool = False) -> dict:
        return {
            "respect_gitignore": True,
            **self.state("policy", {"version": 0, "exclude": []}, bounded=bounded),
        }

    def _admission(self, connection, *, bounded: bool = False, exclusions_only: bool = False):
        policy = {
            "respect_gitignore": True,
            **self._state(connection, "policy", {"version": 0, "exclude": []}, bounded=bounded),
        }
        key = (policy["version"], self._state(connection, "ignore_key", bounded=bounded))
        cache = self._admission_cache
        if cache is None or cache[0] != key:
            files = (
                self._state(connection, "ignore_files", [], bounded=bounded)
                if policy["respect_gitignore"]
                else []
            )
            cache = (
                key,
                GitIgnoreRules(files),
                [_pattern(pattern) for pattern in policy["exclude"]],
            )
            self._admission_cache = cache
        _, rules, patterns = cache
        if exclusions_only:
            return bool(patterns or rules.has_exclusions())
        return lambda path: not _excluded(path, patterns) and not rules.ignored(path)

    def admitted(self, path: str) -> bool:
        with self.store._connect() as connection:
            return self._admission(connection)(path)

    def admission(self):
        """Capture one compiled policy for a bounded request or reconciliation batch."""
        with self.store._connect() as connection:
            return self._admission(connection)

    def has_exclusions(self, *, bounded: bool = False) -> bool:
        with self.store._connect() as connection:
            return self._admission(connection, bounded=bounded, exclusions_only=True)

    def ignore_status(self, *, bounded: bool = False) -> dict:
        return self.state("ignore_status", {}, bounded=bounded)

    def delivery_fence(self) -> tuple:
        """Bind a produced result to its graph and effective-admission generation."""
        with self.store._connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT m.revision,a.revision,a.ready,COALESCE((SELECT json_extract(value,'$.version') FROM graph_state WHERE key='policy'),0),(SELECT value FROM graph_state WHERE key='ignore_key') FROM graph_meta m CROSS JOIN graph_admission a WHERE a.id=1"
                ).fetchone()
            )

    def ignore_current(self, editor: dict, *, bounded: bool = False) -> bool:
        with self.store._connect() as connection:
            policy = self._state(connection, "policy", {}, bounded=bounded)
            if not policy.get("respect_gitignore", True):
                return bool(
                    connection.execute("SELECT ready FROM graph_admission WHERE id=1").fetchone()[0]
                )
            known = self._state(connection, "ignore_status", {}, bounded=bounded)
            return (
                editor.get("ignore_state") == "available"
                and known.get("epoch") == editor.get("epoch")
                and known.get("ignore_revision") == editor.get("ignore_revision")
                and bool(
                    connection.execute("SELECT ready FROM graph_admission WHERE id=1").fetchone()[0]
                )
            )

    def block_admission(self) -> None:
        """Fence cached results while current ignore rules are unknown or unavailable."""
        with self.store._connect(write=True) as connection:
            if connection.execute("SELECT ready FROM graph_admission WHERE id=1").fetchone()[0]:
                connection.execute("UPDATE graph_admission SET ready=0 WHERE id=1")
                self.store._bump(connection)

    def invalidate_ignore(
        self, known: dict | None = None, policy_version: int | None = None
    ) -> None:
        """Retire an expired native token so a retry acquires a fresh rule snapshot."""
        with self.store._connect(write=True) as connection:
            if known is not None:
                current = self._state(connection, "ignore_status", bounded=True)
                version = connection.execute(
                    "SELECT COALESCE((SELECT json_extract(value,'$.version') FROM graph_state WHERE key='policy'),0)"
                ).fetchone()[0]
                if current is None or _json(current) != _json(known) or version != policy_version:
                    return
            connection.execute("DELETE FROM graph_state WHERE key='ignore_status'")
            connection.execute("UPDATE graph_admission SET ready=0 WHERE id=1")
            self.store._bump(connection)

    def _reset_static(self, connection) -> None:
        catalog_delta.reset(connection)
        document_delta.reset(connection)
        for table in (
            "graph_assets",
            "graph_documents",
            "graph_analysis",
            "graph_issues",
            "graph_dependencies",
            "graph_changed_inputs",
            "graph_external",
            "graph_external_checkpoints",
            "graph_external_watch",
            "graph_external_events",
            "graph_asset_failures",
            "graph_addressables",
            "graph_collected_versions",
        ):
            connection.execute("DELETE FROM " + table)
        connection.execute(
            "DELETE FROM edges WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%'"
        )
        connection.execute(
            "DELETE FROM evidence_context WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%'"
        )
        connection.execute(
            "UPDATE observations SET generation=generation+1,state='deleted',error=NULL WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%'"
        )
        self._set(connection, "progress", {"phase": "not_initialized"})
        self._set(connection, "analysis_dirty", True)
        self._set(
            connection, "catalog_revision", self._state(connection, "catalog_revision", 0) + 1
        )
        connection.execute(
            "DELETE FROM graph_state WHERE key IN ('folder_scan','change_page','retry_scan') OR key LIKE 'external_scan:%'"
        )

    def _refresh_admission(self, connection, *, runtime: bool = False) -> None:
        admitted = self._admission(connection)
        external = list(
            connection.execute(
                "SELECT id,path FROM graph_excluded_nodes WHERE id LIKE 'external:%'"
            )
        )
        paths = {
            row[0]: row[1] for row in connection.execute("SELECT id,path FROM graph_excluded_nodes")
        }
        paths.update(
            {
                row[0]: row[1]
                for row in connection.execute("SELECT id,path FROM nodes WHERE path<>''")
            }
        )
        paths.update(
            ("asset:" + row[0], row[1])
            for row in connection.execute("SELECT guid,path FROM graph_asset_paths")
        )
        connection.execute("DELETE FROM graph_excluded_nodes")
        excluded = [
            (owner, path)
            for owner, path in paths.items()
            if not owner.startswith("external:") and path and not admitted(path)
        ]
        excluded.extend((row[0], row[1]) for row in external)
        patterns = [
            _pattern(pattern)
            for pattern in self._state(connection, "policy", {"exclude": []})["exclude"]
        ]
        excluded_ids = {owner for owner, _ in excluded}
        excluded.extend(
            (owner, path)
            for owner, path in connection.execute("SELECT owner,path FROM graph_external_paths")
            if owner not in excluded_ids and _excluded(path, patterns)
        )
        connection.executemany("INSERT INTO graph_excluded_nodes VALUES (?,?)", excluded)
        connection.execute(
            "INSERT OR IGNORE INTO graph_excluded_nodes SELECT e.owner,x.path FROM edges e JOIN graph_excluded_nodes x ON x.id=e.target WHERE e.provider LIKE 'runtime:%'"
        )
        connection.execute("DELETE FROM nodes WHERE id IN (SELECT id FROM graph_excluded_nodes)")
        connection.execute(
            "DELETE FROM edges WHERE provider<>'manual' AND provider NOT LIKE 'runtime:%' AND (owner IN (SELECT id FROM graph_excluded_nodes) OR target IN (SELECT id FROM graph_excluded_nodes))"
        )
        if runtime:
            self._refresh_runtime_admission(connection, admitted)

    def _refresh_runtime_admission(self, connection, admitted) -> None:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_runtime_schema'"
        ).fetchone():
            return
        from services.reference_graph.runtime import initialize_bindings, runtime_admitted

        initialize_bindings(connection)
        catalog = Catalog(self._catalog_rows(connection))
        blocked_handles = {
            (row["session"], row["handle"])
            for row in connection.execute("SELECT * FROM graph_runtime_bindings")
            if not runtime_admitted(dict(row), admitted, catalog)
        }
        connection.execute(
            "CREATE TEMP TABLE runtime_admission_stage(id TEXT PRIMARY KEY,path TEXT NOT NULL)"
        )
        try:
            cursor = connection.execute(
                "SELECT e.owner,e.provider,e.payload,c.published,x.id AS excluded_target FROM edges e LEFT JOIN evidence_context c ON c.owner=e.owner AND c.provider=e.provider LEFT JOIN graph_excluded_nodes x ON x.id=e.target WHERE e.provider LIKE 'runtime:%'"
            )
            while batch := cursor.fetchmany(256):
                excluded = []
                for row in batch:
                    edge = json.loads(row["payload"])
                    provenance = json.loads(row["published"] or "{}")
                    evidence = json.loads(edge.get("evidence") or "{}")
                    observation = {
                        "owner_path": provenance.get("path", ""),
                        "target_path": provenance.get("target_path", ""),
                        "scheme": evidence.get("scheme", ""),
                        "key": edge.get("key", ""),
                    }
                    if (
                        not runtime_admitted(observation, admitted, catalog)
                        or (row["provider"][8:], evidence.get("handle", "")) in blocked_handles
                        or row["excluded_target"] is not None
                    ):
                        excluded.append((row["owner"], ""))
                connection.executemany(
                    "INSERT OR IGNORE INTO runtime_admission_stage VALUES (?,?)", excluded
                )
            old = "SELECT x.id FROM graph_excluded_nodes x JOIN observations o ON o.owner=x.id WHERE o.provider LIKE 'runtime:%'"
            changed = (
                connection.execute(
                    "SELECT 1 FROM (SELECT id FROM runtime_admission_stage EXCEPT "
                    + old
                    + ") LIMIT 1"
                ).fetchone()
                or connection.execute(
                    "SELECT 1 FROM ("
                    + old
                    + " EXCEPT SELECT id FROM runtime_admission_stage) LIMIT 1"
                ).fetchone()
            )
            connection.execute(
                "DELETE FROM graph_excluded_nodes WHERE id IN (SELECT owner FROM observations WHERE provider LIKE 'runtime:%')"
            )
            connection.execute(
                "INSERT INTO graph_excluded_nodes SELECT * FROM runtime_admission_stage"
            )
            connection.execute(
                "DELETE FROM nodes WHERE id IN (SELECT id FROM runtime_admission_stage)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO nodes SELECT o.owner,COALESCE(json_extract(c.published,'$.path'),''),'runtime_observation',1 FROM observations o LEFT JOIN evidence_context c USING(owner,provider) WHERE o.provider LIKE 'runtime:%' AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=o.owner)"
            )
            if changed:
                connection.execute(
                    "UPDATE graph_admission SET revision=(SELECT revision+1 FROM graph_meta) WHERE id=1"
                )
        finally:
            connection.execute("DROP TABLE runtime_admission_stage")

    def set_ignore_rules(self, worker: str, files: list[tuple[str, str]], status: dict) -> bool:
        """Commit verified native rules and retire every generated pre-policy publication."""
        key = _hash(files)
        with self._write(worker) as connection:
            changed = self._state(connection, "ignore_key") != key
            self._set(connection, "ignore_files", files)
            self._set(connection, "ignore_key", key)
            self._set(connection, "ignore_status", status)
            if changed:
                self._reset_static(connection)
                self._refresh_admission(connection)
                self.store._bump(connection)
                connection.execute(
                    "UPDATE graph_admission SET revision=(SELECT revision FROM graph_meta),ready=0 WHERE id=1"
                )
            elif connection.execute(
                "UPDATE graph_admission SET ready=1 WHERE id=1 AND ready=0 AND NOT EXISTS(SELECT 1 FROM graph_state WHERE key='folder_scan')"
            ).rowcount:
                # Restored visibility must not reuse a revision captured while
                # admission was blocked, even when the rules themselves match.
                self.store._bump(connection)
            return changed

    def finish_admission(self, worker: str) -> None:
        with self._write(worker) as connection:
            connection.execute("DELETE FROM graph_state WHERE key='folder_scan'")
            if not connection.execute("SELECT ready FROM graph_admission WHERE id=1").fetchone()[0]:
                self._refresh_admission(connection, runtime=True)
                connection.execute("UPDATE graph_admission SET ready=1 WHERE id=1")
                self.store._bump(connection)

    def set_policy(self, policy: dict, *, expected_version: int) -> int:
        version, _ = self._set_policy(policy, expected_version=expected_version)
        return version

    @contextmanager
    def requested_page_guard(self):
        """Serialize one promoted read with effective policy changes across scoped servers."""
        with ExitStack() as acquired:
            try:
                acquired.enter_context(
                    file_lock(self.store.path.with_suffix(".policy.lock"), wait=False)
                )
            except BlockingIOError as error:
                raise RevisionConflict("Reference collection policy is in use; retry") from error
            yield

    def _set_policy(
        self, policy: dict, *, expected_version: int, _rebuild: bool = False
    ) -> tuple[int, bool]:
        patterns = policy.get("exclude", [])
        if (
            set(policy) - {"exclude", "respect_gitignore"}
            or not isinstance(patterns, list)
            or not all(isinstance(p, str) for p in patterns)
            or type(policy.get("respect_gitignore", True)) is not bool
        ):
            raise ValueError("Policy accepts an exclude pattern list")
        for pattern in patterns:
            _pattern(pattern)
        replacement = {
            "version": expected_version + 1,
            "exclude": patterns,
            "respect_gitignore": policy.get("respect_gitignore", True),
        }
        with ExitStack() as guard, self.store._connect(write=True, public_edit=True) as connection:
            # Replacement must remain possible for legacy oversized policies without
            # loading their payload. The writer transaction still owns the CAS.
            current, authored = connection.execute(
                "SELECT COALESCE((SELECT json_extract(value,'$.version') FROM graph_state WHERE key='policy'),0),"
                "EXISTS(SELECT 1 FROM graph_state WHERE key='policy')"
            ).fetchone()
            if current != expected_version:
                raise RevisionConflict("Graph policy changed")
            # Apply the same raw JSON row admission as every bounded policy read
            # before changing authored state or retiring collection ownership.
            PageBudget().require(
                bounded_rows(connection, "SELECT ? AS payload", (_json(replacement),)).fetchone()
            )
            # Compare inside SQLite so a legacy oversized value never crosses the
            # connection boundary. Unknown authored fields are not equivalent.
            identical = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM graph_state WHERE key='policy' "
                "AND json_type(value)='object' "
                "AND json_remove(value,'$.version','$.exclude','$.respect_gitignore')='{}' "
                "AND COALESCE(json_type(value,'$.exclude'),'array')='array' "
                "AND COALESCE(json_extract(value,'$.exclude'),'[]')=json(?) "
                "AND COALESCE(json_type(value,'$.respect_gitignore'),'true')=?)",
                (_json(patterns), "true" if replacement["respect_gitignore"] else "false"),
            ).fetchone()[0]
            if not _rebuild:
                connection.execute("DELETE FROM graph_state WHERE key='restart_policy_pending'")
                if identical:
                    return current, False
                if not authored and not patterns and replacement["respect_gitignore"]:
                    self._set(connection, "policy", replacement)
                    return expected_version + 1, False
            guard.enter_context(self.requested_page_guard())
            self._set(connection, "policy", replacement)
            if _rebuild:
                connection.execute("DELETE FROM graph_state WHERE key='restart_from'")
            # Retire the old worker before clearing its inputs. It cannot renew
            # or publish a pre-policy page while a fresh census is being admitted.
            connection.execute("DELETE FROM graph_worker")
            self._reset_static(connection)
            self._refresh_admission(connection)
            self.store._bump(connection)
            connection.execute(
                "UPDATE graph_admission SET revision=(SELECT revision FROM graph_meta),ready=0 WHERE id=1"
            )
            return expected_version + 1, True

    def rebuild(self) -> None:
        policy = self.policy()
        self._set_policy(
            {key: policy[key] for key in ("exclude", "respect_gitignore")},
            expected_version=policy["version"],
            _rebuild=True,
        )

    def set_source(
        self, provider: str, config: dict, *, expected_version: int, enabled: bool = True
    ) -> int:
        with self.store._connect(write=True, public_edit=True) as connection:
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
            for table in (
                "graph_analysis",
                "graph_issues",
                "graph_external",
                "graph_external_checkpoints",
                "graph_external_watch",
                "graph_external_events",
            ):
                connection.execute("DELETE FROM " + table + " WHERE provider=?", (provider,))
            connection.execute("DELETE FROM graph_external_paths WHERE provider=?", (provider,))
            connection.execute(
                "DELETE FROM graph_state WHERE key=?", ("external_scan:" + provider,)
            )
            progress = self._state(connection, "progress", {})
            progress["phase"] = "changes_pending"
            self._set(connection, "progress", progress)
            return version

    def observe(self, worker: str, observation: Observation) -> int:
        return self.observe_many(worker, [observation])[0]

    def observe_many(
        self,
        worker: str,
        observations: Iterable[Observation],
        *,
        document_generation: int | None = None,
    ) -> list[int]:
        with self._write(worker) as connection:
            document_delta.require_generation(connection, document_generation)
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
            connection.execute("DELETE FROM graph_excluded_nodes WHERE id=?", (observation.owner,))
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

    @staticmethod
    def _prior_asset(connection, guid: str) -> dict | None:
        row = connection.execute(
            "SELECT a.payload,p.path FROM graph_asset_paths p LEFT JOIN graph_assets a USING(guid) WHERE p.guid=? AND (a.guid IS NOT NULL OR EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id='asset:'||p.guid))",
            (guid,),
        ).fetchone()
        if row is None:
            return None
        if row[0] is not None:
            return json.loads(row[0])
        return {
            "guid": guid,
            "path": row[1],
            "_excluded": True,
            "keys": [
                dict(key)
                for key in connection.execute(
                    "SELECT scheme,value FROM graph_blocked_keys WHERE guid=?", (guid,)
                )
            ],
        }

    def _catalog_changed(self, connection, lookups: Iterable[str], guids: Iterable[str]) -> None:
        connection.execute(
            "UPDATE graph_admission SET ready=0 WHERE id=1 AND EXISTS (SELECT 1 FROM sources WHERE provider LIKE 'runtime:%')"
        )
        self._set(
            connection, "catalog_revision", self._state(connection, "catalog_revision", 0) + 1
        )
        catalog_delta.record(connection, self._state(connection, "catalog_revision"), guids)
        if not connection.execute(
            "SELECT 1 FROM graph_dependencies WHERE dependency GLOB 'catalog:*' LIMIT 1"
        ).fetchone():
            return
        connection.execute("CREATE TEMP TABLE graph_catalog_changes(lookup TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT OR IGNORE INTO graph_catalog_changes VALUES (?)", ((key,) for key in lookups)
        )
        connection.execute(
            "INSERT OR IGNORE INTO graph_catalog_changes VALUES (?)", (CatalogLookups.ANY,)
        )
        if connection.execute(
            "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state<>'deleted' AND owner IN (SELECT d.owner FROM graph_catalog_changes c CROSS JOIN graph_dependencies d ON d.dependency=c.lookup)"
        ).rowcount:
            self._set(connection, "analysis_dirty", True)

    @staticmethod
    def _requeue_native_owners(connection) -> bool:
        # Suppressed edges have no stored owner-to-target mapping. Recollect
        # admitted owners and fence publications from their prior generations.
        changed = connection.execute(
            "UPDATE observations SET generation=generation+1,state='pending',error=NULL "
            "WHERE provider='unity' AND state<>'deleted' "
            "AND owner IN (SELECT 'asset:'||guid FROM graph_assets)"
        ).rowcount
        if changed:
            connection.execute("UPDATE graph_assets SET dirty=1 WHERE dirty=0")
        return bool(changed)

    def record_page(
        self,
        worker: str,
        assets: Iterable[dict],
        progress: dict,
        seen: str,
        *,
        force: bool = True,
        folder_scan: dict | None = None,
    ) -> None:
        with self._write(worker) as connection:
            admitted = self._admission(connection)
            dirty_owners = []
            catalog_changes = []
            catalog_guids = set()
            readmitted = False
            for asset in assets:
                owner = "asset:" + asset["guid"]
                displaced = connection.execute(
                    "SELECT guid FROM graph_assets WHERE path=? AND guid<>? UNION SELECT guid FROM graph_asset_paths WHERE path=? AND guid<>?",
                    (asset["path"], asset["guid"], asset["path"], asset["guid"]),
                ).fetchall()
                if displaced:
                    displaced_owners = ["asset:" + item[0] for item in displaced]
                    self._invalidate(connection, displaced_owners)
                    for displaced_owner in displaced_owners:
                        catalog_guids.add(displaced_owner[6:])
                        catalog_changes.append(
                            Catalog.changed_lookups(
                                self._prior_asset(connection, displaced_owner[6:]), None
                            )
                        )
                        self._delete(connection, displaced_owner)
                    for table in ("graph_assets", "graph_asset_paths", "graph_blocked_keys"):
                        connection.executemany("DELETE FROM " + table + " WHERE guid=?", displaced)
                old = self._prior_asset(connection, asset["guid"])
                connection.execute(
                    "INSERT OR REPLACE INTO graph_asset_paths VALUES (?,?,?)",
                    (asset["guid"], asset["path"], seen),
                )
                if not admitted(asset["path"]):
                    catalog_guids.add(asset["guid"])
                    catalog_changes.append(
                        Catalog.changed_lookups(old, {**asset, "_excluded": True})
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO graph_excluded_nodes VALUES (?,?)",
                        (owner, asset["path"]),
                    )
                    if old is not None and not old.get("_excluded"):
                        self._invalidate(connection, [owner])
                        self._delete(connection, owner)
                        connection.execute(
                            "DELETE FROM graph_assets WHERE guid=?", (asset["guid"],)
                        )
                    connection.execute("DELETE FROM nodes WHERE id=?", (owner,))
                    connection.execute(
                        "DELETE FROM graph_blocked_keys WHERE guid=?", (asset["guid"],)
                    )
                    connection.executemany(
                        "INSERT OR IGNORE INTO graph_blocked_keys VALUES (?,?,?)",
                        (
                            (asset["guid"], key["scheme"], key["value"])
                            for key in asset.get("keys", ())
                        ),
                    )
                    continue
                # A separate delete event may already have retired the old
                # path. The private exclusion survives until this admission.
                if connection.execute(
                    "DELETE FROM graph_excluded_nodes WHERE id=?", (owner,)
                ).rowcount:
                    readmitted = True
                connection.execute("DELETE FROM graph_blocked_keys WHERE guid=?", (asset["guid"],))
                guid, payload = asset["guid"], _json(asset)
                dirty = (
                    force
                    or old is None
                    or old.get("_excluded", False)
                    or old["path"] != asset["path"]
                )
                if old != asset:
                    catalog_guids.add(guid)
                    catalog_changes.append(Catalog.changed_lookups(old, asset))
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
                    dirty_owners.append("asset:" + guid)
                    connection.execute(
                        "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE owner=? AND provider='unity'",
                        ("asset:" + guid,),
                    )
            if readmitted:
                self._requeue_native_owners(connection)
            if dirty_owners:
                self._invalidate(connection, dirty_owners)
            if catalog_changes:
                self._catalog_changed(
                    connection, chain.from_iterable(catalog_changes), iter(catalog_guids)
                )
            if dirty_owners or catalog_changes:
                self.store._bump(connection)
            self._set(connection, "progress", progress)
            if folder_scan is not None:
                self._set(connection, "folder_scan", folder_scan)

    def _delete(self, connection, owner: str) -> None:
        connection.execute("DELETE FROM graph_addressables WHERE owner=?", (owner,))
        if owner.startswith("asset:"):
            connection.execute("DELETE FROM graph_asset_failures WHERE guid=?", (owner[6:],))
            connection.execute("DELETE FROM graph_collected_versions WHERE guid=?", (owner[6:],))
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

    def _remove_assets(self, connection, owners: Callable[[], Iterable[str]]) -> bool:
        """Reiterate bounded identity pages without retaining the full removal set."""
        if next(iter(owners()), None) is None:
            return False
        self._catalog_changed(
            connection,
            (
                lookup
                for owner in owners()
                for lookup in Catalog.changed_lookups(
                    self._prior_asset(connection, owner[6:]), None
                )
            ),
            (owner[6:] for owner in owners()),
        )
        self._invalidate(connection, owners())
        for owner in owners():
            check_graph_work()
            self._delete(connection, owner)
        return True

    def finish_census(self, worker: str, seen: str, progress: dict) -> None:
        with self._write(worker) as connection:
            connection.execute("DELETE FROM graph_state WHERE key='change_page'")

            def owners():
                after = ""
                while True:
                    check_graph_work()
                    page = connection.execute(
                        "SELECT guid FROM graph_assets WHERE seen<>? AND guid>? "
                        "UNION SELECT guid FROM graph_asset_paths WHERE seen<>? AND guid>? "
                        "ORDER BY guid LIMIT 256",
                        (seen, after, seen, after),
                    ).fetchall()
                    if not page:
                        return
                    for row in page:
                        check_graph_work()
                        yield "asset:" + row[0]
                    after = page[-1][0]

            removed = self._remove_assets(connection, owners)
            check_graph_work()
            connection.execute("DELETE FROM graph_assets WHERE seen<>?", (seen,))
            connection.execute("DELETE FROM graph_asset_paths WHERE seen<>?", (seen,))
            connection.execute(
                "DELETE FROM graph_blocked_keys WHERE guid NOT IN (SELECT guid FROM graph_asset_paths)"
            )
            if removed:
                self.store._bump(connection)
            check_graph_work()
            self._set(connection, "progress", progress)

    def _remove_guids(self, connection, rows) -> None:
        if rows:
            self._remove_assets(connection, lambda: ("asset:" + row[0] for row in rows))
            for table in ("graph_assets", "graph_asset_paths", "graph_blocked_keys"):
                connection.executemany("DELETE FROM " + table + " WHERE guid=?", rows)
            self.store._bump(connection)

    def remove_path(self, worker: str, path: str, *, exact: bool = False) -> None:
        with self._write(worker) as connection:
            if exact:
                rows = connection.execute(
                    "SELECT guid FROM graph_asset_paths WHERE path=?", (path,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT guid FROM graph_assets WHERE path=? UNION SELECT guid FROM graph_assets WHERE path>=? AND path<? UNION SELECT guid FROM graph_asset_paths WHERE path=? UNION SELECT guid FROM graph_asset_paths WHERE path>=? AND path<?",
                    (path, path + "/", path + "0", path, path + "/", path + "0"),
                ).fetchall()
            self._remove_guids(connection, rows)

    def begin_folder_scan(self, worker: str, scan: dict, through: int) -> None:
        with self._write(worker) as connection:
            self._set(connection, "folder_scan", scan)
            connection.execute(
                "INSERT OR IGNORE INTO graph_state VALUES ('change_page',?)",
                (_json({"through": through}),),
            )
            if connection.execute(
                "UPDATE graph_admission SET ready=0 WHERE id=1 AND ready=1"
            ).rowcount:
                self.store._bump(connection)

    def remove_missing_guids(self, worker: str, guids: list[str], scan: dict) -> None:
        with self._write(worker) as connection:
            self._remove_guids(connection, [(guid,) for guid in guids])
            self._set(connection, "folder_scan", scan)

    def finish_changes(self, worker: str, progress: dict, page: dict | None) -> None:
        with self._write(worker) as connection:
            self._set(connection, "progress", progress)
            if page:
                self._set(connection, "change_page", page)
            else:
                connection.execute("DELETE FROM graph_state WHERE key='change_page'")

    def descendant_assets(self, path: str, after: tuple[str, str]) -> list[dict]:
        """Page only known descendant identities for targeted folder-move reconciliation."""
        with self.store._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT guid,path FROM graph_asset_paths WHERE (path,guid)>(?,?) AND path<? ORDER BY path,guid LIMIT 256",
                    (max(path + "/", after[0]), after[1], path + "0"),
                )
            ]

    def catalog(self) -> list[dict]:
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM graph_assets ORDER BY guid")
            ]

    def asset_limits(self, worker: str, limits: dict[str, int]) -> None:
        """Retry item-ceiling failures when operator provisioning changes."""
        with self._write(worker) as connection:
            previous = self._state(connection, "asset_limits")
            if previous == limits:
                return
            if previous is not None:
                connection.execute(
                    "UPDATE graph_asset_failures SET retry_at=0 WHERE 'asset:'||guid IN "
                    "(SELECT owner FROM observations WHERE provider='unity' AND "
                    "(error LIKE 'CollectionBudgetExceeded: Reference collection exceeded max_source_bytes=%' "
                    "OR error LIKE 'CollectionBudgetExceeded: Reference collection exceeded max_dependency_items=%'))"
                )
                connection.execute("DELETE FROM graph_state WHERE key='retry_scan'")
            self._set(connection, "asset_limits", limits)

    def queue_due_assets(
        self, worker: str, through: float, *, after: tuple[float, str] | None = None
    ) -> list[tuple[float, str]]:
        """Promote one deadline-ordered retry page under the collector's durable lease."""
        with self._write(worker) as connection:
            where, values = "retry_at<=?", [through]
            if after is not None:
                where += " AND (retry_at,guid)>(?,?)"
                values.extend(after)
            page = [
                tuple(row)
                for row in connection.execute(
                    "SELECT retry_at,guid FROM graph_asset_failures "
                    "INDEXED BY graph_asset_failures_retry WHERE "
                    + where
                    + " ORDER BY retry_at,guid LIMIT 256",
                    values,
                )
            ]
            # Keep failures until successful collection: attempts/backoff and
            # unavailable evidence still belong to the existing retry lifecycle.
            changed = connection.executemany(
                "UPDATE graph_assets SET dirty=1 WHERE guid=? AND dirty<>1",
                [(guid,) for _, guid in page],
            ).rowcount
            if changed:
                connection.execute(
                    "INSERT INTO graph_state VALUES ('summary_generation','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
                )
            self._set(
                connection,
                "retry_scan",
                {"through": through, "after": page[-1]}
                if page
                else {"through": through, "ready": True},
            )
            return page

    def finish_retry_scan(self, worker: str) -> None:
        with self._write(worker) as connection:
            connection.execute("DELETE FROM graph_state WHERE key='retry_scan'")

    def dirty_assets(self, limit: int = 100, *, after: str = "") -> list[dict]:
        """Page the ready queue after this pass's due retries have been promoted."""
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM graph_assets WHERE dirty=1 AND guid>? ORDER BY guid LIMIT ?",
                    (after, limit),
                )
            ]

    def requested_asset(
        self, worker: str, guids: tuple[str, ...], partial_revision: int | None
    ) -> dict | None:
        """Choose a queued GUID only from this worker's admitted, eligible inventory."""
        with self.store._connect() as connection:
            self._require_worker(connection, worker)
            row = connection.execute(
                "SELECT a.payload FROM json_each(?) q CROSS JOIN graph_assets a "
                "ON a.guid=q.value LEFT JOIN graph_asset_failures f USING(guid) "
                "WHERE a.dirty=1 AND (f.guid IS NULL OR f.retry_at<=?) "
                "AND EXISTS (SELECT 1 FROM graph_admission WHERE id=1 AND (ready=1 OR revision=?)) "
                "ORDER BY q.key LIMIT 1",
                (_json(guids), time.time(), partial_revision),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def clean_asset(self, worker: str, guid: str, *, version: dict | None = None) -> None:
        with self._write(worker) as connection:
            if version is not None:
                connection.execute(
                    "INSERT OR REPLACE INTO graph_collected_versions VALUES (?,?)",
                    (guid, _json(version)),
                )
            cleaned = connection.execute(
                "UPDATE graph_assets SET dirty=0 WHERE guid=? AND dirty<>0", (guid,)
            ).rowcount
            recovered = connection.execute(
                "DELETE FROM graph_asset_failures WHERE guid=?", (guid,)
            ).rowcount
            if recovered:
                # Callers created while this input was unavailable have no old
                # dependency edge. Resolve them against the recovered symbols.
                connection.execute(
                    "INSERT OR IGNORE INTO graph_changed_inputs VALUES (?)", ("asset:" + guid,)
                )
                self._set(connection, "analysis_dirty", True)
            # Cleaning changes counts without changing reference evidence or
            # invalidating graph cursors. Other counted mutations bump the graph
            # revision; this independent fence commits/rolls back with cleanup.
            if cleaned or recovered:
                connection.execute(
                    "INSERT INTO graph_state VALUES ('summary_generation','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
                )

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
            return self._blocked_analysis(connection)

    @staticmethod
    def _blocked_analysis(connection) -> set[str]:
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
                "INSERT INTO graph_documents(owner,format,fingerprint,payload,compilation_signature) VALUES (?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET format=excluded.format,fingerprint=excluded.fingerprint,payload=excluded.payload,compilation_signature=excluded.compilation_signature",
                (
                    owner,
                    format_,
                    fingerprint,
                    encoded,
                    payload.get("build", {}).get("binding_revision")
                    if format_ == "csharp"
                    else None,
                ),
            )
            self.store._bump(connection)
            return True

    def document_manifest(self) -> dict[str, dict]:
        with self.store._connect() as connection:
            return {
                row["owner"]: dict(row)
                for row in connection.execute(
                    "SELECT d.owner,d.format,d.fingerprint,o.state FROM graph_documents d LEFT JOIN observations o ON o.owner=d.owner AND o.provider='code' ORDER BY d.owner"
                )
            }

    def documents(self, owners: Iterable[str] | None = None) -> list[dict]:
        with self.store._connect() as connection:
            return [
                {
                    "owner": row[0],
                    "format": row[1],
                    "fingerprint": row[2],
                    "data": json.loads(row[3]),
                    "state": row["state"],
                }
                for row in connection.execute(
                    "SELECT d.*,o.state FROM graph_documents d LEFT JOIN observations o ON o.owner=d.owner AND o.provider='code'"
                    + (
                        " WHERE d.owner IN (SELECT value FROM json_each(?))"
                        if owners is not None
                        else ""
                    )
                    + " ORDER BY d.owner",
                    (_json(list(owners)),) if owners is not None else (),
                )
            ]

    def drop_document(self, worker: str, owner: str) -> None:
        with self._write(worker) as connection:
            previous = connection.execute(
                "SELECT 1 FROM graph_documents WHERE owner=?", (owner,)
            ).fetchone()
            if previous is None:
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
            self.store._bump(connection)

    def changed_inputs(self) -> set[str]:
        with self.store._connect() as connection:
            return {row[0] for row in connection.execute("SELECT owner FROM graph_changed_inputs")}

    @staticmethod
    def _invalidate(connection, owners: Iterable[str]) -> None:
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS graph_changed(id TEXT PRIMARY KEY)")
        connection.execute("DELETE FROM graph_changed")
        connection.executemany(
            "INSERT OR IGNORE INTO graph_changed VALUES (?)", ((owner,) for owner in owners)
        )
        if connection.execute(
            "UPDATE observations SET generation=generation+1,state='pending',error=NULL WHERE provider='code' AND state<>'deleted' AND owner IN (WITH RECURSIVE affected(id) AS (SELECT id FROM graph_changed UNION SELECT d.owner FROM graph_dependencies d JOIN affected a ON d.dependency=a.id) SELECT id FROM affected)"
        ).rowcount:
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

    @classmethod
    def _require_catalog(cls, connection, revision: int | None) -> None:
        if revision is not None and revision != cls._state(connection, "catalog_revision", 0):
            raise RevisionConflict("Catalog changed during analysis")

    def publish_many(
        self,
        worker: str,
        publications: Iterable[tuple],
        *,
        catalog_revision: int | None = None,
        document_generation: int | None = None,
    ) -> list[bool]:
        with self._write(worker) as connection:
            document_delta.require_generation(connection, document_generation)
            self._require_catalog(connection, catalog_revision)
            return [self._publish(connection, *publication) for publication in publications]

    @staticmethod
    def _code_dependencies(
        connection, owner: str, dependencies: set[str], lookups: CatalogLookups | None
    ) -> None:
        connection.execute("DELETE FROM graph_dependencies WHERE owner=?", (owner,))
        catalog = lookups.dependencies if lookups is not None else {CatalogLookups.ANY}
        connection.executemany(
            "INSERT OR IGNORE INTO graph_dependencies VALUES (?,?)",
            ((owner, dependency) for dependency in chain(dependencies, catalog)),
        )

    def publish_events(
        self,
        worker: str,
        owner: str,
        generation: int,
        events: Iterable[Edge | dict],
        dependencies: Callable[[], set[str]] = set,
        *,
        provider: str = "code",
        coverage: str = "partial_static",
        skip_ready: bool = False,
        before_publish: Callable[[], None] = lambda: None,
        catalog_lookups: CatalogLookups | None = None,
        catalog_revision: int | None = None,
        document_generation: int | None = None,
        check: Callable[[], None] = lambda: None,
    ) -> None:
        """Stage bounded batches outside visible evidence, then promote atomically.

        Callers rechecking unchanged inputs may skip an already-ready generation;
        ownership, enabled-source and input fences still apply to that no-op.
        """

        def require_current(connection):
            check()
            document_delta.require_generation(connection, document_generation)
            self._require_catalog(connection, catalog_revision)
            if not self.store._current(connection, owner, provider, generation):
                raise RevisionConflict("Graph inputs changed during streamed analysis")

        def stage(rows):
            with self._write(worker) as connection:
                require_current(connection)
                connection.executemany(
                    "INSERT OR IGNORE INTO graph_staging VALUES (?,?,?,?,?,?,?)", rows
                )

        complete = False
        try:
            with self._write(worker) as connection:
                if (
                    skip_ready
                    and connection.execute(
                        "SELECT 1 FROM observations o JOIN sources s USING(provider) WHERE o.owner=? AND o.provider=? AND o.generation=? AND o.state='ready' AND s.enabled=1",
                        (owner, provider, generation),
                    ).fetchone()
                ):
                    check()
                    document_delta.require_generation(connection, document_generation)
                    self._require_catalog(connection, catalog_revision)
                    complete = True
                    return
                require_current(connection)
                connection.execute(
                    "DELETE FROM graph_staging WHERE worker=? AND owner=?", (worker, owner)
                )
            batch = []
            for ordinal, event in enumerate(events):
                check()
                is_edge = isinstance(event, Edge)
                payload = _json(asdict(event) if is_edge else event)
                if is_edge:
                    identity = _hash((owner, provider, payload))
                else:
                    identity = _hash(payload) if provider == "code" else str(ordinal)
                batch.append(
                    (
                        worker,
                        owner,
                        generation,
                        identity,
                        "edge" if is_edge else "issue",
                        payload,
                        ordinal,
                    )
                )
                if len(batch) == 256:
                    stage(batch)
                    batch.clear()
            if batch:
                stage(batch)
            resolved_dependencies = dependencies()
            before_publish()
            with self._write(worker) as connection:
                require_current(connection)
                connection.execute(
                    "DELETE FROM edges WHERE owner=? AND provider=?", (owner, provider)
                )
                connection.execute(
                    "INSERT INTO edges SELECT identity,owner,?,json_extract(payload,'$.target'),json_extract(payload,'$.kind'),payload FROM graph_staging s WHERE worker=? AND owner=? AND generation=? AND kind='edge' AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=s.owner OR x.id=json_extract(s.payload,'$.target'))",
                    (provider, worker, owner, generation),
                )
                connection.execute(
                    "DELETE FROM graph_issues WHERE owner=? AND provider=?", (owner, provider)
                )
                connection.execute(
                    "INSERT INTO graph_issues SELECT owner,?,ordinal,payload FROM graph_staging WHERE worker=? AND owner=? AND generation=? AND kind='issue'",
                    (provider, worker, owner, generation),
                )
                if provider == "code":
                    self._code_dependencies(
                        connection, owner, resolved_dependencies, catalog_lookups
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO graph_analysis VALUES (?,?,?,?)",
                    (owner, provider, coverage, generation),
                )
                connection.execute(
                    "UPDATE observations SET state='ready',error=NULL WHERE owner=? AND provider=?",
                    (owner, provider),
                )
                self.store._publish_context(connection, owner, provider, generation)
                connection.execute(
                    "DELETE FROM graph_staging WHERE worker=? AND owner=?", (worker, owner)
                )
                self.store._bump(connection)
                check()
            complete = True
        finally:
            # Only this unique worker's private staging is removed. A retired
            # worker cannot touch its successor's work or visible publications.
            if not complete:
                with self.store._connect(write=True) as connection:
                    connection.execute(
                        "DELETE FROM graph_staging WHERE worker=? AND owner=?", (worker, owner)
                    )

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
        self.store._publish_context(connection, owner, provider, generation)
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
            self._code_dependencies(
                connection, owner, analysis.dependencies, analysis.catalog_lookups
            )
        self.store._bump(connection)
        return True

    @classmethod
    def _require_analysis_inputs(
        cls, connection, rules_revision, catalog_revision, document_generation
    ):
        document_delta.require_generation(connection, document_generation)
        cls._require_catalog(connection, catalog_revision)
        if (
            connection.execute("SELECT rules_revision FROM graph_meta").fetchone()[0]
            != rules_revision
        ):
            raise RevisionConflict("Analysis rules changed during reconciliation")

    def queue_analysis(
        self,
        worker,
        owners,
        rules_revision,
        catalog_revision,
        document_generation,
        *,
        all_documents,
    ):
        with self._write(worker) as connection:
            self._require_analysis_inputs(
                connection, rules_revision, catalog_revision, document_generation
            )
            if all_documents:
                document_delta.queue_all(connection)
            else:
                document_delta.queue(connection, owners)

    def pending_analysis(self, after: str, limit: int = 100) -> list[str]:
        with self.store._connect() as connection:
            return document_delta.pending(connection, after, limit)

    def finish_analysis(
        self,
        worker: str,
        rules_revision: int,
        catalog_revision: int | None = None,
        *,
        document_generation: int | None = None,
        blocked: set[str] | None = None,
    ) -> None:
        with self._write(worker) as connection:
            self._require_analysis_inputs(
                connection, rules_revision, catalog_revision, document_generation
            )
            if blocked is not None and (
                self._blocked_analysis(connection) != blocked
                or document_delta.has_eligible_pending(connection, blocked)
            ):
                raise RevisionConflict("Analysis inputs were invalidated during publication")
            self._set(connection, "analysis_dirty", False)
            self._set(connection, "analyzed_rules_revision", rules_revision)
            connection.execute("DELETE FROM graph_changed_inputs")

    def diagnostics(
        self,
        owner: str,
        *,
        offset: int = 0,
        limit: int = 100,
        revision: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        from services.reference_graph.queries import decode_cursor, encode_cursor

        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Use nonnegative offset and page size from 1 to 1000")
        position = None
        if cursor is not None:
            position = decode_cursor(self.store.scope.key, cursor)
            if (
                offset
                or position.get("kind") != "diagnostics"
                or position.get("owner") != _hash(owner)
                or type(position.get("revision")) is not int
                or type(position.get("row")) is not int
                or not 0 < position["row"] < 2**63
                or not isinstance(position.get("anchor"), str)
                or type(position.get("offset")) is not int
                or position["offset"] < 0
            ):
                raise ValueError("Invalid diagnostics cursor; retain its original node")
        with self.store._connect() as connection:
            current = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            if (
                (offset and revision is None)
                or (revision is not None and revision != current)
                or (position is not None and position["revision"] != current)
            ):
                raise RevisionConflict("Diagnostics changed; restart from the first page")
            if connection.execute(
                "SELECT 1 FROM graph_excluded_nodes WHERE id=?", (owner,)
            ).fetchone():
                return {
                    "revision": current,
                    "coverage": [],
                    "issues": [],
                    "next_offset": None,
                    "next_cursor": None,
                    "excluded": True,
                    "reason": "excluded_by_collection_policy",
                }
            statement = "SELECT i.rowid AS cursor_row,i.provider,i.ordinal,i.payload FROM graph_issues i JOIN sources s USING(provider) WHERE i.owner=? AND s.enabled=1"
            parameters = [owner]
            if position is not None:
                anchor_row = connection.execute(
                    "SELECT provider,ordinal FROM graph_issues WHERE rowid=? AND owner=?",
                    (position["row"], owner),
                ).fetchone()
                if anchor_row is None or _hash(list(anchor_row)) != position["anchor"]:
                    raise ValueError(
                        "Invalid diagnostics cursor anchor; restart from the first page"
                    )
                statement += " AND (i.provider,i.ordinal)>(?,?)"
                parameters.extend(anchor_row)
                offset = position["offset"]
            statement += " ORDER BY i.provider,i.ordinal LIMIT ?"
            parameters.append(limit + 1)
            if position is None and offset:
                statement += " OFFSET ?"
                parameters.append(offset)
            rows = bounded_rows(connection, statement, tuple(parameters))
            issues, budget, more, anchor = [], PageBudget(), False, None
            for row in rows:
                if len(issues) == limit or not budget.admit(row):
                    more = True
                    break
                provider, issue = row["provider"], json.loads(row["payload"])
                # Old databases can still contain raw unknown external values.
                # Redact at delivery without discarding resolved graph edges.
                if provider.startswith("external:") and "key" in issue:
                    issue.pop("key")
                    issue["key_redacted"] = True
                issues.append({"provider": provider, **issue})
                # Keep potentially long IDs out of the bounded cursor. Revision
                # and digest fences reject deleted, moved or reused row anchors.
                anchor = {"row": row["cursor_row"], "anchor": _hash([provider, row["ordinal"]])}
            rows.close()
            coverage, coverage_budget = [], PageBudget()
            for row in bounded_rows(
                connection,
                "SELECT a.provider,a.coverage,o.state,o.error FROM graph_analysis a JOIN sources s USING(provider) LEFT JOIN observations o ON o.owner=a.owner AND o.provider=a.provider WHERE a.owner=? AND s.enabled=1 ORDER BY a.provider",
                (owner,),
            ):
                coverage_budget.require(row)
                coverage.append({key: row[key] for key in row.keys() if key != "_response_bytes"})
            for row in bounded_rows(
                connection,
                "SELECT state,version,error FROM graph_addressables WHERE owner=?",
                (owner,),
            ):
                coverage_budget.require(row)
                coverage.append(
                    {
                        "provider": "addressables",
                        "coverage": "catalog_keys",
                        **{key: row[key] for key in row.keys() if key != "_response_bytes"},
                    }
                )
            return {
                "revision": connection.execute("SELECT revision FROM graph_meta").fetchone()[0],
                "coverage": coverage,
                "issues": issues,
                "next_offset": offset + len(issues) if more else None,
                "next_cursor": encode_cursor(
                    self.store.scope.key,
                    {
                        "kind": "diagnostics",
                        "owner": _hash(owner),
                        "revision": current,
                        "offset": offset + len(issues),
                        **anchor,
                    },
                )
                if more
                else None,
            }

    def counts(self) -> dict:
        identity = []
        with self.store._connect(_identity=identity) as connection:
            revision = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            generation = self._state(connection, "summary_generation", 0)
            return self.store._summary(
                "workspace",
                revision,
                identity,
                lambda: {
                    "assets": connection.execute("SELECT count(*) FROM graph_assets").fetchone()[0],
                    "dirty_assets": connection.execute(
                        "SELECT count(*) FROM graph_assets WHERE dirty=1"
                    ).fetchone()[0],
                    "failed_assets": connection.execute(
                        "SELECT count(*) FROM graph_asset_failures"
                    ).fetchone()[0],
                    "unresolved": connection.execute(
                        "SELECT count(*) FROM graph_issues i JOIN sources s USING(provider) WHERE s.enabled=1 AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=i.owner)"
                    ).fetchone()[0],
                },
                generation=generation,
            )

    def external_inputs(
        self,
        provider: str,
        *,
        admission: bool = False,
        paths: tuple[str, ...] | None = None,
        _after: str | None = None,
        _unseen: str | None = None,
    ) -> dict[str, dict]:
        if paths == ():
            return {}
        params = (provider,)
        selection = "graph_external_paths p"
        if paths is not None:
            # The provider/owner primary key admits only the captured event identities.
            selection = "json_each(?) j CROSS JOIN graph_external_paths p ON p.owner=j.value"
            params = (_json([provider + ":" + _hash(path) for path in paths]), provider)
        paging = ""
        if _after is not None:
            paging = " AND p.owner>? AND coalesce(e.seen,'')<>? ORDER BY p.owner LIMIT 256"
            params = (*params, _after, _unseen)
        with self.store._connect() as connection:
            return {
                row["path"]: dict(row)
                for row in connection.execute(
                    "SELECT p.provider,p.owner,p.path,e.fingerprint,e.stamp,e.analysis_key,e.seen,"
                    "o.state,c.published AS provenance,v.generation AS checkpoint "
                    f"FROM {selection} LEFT JOIN graph_external e USING(owner,provider) "
                    "LEFT JOIN observations o USING(owner,provider) "
                    "LEFT JOIN evidence_context c USING(owner,provider) "
                    "LEFT JOIN graph_external_checkpoints v ON v.owner=p.owner AND v.provider=p.provider "
                    "AND v.generation=o.generation AND o.state='pending' WHERE p.provider=?"
                    + paging
                    if admission
                    else "SELECT e.*,o.state,c.published AS provenance FROM graph_external e LEFT JOIN observations o USING(owner,provider) LEFT JOIN evidence_context c USING(owner,provider) WHERE e.provider=?",
                    params,
                )
            }

    def external_input_page(
        self, provider: str, *, after: str = "", unseen: str
    ) -> dict[str, dict]:
        """Bound prior-path reconciliation independently of the source population."""
        return self.external_inputs(provider, admission=True, _after=after, _unseen=unseen)

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

    def external_validation(self, pairs: Iterable[tuple[str, str]], *, admission: bool = False):
        """One request-local metadata snapshot, never a cached permission grant."""
        with self.store._connect() as connection:
            budget, metadata = PageBudget(), {}
            for row in bounded_rows(
                connection,
                "SELECT e.*,s.enabled,s.version,s.config,c.published AS provenance FROM json_each(?) r JOIN graph_external e ON e.provider=json_extract(r.value,'$[0]') AND e.owner=json_extract(r.value,'$[1]') JOIN sources s USING(provider) LEFT JOIN evidence_context c ON c.provider=e.provider AND c.owner=e.owner",
                (_json(list(pairs)),),
            ):
                # Authorization is complete or fails, including repeated source configs.
                budget.require(row)
                metadata[row["provider"], row["owner"]] = {
                    key: row[key] for key in row.keys() if key != "_response_bytes"
                }
            if admission:
                return (
                    metadata,
                    {
                        "respect_gitignore": True,
                        **self._state(connection, "policy", {}, bounded=True),
                    },
                    self._admission(connection, bounded=True),
                )
            return metadata

    def external_nodes(self, owners: Iterable[str]) -> list[dict]:
        """Resolve manual endpoint identities to their exact granted source roots in one read."""
        with self.store._connect() as connection:
            budget, nodes = PageBudget(), []
            for row in bounded_rows(
                connection,
                "SELECT e.owner,e.path,s.provider,s.enabled,s.config FROM json_each(?) r JOIN graph_external_paths e ON e.owner=r.value JOIN sources s USING(provider)",
                (_json(list(owners)),),
            ):
                budget.require(row)
                nodes.append({key: row[key] for key in row.keys() if key != "_response_bytes"})
            return nodes

    def hide_external(self, worker: str, paths: Iterable[tuple[str, str]]) -> None:
        with self._write(worker) as connection:
            changed = False
            for owner, path in paths:
                if (
                    connection.execute(
                        "SELECT 1 FROM graph_excluded_nodes WHERE id=? AND path=?", (owner, path)
                    ).fetchone()
                    and not connection.execute(
                        "SELECT 1 FROM nodes WHERE id=?", (owner,)
                    ).fetchone()
                ):
                    continue
                self._delete(connection, owner)
                connection.execute(
                    "INSERT OR REPLACE INTO graph_excluded_nodes VALUES (?,?)", (owner, path)
                )
                connection.execute("DELETE FROM nodes WHERE id=?", (owner,))
                changed = True
            if changed:
                self.store._bump(connection)
                connection.execute(
                    "UPDATE graph_admission SET revision=(SELECT revision FROM graph_meta) WHERE id=1"
                )

    def catalog_snapshot(self, known_revision: int | None = None) -> tuple[int, list[dict] | None]:
        with self.store._connect() as connection:
            revision = self._state(connection, "catalog_revision", 0)
            rows = None if revision == known_revision else list(self._catalog_rows(connection))
            return revision, rows

    @staticmethod
    def _catalog_rows(connection) -> Iterable[dict]:
        # Consume inside the caller's read snapshot; no decoded catalog backlog.
        for row in connection.execute("SELECT payload FROM graph_assets ORDER BY guid"):
            check_graph_work()
            yield json.loads(row[0])
        blocked = {}
        for guid, path, scheme, value in connection.execute(
            "SELECT p.guid,p.path,k.scheme,k.value FROM graph_asset_paths p "
            "JOIN graph_excluded_nodes n ON n.id='asset:'||p.guid "
            "LEFT JOIN graph_blocked_keys k ON k.guid=p.guid ORDER BY p.guid,k.scheme,k.value"
        ):
            check_graph_work()
            if blocked.get("guid") != guid:
                if blocked:
                    yield blocked
                blocked = {"guid": guid, "path": path, "_excluded": True, "keys": []}
            if scheme is not None:
                blocked["keys"].append({"scheme": scheme, "value": value})
        check_graph_work()
        if blocked:
            yield blocked

    def key_admission(self):
        """Reuse a minimal key admission catalog across runtime pages."""
        with self._key_cache_lock:
            self._key_cache = self.refresh_catalog(self._key_cache)
            return self._key_cache[1]

    def clear_caches(self) -> None:
        """Release derived admission data without changing durable evidence."""
        with self._key_cache_lock:
            self._key_cache = None
            self._admission_cache = None

    def refresh_catalog(self, cache: tuple[int, Catalog] | None) -> tuple[int, Catalog]:
        """Update only journaled GUIDs; a gap, reset or cold open rebuilds safely."""
        with self.store._connect() as connection:
            revision = self._state(connection, "catalog_revision", 0)
            if cache is not None and revision == cache[0]:
                return cache
            guids = catalog_delta.changed_guids(connection, cache[0], revision) if cache else None
            if guids is None:
                return revision, Catalog(self._catalog_rows(connection))
            assets, removed = [], []
            for guid in guids:
                asset = self._prior_asset(connection, guid)
                if asset is None:
                    removed.append(guid)
                else:
                    assets.append(asset)
        cache[1].update(assets, removed=removed)
        return revision, cache[1]

    def provider_status(self, *, bounded: bool = False) -> dict:
        identity = []
        with self.store._connect(_identity=identity) as connection:
            revision = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]

            def aggregate():
                states, versions = {}, set()
                for state, version, count in connection.execute(
                    "SELECT state,version,count(*) FROM graph_addressables GROUP BY state,version"
                ):
                    states[state] = states.get(state, 0) + count
                    if version is not None:
                        versions.add(version)
                return {
                    "states": states,
                    "versions": sorted(versions),
                    "version_kind": "assembly",
                    "errors": states.get("failed", 0) + states.get("unsupported", 0),
                }

            result = {
                "addressables": self.store._summary("addressables", revision, identity, aggregate)
            }
            statement = "SELECT s.provider,s.version,g.value AS payload FROM sources s LEFT JOIN graph_state g ON g.key='external_scan:'||s.provider WHERE s.enabled=1 AND s.provider LIKE 'external:%'"
            rows = bounded_rows(connection, statement) if bounded else connection.execute(statement)
            budget = PageBudget()
            for row in rows:
                if bounded:
                    budget.require(row)
                state = json.loads(row["payload"]) if row["payload"] else {}
                if state.get("source_version") != row["version"]:
                    state = {}
                result[row["provider"]] = {
                    "state": "not_verified",
                    "last_verified_at": None,
                    **state,
                    "current": False,
                }
            return result

    def external_scan(
        self, worker: str, source: dict, values: dict, *, incomplete: str | None = None
    ) -> None:
        with self._write(worker) as connection:
            self._set(
                connection,
                "external_scan:" + source["provider"],
                {**values, "source_version": source["version"]},
            )
            if incomplete is not None:
                # Preserve only complete publications, or a checkpoint which still
                # owns this pending generation. An admitted-only or failed file
                # must go through analysis again even if its stamp is unchanged.
                connection.execute(
                    "INSERT OR REPLACE INTO graph_external_checkpoints "
                    "SELECT o.provider,o.owner,o.generation+1 FROM observations o "
                    "LEFT JOIN graph_external_checkpoints v USING(provider,owner) "
                    "WHERE o.provider=? AND (o.state='ready' OR (o.state='pending' AND o.generation=v.generation))",
                    (source["provider"],),
                )
                connection.execute(
                    "UPDATE observations SET generation=generation+1,state='pending',error=? WHERE provider=? AND state<>'deleted'",
                    (incomplete, source["provider"]),
                )
                self.store._bump(connection)

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

    def record_external_many(
        self, worker: str, rows: Iterable[tuple], *, resume: list[tuple] | None = None
    ) -> None:
        with self._write(worker) as connection:
            if resume:
                updated = connection.executemany(
                    "UPDATE observations SET state='ready',error=NULL WHERE provider=? AND owner=? AND generation=? AND state='pending'",
                    resume,
                ).rowcount
                if updated != len(resume):
                    raise RevisionConflict("External checkpoint changed before verification")
                self.store._bump(connection)
            self._record_external(connection, rows)

    def refresh_external_provenance(self, worker: str, source: dict, rows: Iterable[tuple]) -> None:
        with self._write(worker) as connection:
            current = connection.execute(
                "SELECT version,enabled FROM sources WHERE provider=?", (source["provider"],)
            ).fetchone()
            if current is None or current[0] != source["version"] or not current[1]:
                raise RevisionConflict("External source grant changed")
            changed = False
            for owner, fingerprint, provenance in rows:
                observation = connection.execute(
                    "SELECT generation FROM observations WHERE owner=? AND provider=? AND fingerprint=? AND state='ready'",
                    (owner, source["provider"], fingerprint),
                ).fetchone()
                if observation is None:
                    raise RevisionConflict("External evidence changed before provenance refresh")
                self.store._context(
                    connection, owner, source["provider"], observation[0], provenance
                )
                self.store._publish_context(connection, owner, source["provider"], observation[0])
                changed = True
            if changed:
                self.store._bump(connection)

    @staticmethod
    def _record_external(connection, rows: Iterable[tuple]) -> None:
        rows = list(rows)
        connection.executemany(
            "INSERT INTO graph_external_paths VALUES (?,?,?) ON CONFLICT(provider,owner) DO UPDATE SET path=excluded.path",
            ((row[0], row[1], row[2]) for row in rows),
        )
        connection.executemany(
            "INSERT INTO graph_external VALUES (?,?,?,?,?,?,?) ON CONFLICT(provider,owner) DO UPDATE SET path=excluded.path,fingerprint=excluded.fingerprint,stamp=excluded.stamp,analysis_key=excluded.analysis_key,seen=excluded.seen",
            rows,
        )

    def finish_external(
        self, worker: str, provider: str, seen: str, *, check: Callable[[], None] = lambda: None
    ) -> None:
        with self._write(worker) as connection:
            connection.execute(
                "DELETE FROM graph_external_checkpoints WHERE provider=?", (provider,)
            )
            after, changed = "", False
            while True:
                check()
                removed = connection.execute(
                    "SELECT owner FROM graph_external WHERE provider=? AND owner>? AND seen<>? ORDER BY owner LIMIT 256",
                    (provider, after, seen),
                ).fetchall()
                if not removed:
                    break
                for row in removed:
                    self._delete(connection, row[0])
                changed, after = True, removed[-1][0]
            check()
            connection.execute(
                "DELETE FROM graph_external WHERE provider=? AND seen<>?", (provider, seen)
            )
            if changed:
                self.store._bump(connection)

    def remove_external(self, worker: str, provider: str, owners: list[str]) -> None:
        with self._write(worker) as connection:
            for owner in owners:
                self._delete(connection, owner)
                for table in ("graph_external", "graph_external_checkpoints"):
                    connection.execute(
                        f"DELETE FROM {table} WHERE provider=? AND owner=?", (provider, owner)
                    )
            self.store._bump(connection)
