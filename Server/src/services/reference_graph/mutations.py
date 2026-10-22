"""Durable mutation receipts, independent of request and worker lifetimes.

A receipt observes imported evidence; it never turns an uncertain write into a
confirmed write. Large batches append target pages and retain their state on disk.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from services.reference_graph.queries import GraphQueries, _SELECT
from services.reference_graph.store import RevisionConflict, _json


class MutationReceipts:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = workspace.store
        self.queries = workspace.queries()
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_mutations_schema(version INTEGER NOT NULL)"
            )
            row = connection.execute("SELECT version FROM graph_mutations_schema").fetchone()
            if row and row[0] != 1:
                raise ValueError("Unsupported mutation receipt schema")
            if row is None:
                for statement in (
                    "CREATE TABLE graph_mutations(id TEXT PRIMARY KEY,state TEXT NOT NULL,outcome TEXT NOT NULL,outcome_source TEXT NOT NULL,baseline TEXT NOT NULL,checkpoint TEXT NOT NULL,before_snapshot TEXT NOT NULL,after_snapshot TEXT,checked TEXT,created REAL NOT NULL)",
                    "CREATE TABLE graph_mutation_targets(receipt TEXT NOT NULL,path TEXT NOT NULL,probe TEXT NOT NULL,PRIMARY KEY(receipt,path))",
                    "CREATE TABLE graph_mutation_owners(receipt TEXT NOT NULL,owner TEXT NOT NULL,PRIMARY KEY(receipt,owner))",
                    "CREATE TABLE graph_mutation_events(receipt TEXT NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(receipt,path))",
                    "CREATE TABLE graph_mutation_validation(receipt TEXT PRIMARY KEY,checkpoint TEXT NOT NULL,after_path TEXT NOT NULL)",
                ):
                    connection.execute(statement)
                connection.execute("INSERT INTO graph_mutations_schema VALUES (1)")

    @staticmethod
    def paths(paths):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 1000:
            raise ValueError("Supply 1-1000 paths per page; append more pages before dispatch")
        for path in paths:
            if (
                not isinstance(path, str)
                or not path.startswith(("Assets/", "Packages/"))
                or "\\" in path
                or any(not part or part.startswith(".") for part in path.split("/"))
            ):
                raise ValueError("Mutation targets require admitted, relative asset paths")
        if len(set(paths)) != len(paths):
            raise ValueError("A target page cannot contain duplicate paths")

    @staticmethod
    def _row(connection, identity):
        row = connection.execute("SELECT * FROM graph_mutations WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("Unknown or released mutation receipt in this scope")
        return row

    def resolve_guids(self, guids):
        if (
            not isinstance(guids, list)
            or not 1 <= len(guids) <= 1000
            or any(
                not isinstance(guid, str) or re.fullmatch(r"[a-f0-9]{32}", guid) is None
                for guid in guids
            )
            or len(set(guids)) != len(guids)
        ):
            raise ValueError("Supply 1-1000 distinct lowercase GUIDs per target page")
        with self.store._connect() as connection:
            paths = []
            for guid in guids:
                row = connection.execute(
                    "SELECT path FROM graph_assets WHERE guid=?", (guid,)
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "GUID is not in this graph's current inventory; synchronize it or supply a path"
                    )
                paths.append(row[0])
            return paths

    def _owners(self, connection, identity, probes):
        for probe in probes:
            path = probe["path"]
            connection.execute(
                "INSERT OR IGNORE INTO graph_mutation_owners SELECT ?, 'asset:'||guid FROM graph_assets WHERE path=? OR (path>=? AND path<?)",
                (identity, path, path + "/", path + "0"),
            )
            if probe.get("guid"):
                connection.execute(
                    "INSERT OR IGNORE INTO graph_mutation_owners VALUES (?,?)",
                    (identity, "asset:" + probe["guid"]),
                )

    def _capture(self, connection, identity, snapshot=None):
        snapshot = snapshot or uuid.uuid4().hex
        revision = GraphQueries._revision(connection)
        sources = _json(GraphQueries._sources(connection))
        connection.execute(
            "INSERT OR IGNORE INTO graph_snapshots VALUES (?,?,?,'ready',0,?,?)",
            (snapshot, "Mutation incident evidence", revision, sources, time.time()),
        )
        rows = connection.execute(
            _SELECT
            + "(e.owner IN (SELECT owner FROM graph_mutation_owners WHERE receipt=?) OR e.target IN (SELECT owner FROM graph_mutation_owners WHERE receipt=?)) ORDER BY e.id",
            (identity, identity),
        )
        for row in rows:
            connection.execute(
                "INSERT OR IGNORE INTO graph_snapshot_edges VALUES (?,?,?)",
                (snapshot, row["id"], _json(GraphQueries._evidence(row))),
            )
        connection.execute(
            "UPDATE graph_snapshots SET captured=(SELECT count(*) FROM graph_snapshot_edges WHERE snapshot=?) WHERE id=?",
            (snapshot, snapshot),
        )
        return snapshot

    def begin(self, probes: list[dict], baseline: dict) -> dict:
        self.paths([probe["path"] for probe in probes])
        identity = uuid.uuid4().hex
        checkpoint = {"epoch": baseline["epoch"], "after": baseline["revision"]}
        with self.store._connect(write=True) as connection:
            self._owners(connection, identity, probes)
            snapshot = self._capture(connection, identity)
            connection.execute(
                "INSERT INTO graph_mutations VALUES (?,'open','unknown','not_reported',?,?,?,NULL,NULL,?)",
                (identity, _json(baseline), _json(checkpoint), snapshot, time.time()),
            )
            self._add(connection, identity, probes)
            connection.execute(
                "UPDATE graph_snapshots SET revision=? WHERE id=?",
                (GraphQueries._revision(connection), snapshot),
            )
        return self.read(identity)

    def _add(self, connection, identity, probes):
        connection.executemany(
            "INSERT OR IGNORE INTO graph_mutation_targets VALUES (?,?,?)",
            ((identity, probe["path"], _json(probe)) for probe in probes),
        )
        # Upgrade only the requested legacy owners; do not re-read a whole large
        # project merely because receipt support became available.
        changed = connection.execute(
            "UPDATE graph_assets SET dirty=1 WHERE dirty=0 AND 'asset:'||guid IN (SELECT owner FROM graph_mutation_owners WHERE receipt=?) AND guid NOT IN (SELECT guid FROM graph_collected_versions)",
            (identity,),
        ).rowcount
        if changed:
            self.store._bump(connection)

    def add(self, identity, probes, baseline):
        self.paths([probe["path"] for probe in probes])
        with self.store._connect(write=True) as connection:
            row = self._row(connection, identity)
            if row["state"] != "open" or json.loads(row["baseline"]) != baseline:
                raise RevisionConflict(
                    "Target pages must precede mutation and use the original native checkpoint"
                )
            if (
                GraphQueries._revision(connection)
                != connection.execute(
                    "SELECT revision FROM graph_snapshots WHERE id=?", (row["before_snapshot"],)
                ).fetchone()[0]
            ):
                raise RevisionConflict("Graph changed while preparing targets; start a new receipt")
            self._owners(connection, identity, probes)
            self._capture(connection, identity, row["before_snapshot"])
            self._add(connection, identity, probes)
            connection.execute(
                "UPDATE graph_snapshots SET revision=? WHERE id=?",
                (GraphQueries._revision(connection), row["before_snapshot"]),
            )
        return self.read(identity)

    def read(self, identity):
        with self.store._connect() as connection:
            row = self._row(connection, identity)
            return {
                "id": row["id"],
                "state": row["state"],
                "outcome": row["outcome"],
                "outcome_source": row["outcome_source"],
                "mutation_confirmed": row["outcome"] == "accepted"
                and row["outcome_source"] == "tool_response",
                "baseline": json.loads(row["baseline"]),
                "checkpoint": json.loads(row["checkpoint"]),
                "checked": json.loads(row["checked"]) if row["checked"] else None,
                "targets": connection.execute(
                    "SELECT count(*) FROM graph_mutation_targets WHERE receipt=?", (identity,)
                ).fetchone()[0],
                "coverage": "direct_incident_edges; imported_state; not_exclusive_causation",
            }

    def finish(self, identity, outcome: str, source: str = "caller_reported"):
        if outcome not in {"accepted", "failed", "unknown"} or source not in {
            "caller_reported",
            "tool_response",
        }:
            raise ValueError("Outcome must be accepted, failed or unknown")
        with self.store._connect(write=True) as connection:
            row = self._row(connection, identity)
            if row["state"] != "open":
                if (row["outcome"], row["outcome_source"]) != (outcome, source):
                    raise RevisionConflict("Mutation outcome was already recorded")
            else:
                connection.execute(
                    "UPDATE graph_mutations SET state='pending',outcome=?,outcome_source=? WHERE id=?",
                    (outcome, source, identity),
                )
        return self.read(identity)

    def targets(self, identity, after="", limit=128):
        GraphQueries._page_size(limit)
        with self.store._connect() as connection:
            self._row(connection, identity)
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT probe FROM graph_mutation_targets WHERE receipt=? AND path>? ORDER BY path LIMIT ?",
                    (identity, after, limit),
                )
            ]

    def events(self, identity, changes, epoch, after):
        with self.store._connect(write=True) as connection:
            self._row(connection, identity)
            connection.executemany(
                "INSERT OR REPLACE INTO graph_mutation_events VALUES (?,?,?)",
                (
                    (identity, row["path"], _json(row))
                    for row in changes
                    if isinstance(row.get("path"), str)
                ),
            )
            connection.execute(
                "UPDATE graph_mutations SET checkpoint=? WHERE id=?",
                (_json({"epoch": epoch, "after": after}), identity),
            )

    def check_targets(self, identity, probes, *, replacement_epoch: bool):
        with self.store._connect() as connection:
            self._row(connection, identity)
            for probe in probes:
                path = probe["path"]
                row = connection.execute(
                    "SELECT probe FROM graph_mutation_targets WHERE receipt=? AND path=?",
                    (identity, path),
                ).fetchone()
                if row is None:
                    raise RevisionConflict("Native probe did not match registered targets")
                before = json.loads(row[0])
                changed = any(
                    before.get(key) != probe.get(key)
                    for key in ("stamp", "present", "guid", "folder")
                )
                imported = (
                    replacement_epoch
                    or connection.execute(
                        "SELECT 1 FROM graph_mutation_events WHERE receipt=? AND (path=? OR (path>=? AND path<?)) LIMIT 1",
                        (identity, path, path + "/", path + "0"),
                    ).fetchone()
                )
                if (changed or probe.get("folder")) and not imported:
                    return "awaiting_import"
                if (
                    changed
                    and probe["present"]
                    and not probe.get("folder")
                    and not replacement_epoch
                ):
                    event = connection.execute(
                        "SELECT payload FROM graph_mutation_events WHERE receipt=? AND path=?",
                        (identity, path),
                    ).fetchone()
                    if event is None or json.loads(event[0]).get("file_stamp") != probe.get(
                        "stamp"
                    ):
                        return "awaiting_import"
                if not probe["present"]:
                    if connection.execute(
                        "SELECT 1 FROM graph_assets WHERE path=? OR (path>=? AND path<?) LIMIT 1",
                        (path, path + "/", path + "0"),
                    ).fetchone():
                        return "awaiting_collection"
                elif not probe.get("folder"):
                    current = connection.execute(
                        "SELECT a.dirty,a.path,v.payload FROM graph_assets a LEFT JOIN graph_collected_versions v USING(guid) WHERE a.guid=?",
                        (probe["guid"],),
                    ).fetchone()
                    if (
                        current is None
                        or current["dirty"]
                        or current["path"] != path
                        or current["payload"] is None
                    ):
                        return "awaiting_collection"
                    version = json.loads(current["payload"])
                    if any(version.get(key) != probe.get(key) for key in ("stamp", "dependency")):
                        return "awaiting_collection"
        return None

    def request_collection(self, identity, probes):
        """Repair only mismatching imported targets, including legacy stamps."""
        with self.store._connect(write=True) as connection:
            self._row(connection, identity)
            changed = False
            for probe in probes:
                if probe.get("present") and not probe.get("folder"):
                    version = connection.execute(
                        "SELECT payload FROM graph_collected_versions WHERE guid=?",
                        (probe["guid"],),
                    ).fetchone()
                    if version is not None:
                        collected = json.loads(version[0])
                        if all(
                            collected.get(key) == probe.get(key) for key in ("stamp", "dependency")
                        ):
                            continue
                    changed |= (
                        connection.execute(
                            "UPDATE graph_assets SET dirty=1 WHERE guid=? AND dirty=0",
                            (probe["guid"],),
                        ).rowcount
                        > 0
                    )
            if changed:
                self.store._bump(connection)
        return changed

    def validation(self, identity, native, revision, after=None):
        key = _json({**native, "graph_revision": revision})
        with self.store._connect(write=True) as connection:
            self._row(connection, identity)
            row = connection.execute(
                "SELECT checkpoint,after_path FROM graph_mutation_validation WHERE receipt=?",
                (identity,),
            ).fetchone()
            if row is None or row["checkpoint"] != key:
                connection.execute(
                    "INSERT OR REPLACE INTO graph_mutation_validation VALUES (?,?,'')",
                    (identity, key),
                )
                position = ""
            else:
                position = row["after_path"]
            if after is not None:
                if GraphQueries._revision(connection) != revision:
                    raise RevisionConflict("Graph changed during mutation target verification")
                connection.execute(
                    "UPDATE graph_mutation_validation SET after_path=? WHERE receipt=?",
                    (after, identity),
                )
                position = after
            return position

    def complete(self, identity, native, revision):
        with self.store._connect(write=True) as connection:
            row = self._row(connection, identity)
            if row["state"] == "synchronized":
                return
            if row["state"] != "pending" or GraphQueries._revision(connection) != revision:
                raise RevisionConflict("Graph changed before receipt publication")
            # Include newly imported GUIDs and descendants without materializing
            # the target or incident-edge set in Python.
            for target in connection.execute(
                "SELECT path FROM graph_mutation_targets WHERE receipt=?", (identity,)
            ):
                self._owners(connection, identity, [{"path": target[0]}])
            snapshot = self._capture(connection, identity)
            checked = {
                "revision": revision,
                "epoch": native["epoch"],
                "native_revision": native["revision"],
                "at": time.time(),
            }
            connection.execute(
                "UPDATE graph_mutations SET state='synchronized',after_snapshot=?,checked=? WHERE id=?",
                (snapshot, _json(checked), identity),
            )
            connection.execute("DELETE FROM graph_mutation_events WHERE receipt=?", (identity,))
            connection.execute("DELETE FROM graph_mutation_validation WHERE receipt=?", (identity,))

    def diff(self, identity, *, limit=100, cursor=None):
        with self.store._connect() as connection:
            row = self._row(connection, identity)
            if row["state"] != "synchronized":
                raise RevisionConflict(
                    "Wait for this receipt to synchronize before comparing evidence"
                )
            before, after = row["before_snapshot"], row["after_snapshot"]
        return self.queries.diff(before, after, limit=limit, cursor=cursor)

    def release(self, identity):
        with self.store._connect(write=True) as connection:
            row = self._row(connection, identity)
            snapshots = [row["before_snapshot"], row["after_snapshot"]]
            for table in (
                "graph_mutation_targets",
                "graph_mutation_owners",
                "graph_mutation_events",
                "graph_mutation_validation",
            ):
                connection.execute(f"DELETE FROM {table} WHERE receipt=?", (identity,))
            connection.execute("DELETE FROM graph_mutations WHERE id=?", (identity,))
            for snapshot in snapshots:
                if snapshot:
                    connection.execute(
                        "DELETE FROM graph_snapshot_edges WHERE snapshot=?", (snapshot,)
                    )
                    connection.execute("DELETE FROM graph_snapshots WHERE id=?", (snapshot,))
        return True
