"""Scoped, durable runtime observations. No event-count or session-age limit."""

from __future__ import annotations

import json
import re
import secrets
import time
import uuid

from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, Observation, RevisionConflict, _json


class RuntimeObservations:
    def __init__(self, workspace):
        self.workspace, self.store = workspace, workspace.store
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_runtime_schema(version INTEGER NOT NULL)"
            )
            row = connection.execute("SELECT version FROM graph_runtime_schema").fetchone()
            if row is not None and row[0] != 1:
                raise ValueError("Unsupported runtime observation schema")
            if row is None:
                for sql in (
                    "CREATE TABLE graph_runtime_sessions(id TEXT PRIMARY KEY,token TEXT NOT NULL,state TEXT NOT NULL,offset INTEGER NOT NULL,metadata TEXT NOT NULL,created REAL NOT NULL,accepted INTEGER NOT NULL DEFAULT 0,sequence INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE graph_runtime_events(session TEXT NOT NULL,sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session,sequence))",
                    "CREATE TABLE graph_runtime_handles(session TEXT NOT NULL,handle TEXT NOT NULL,acquired INTEGER NOT NULL,released INTEGER NOT NULL,unmatched INTEGER NOT NULL,owner_path TEXT NOT NULL,target_path TEXT NOT NULL,PRIMARY KEY(session,handle))",
                ):
                    connection.execute(sql)
                connection.execute("INSERT INTO graph_runtime_schema VALUES (1)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_runtime_blocked_handles(session TEXT NOT NULL,handle TEXT NOT NULL,PRIMARY KEY(session,handle))"
            )

    @staticmethod
    def _session(connection, identity):
        row = connection.execute(
            "SELECT * FROM graph_runtime_sessions WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown runtime capture in this scope")
        return row

    def begin(self):
        identity = uuid.uuid4().hex
        with self.store._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO graph_runtime_sessions(id,token,state,offset,metadata,created) VALUES (?,?,'starting',0,'{}',?)",
                (identity, secrets.token_hex(32), time.time()),
            )
            connection.execute("INSERT INTO sources VALUES (?,1,1,'{}')", ("runtime:" + identity,))
        return self.status(identity)

    def native(self, identity):
        with self.store._connect() as connection:
            row = self._session(connection, identity)
            return {"token": row["token"], "offset": row["offset"]}

    def sessions(self, *, after="", limit=100):
        GraphQueries._page_size(limit)
        if not isinstance(after, str):
            raise ValueError("Capture cursor must be a string")
        with self.store._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,state,created FROM graph_runtime_sessions WHERE id>? ORDER BY id LIMIT ?",
                    (after, limit + 1),
                )
            ]
        return {
            "captures": rows[:limit],
            "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    def status(self, identity):
        with self.store._connect() as connection:
            row = self._session(connection, identity)
            count = row["accepted"]
            return {
                "id": identity,
                "state": row["state"],
                "events": count,
                "offset": row["offset"],
                "metadata": json.loads(row["metadata"]),
                "coverage": "explicit_instrumentation; historical_runtime_observations; not_unused_or_leak_proof",
            }

    def admitted(self, row, admission=None, catalog=None):
        admission = admission or self.workspace.admission()
        for key in ("owner_path", "target_path"):
            path = row.get(key, "")
            if path and (
                not isinstance(path, str)
                or not path.startswith(("Assets/", "Packages/"))
                or "\\" in path
                or any(not part or part.startswith(".") for part in path.split("/"))
                or not admission(path)
            ):
                return False
        if (
            row.get("key")
            and catalog is not None
            and catalog.resolve_detail(row.get("scheme", ""), row["key"])[1] == "excluded"
        ):
            return False
        return True

    def ingest(self, identity, after, page):
        events = page.get("events")
        if (
            not isinstance(events, list)
            or len(events) > 1000
            or type(page.get("next_offset")) is not int
            or page["next_offset"] < after
        ):
            raise ValueError("Invalid native runtime page")
        metadata = {
            key: page[key]
            for key in ("build", "runtime_session", "playing", "error", "count")
            if key in page
        }
        with self.store._connect(write=True) as connection:
            session = self._session(connection, identity)
            previous = json.loads(session["metadata"])
            if previous and any(
                previous.get(key) != metadata.get(key)
                for key in ("build", "runtime_session", "playing")
            ):
                raise RevisionConflict("Runtime capture identity changed; start a separate capture")
            if session["offset"] != after:
                if session["offset"] == page["next_offset"]:
                    return self.status(identity)
                raise RevisionConflict("Runtime ingestion cursor changed")
            provider = "runtime:" + identity
            accepted, sequence = session["accepted"], session["sequence"]
            admission = self.workspace._admission(connection)
            catalog = self.workspace.key_admission()
            for row in events:
                if (
                    not isinstance(row, dict)
                    or type(row.get("sequence")) is not int
                    or row["sequence"] != sequence + 1
                    or row.get("kind") not in {"load", "acquire", "release"}
                ):
                    raise ValueError("Invalid runtime observation")
                sequence = row["sequence"]
                if not all(
                    isinstance(row.get(key, ""), str)
                    for key in (
                        "scheme",
                        "key",
                        "handle",
                        "owner_path",
                        "target_path",
                        "target_guid",
                    )
                ):
                    raise ValueError("Runtime observation fields must be strings")
                handle = row.get("handle", "")
                if (
                    row["kind"] == "release"
                    and handle
                    and connection.execute(
                        "SELECT 1 FROM graph_runtime_blocked_handles WHERE session=? AND handle=?",
                        (identity, handle),
                    ).fetchone()
                ):
                    continue
                if not self.admitted(row, admission, catalog):
                    if handle:
                        connection.execute(
                            "INSERT OR IGNORE INTO graph_runtime_blocked_handles VALUES (?,?)",
                            (identity, handle),
                        )
                    continue
                if row["kind"] == "acquire" and handle:
                    connection.execute(
                        "DELETE FROM graph_runtime_blocked_handles WHERE session=? AND handle=?",
                        (identity, handle),
                    )
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO graph_runtime_events VALUES (?,?,?)",
                    (identity, row["sequence"], _json(row)),
                ).rowcount
                if not inserted:
                    continue
                accepted += 1
                self._handle(connection, identity, row)
                owner = provider + ":" + str(row["sequence"])
                context = {
                    "capture": identity,
                    "runtime_session": metadata.get("runtime_session"),
                    "build": metadata.get("build"),
                    "path": row.get("owner_path", ""),
                    "target_path": row.get("target_path", ""),
                    "sequence": row["sequence"],
                    "historical_runtime": True,
                }
                generation = self.store._observe(
                    connection,
                    Observation(
                        owner,
                        provider,
                        _json(row),
                        path=row.get("owner_path", ""),
                        kind="runtime_observation",
                        provenance=context,
                    ),
                )
                guid = row.get("target_guid", "")
                resolved = bool(re.fullmatch(r"[a-f0-9]{32}", guid)) and bool(
                    row.get("target_path")
                )
                target = (
                    "asset:" + guid
                    if resolved
                    else "runtime-key:" + identity + ":" + str(row["sequence"])
                )
                self.store._insert_edge(
                    connection,
                    owner,
                    provider,
                    Edge(
                        target,
                        "runtime_" + row["kind"],
                        key=row.get("key", ""),
                        confidence="runtime_observed" if resolved else "runtime_unresolved",
                        evidence=_json(
                            {
                                "scheme": row.get("scheme", ""),
                                "handle": row.get("handle", ""),
                                "sequence": row["sequence"],
                            }
                        ),
                    ),
                )
                connection.execute(
                    "UPDATE observations SET state='ready' WHERE owner=? AND provider=?",
                    (owner, provider),
                )
                self.store._publish_context(connection, owner, provider, generation)
            connection.execute(
                "UPDATE graph_runtime_sessions SET state=?,offset=?,metadata=?,accepted=?,sequence=? WHERE id=?",
                (
                    page.get("state", "unknown"),
                    page["next_offset"],
                    _json(metadata),
                    accepted,
                    sequence,
                    identity,
                ),
            )
        return self.status(identity)

    @staticmethod
    def _handle(connection, identity, row):
        handle, kind = row.get("handle"), row["kind"]
        if not handle or kind not in {"acquire", "release"}:
            return
        connection.execute(
            "INSERT OR IGNORE INTO graph_runtime_handles VALUES (?,?,0,0,0,'','')",
            (identity, handle),
        )
        if kind == "acquire":
            connection.execute(
                "UPDATE graph_runtime_handles SET acquired=acquired+1,owner_path=?,target_path=? WHERE session=? AND handle=?",
                (row.get("owner_path", ""), row.get("target_path", ""), identity, handle),
            )
        else:
            connection.execute(
                "UPDATE graph_runtime_handles SET unmatched=unmatched+CASE WHEN released>=acquired THEN 1 ELSE 0 END,released=released+1 WHERE session=? AND handle=?",
                (identity, handle),
            )

    def events(self, identity, *, after=0, limit=100):
        GraphQueries._page_size(limit)
        if type(after) is not int or after < 0:
            raise ValueError("Runtime event cursor must be nonnegative")
        with self.store._connect() as connection:
            self._session(connection, identity)
            rows = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM graph_runtime_events WHERE session=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (identity, after, limit + 1),
                )
            ]
        admission = self.workspace.admission()
        catalog = self.workspace.key_admission()
        for row in rows:
            if not self.admitted(row, admission, catalog):
                raise RevisionConflict(
                    "Runtime observation is excluded by the current collection policy"
                )
        return {
            "events": rows[:limit],
            "next_after": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        }

    def handles(self, identity, *, after="", limit=100):
        GraphQueries._page_size(limit)
        if not isinstance(after, str):
            raise ValueError("Handle cursor must be a string")
        with self.store._connect() as connection:
            self._session(connection, identity)
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT handle,acquired,released,acquired-released AS balance,unmatched AS unmatched_releases,owner_path,target_path FROM graph_runtime_handles WHERE session=? AND handle>? ORDER BY handle LIMIT ?",
                    (identity, after, limit + 1),
                )
            ]
        admission = self.workspace.admission()
        for row in rows:
            if not self.admitted(row, admission):
                raise RevisionConflict(
                    "Runtime handle is excluded by the current collection policy"
                )
            row.pop("owner_path")
            row.pop("target_path")
        return {
            "handles": rows[:limit],
            "next_after": rows[limit - 1]["handle"] if len(rows) > limit else None,
            "not_a_leak_proof": True,
        }

    def release(self, identity):
        with self.store._connect(write=True) as connection:
            self._session(connection, identity)
            provider = "runtime:" + identity
            connection.execute(
                "DELETE FROM nodes WHERE id IN (SELECT owner FROM observations WHERE provider=?)",
                (provider,),
            )
            for table in ("edges", "observations", "evidence_context", "sources"):
                connection.execute(f"DELETE FROM {table} WHERE provider=?", (provider,))
            for table in (
                "graph_runtime_events",
                "graph_runtime_handles",
                "graph_runtime_blocked_handles",
            ):
                connection.execute(f"DELETE FROM {table} WHERE session=?", (identity,))
            connection.execute("DELETE FROM graph_runtime_sessions WHERE id=?", (identity,))
            self.store._bump(connection)
        return True
