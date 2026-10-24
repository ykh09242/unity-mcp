"""Revision-bound exploration with resumable SQLite queues and snapshots.

Only a page is materialized in Python. Query jobs are scoped by their owning
GraphStore, do not retain connections, and have no total traversal/depth deadline.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from services.reference_graph.store import GraphStore, RevisionConflict, _hash, _json


_SELECT = """SELECT e.*,o.state,n.present,n.path AS target_path,src.path AS source_path,
 c.published AS provenance FROM edges e JOIN sources s ON s.provider=e.provider
 LEFT JOIN observations o ON o.owner=e.owner AND o.provider=e.provider
 LEFT JOIN evidence_context c ON c.owner=e.owner AND c.provider=e.provider
 LEFT JOIN nodes n ON n.id=e.target LEFT JOIN nodes src ON src.id=e.owner
 WHERE s.enabled=1 AND """


class GraphQueries:
    def __init__(self, store: GraphStore):
        self.store = store
        with store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_queries_schema(version INTEGER NOT NULL)"
            )
            version = connection.execute("SELECT version FROM graph_queries_schema").fetchone()
            if version and version[0] not in (1, 2):
                raise ValueError("Unsupported graph query schema")
            if version is None:
                for statement in (
                    "CREATE TABLE graph_queries(id TEXT PRIMARY KEY,kind TEXT NOT NULL,signature TEXT NOT NULL,revision INTEGER NOT NULL,sequence INTEGER NOT NULL,last TEXT NOT NULL,result TEXT,finished INTEGER NOT NULL DEFAULT 0,used REAL NOT NULL,visited INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE graph_query_nodes(job TEXT NOT NULL,node TEXT NOT NULL,depth INTEGER NOT NULL,via TEXT,PRIMARY KEY(job,node))",
                    "CREATE TABLE graph_query_queue(seq INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT NOT NULL,node TEXT NOT NULL,last TEXT NOT NULL DEFAULT '')",
                    "CREATE INDEX graph_query_queue_job ON graph_query_queue(job,seq)",
                    "CREATE TABLE graph_snapshots(id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,captured INTEGER NOT NULL,sources TEXT NOT NULL,created REAL NOT NULL)",
                    "CREATE TABLE graph_snapshot_edges(snapshot TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(snapshot,id))",
                ):
                    connection.execute(statement)
                connection.execute("INSERT INTO graph_queries_schema VALUES (1)")
            if version is None or version[0] < 2:
                connection.execute(
                    "ALTER TABLE graph_queries ADD COLUMN abandoned INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute("UPDATE graph_queries_schema SET version=2")

    @staticmethod
    def _page_size(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page size must be from 1 to 1000; continue for remaining results")

    @staticmethod
    def _revision(connection) -> int:
        return connection.execute("SELECT revision FROM graph_meta").fetchone()[0]

    @staticmethod
    def _sources(connection) -> dict:
        return dict(
            connection.execute(
                "SELECT provider,version FROM sources WHERE enabled=1 AND (provider LIKE 'external:%' OR provider LIKE 'runtime:%')"
            )
        )

    @staticmethod
    def _evidence(row) -> dict:
        provenance = json.loads(row["provenance"] or "{}")
        stale = row["state"] != "ready" and row["provider"] != "manual"
        published_path = provenance.get("path")
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "owner": row["owner"],
            "provider": row["provider"],
            "source_path": published_path or ("" if stale else row["source_path"] or ""),
            "source_path_origin": "published"
            if published_path
            else "unavailable"
            if stale
            else "current_owner",
            "current_source_path": row["source_path"] or "",
            "target_path": row["target_path"] or "",
            "provenance": provenance,
            "stale": stale,
            "target_exists": None if row["present"] is None else bool(row["present"]),
        }

    def _encode(self, value: dict) -> str:
        return base64.urlsafe_b64encode(
            _json({"scope": self.store.scope.key, **value}).encode()
        ).decode()

    def _decode(self, cursor: str) -> dict:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError("Invalid cursor")
            data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(data, dict) or data.get("scope") != self.store.scope.key:
                raise ValueError("Cursor belongs to another scope")
            return data
        except (ValueError, TypeError, UnicodeError) as error:
            raise ValueError("Invalid graph query cursor") from error

    def _job(self, connection, kind: str, parameters: dict, cursor: str | None) -> tuple:
        revision = self._revision(connection)
        signature = _hash(parameters)
        if cursor is None:
            identity = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO graph_queries(id,kind,signature,revision,sequence,last,used) VALUES (?,?,?,?,0,'',?)",
                (identity, kind, signature, revision, time.time()),
            )
            return (
                dict(
                    connection.execute(
                        "SELECT * FROM graph_queries WHERE id=?", (identity,)
                    ).fetchone()
                ),
                None,
                True,
            )
        position = self._decode(cursor)
        identity, sequence = position.get("id"), position.get("sequence")
        if not isinstance(identity, str) or type(sequence) is not int or sequence < 0:
            raise ValueError("Invalid job cursor")
        row = connection.execute("SELECT * FROM graph_queries WHERE id=?", (identity,)).fetchone()
        if row is None or row["abandoned"] or row["kind"] != kind or row["signature"] != signature:
            raise ValueError("Cursor belongs to another or released query")
        if row["revision"] != revision:
            raise RevisionConflict("Graph changed during exploration; start a new query")
        if sequence == row["sequence"] - 1 and row["result"] is not None:
            return dict(row), json.loads(row["result"]), False
        if sequence != row["sequence"] or row["finished"]:
            raise RevisionConflict("Query continuation has already advanced")
        return dict(row), None, False

    def _finish_page(self, connection, job: dict, data: dict, more: bool, last: str = "") -> dict:
        next_sequence = job["sequence"] + 1
        result = {
            "revision": job["revision"],
            "query_id": job["id"],
            **data,
            "next_cursor": self._encode({"id": job["id"], "sequence": next_sequence})
            if more
            else None,
        }
        connection.execute(
            "UPDATE graph_queries SET sequence=?,last=?,result=?,finished=?,used=? WHERE id=?",
            (next_sequence, last, _json(result), int(not more), time.time(), job["id"]),
        )
        if not more:
            # Retain only the last response for a lost-response retry. The large
            # traversal queue and visited set have no value after completion.
            connection.execute("DELETE FROM graph_query_nodes WHERE job=?", (job["id"],))
        return result

    def explain(self, identity: str) -> dict:
        if not isinstance(identity, str) or not identity:
            raise ValueError("An edge id is required")
        with self.store._connect() as connection:
            rows = connection.execute(_SELECT + "e.id=?", (identity,)).fetchall()
            return {
                "revision": self._revision(connection),
                "edges": [self._evidence(row) for row in rows],
                "explanation_coverage": "recorded_evidence",
                "sources": self._sources(connection),
            }

    def impact(
        self,
        node: str,
        *,
        direction: str = "incoming",
        limit: int = 100,
        cursor: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        return self._traverse(
            node, direction=direction, limit=limit, cursor=cursor, max_depth=max_depth
        )

    def path(
        self,
        node: str,
        target: str,
        *,
        direction: str = "incoming",
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict:
        if not isinstance(target, str) or not target:
            raise ValueError("A target node is required")
        return self._traverse(node, target=target, direction=direction, limit=limit, cursor=cursor)

    def _traverse(
        self,
        node: str,
        *,
        target: str | None = None,
        direction: str = "incoming",
        limit: int = 100,
        cursor: str | None = None,
        max_depth: int | None = None,
    ) -> dict:
        self._page_size(limit)
        if not isinstance(node, str) or not node or direction not in {"incoming", "outgoing"}:
            raise ValueError("Use a node and incoming/outgoing direction")
        if max_depth is not None and (type(max_depth) is not int or max_depth < 1):
            raise ValueError("max_depth must be positive or omitted for all reachable nodes")
        with self.store._connect(write=True) as connection:
            job, replay, created = self._job(
                connection,
                "path" if target is not None else "impact",
                {
                    "node": node,
                    "target": target,
                    "direction": direction,
                    "limit": limit,
                    "max_depth": max_depth,
                },
                cursor,
            )
            if replay is not None:
                return replay
            visited = job["visited"]
            if created:
                visited = 1
                connection.execute(
                    "INSERT INTO graph_query_nodes VALUES (?,?,0,NULL)", (job["id"], node)
                )
                connection.execute(
                    "INSERT INTO graph_query_queue(job,node) VALUES (?,?)", (job["id"], node)
                )
            edges, budget = [], limit * 2
            route_node = job["last"] or (node if target == node else "")
            while not route_node and budget and len(edges) < limit:
                queued = connection.execute(
                    "SELECT q.*,n.depth,n.via FROM graph_query_queue q JOIN graph_query_nodes n ON n.job=q.job AND n.node=q.node WHERE q.job=? ORDER BY q.seq LIMIT 1",
                    (job["id"],),
                ).fetchone()
                if queued is None:
                    break
                budget -= 1
                if max_depth is not None and queued["depth"] >= max_depth:
                    connection.execute(
                        "DELETE FROM graph_query_queue WHERE seq=?", (queued["seq"],)
                    )
                    continue
                column = "owner" if direction == "outgoing" else "target"
                count = min(limit - len(edges), max(1, budget), 256)
                rows = connection.execute(
                    _SELECT + f"e.{column}=? AND e.id>? ORDER BY e.id LIMIT ?",
                    (queued["node"], queued["last"], count),
                ).fetchall()
                for row in rows:
                    neighbor = row["target"] if direction == "outgoing" else row["owner"]
                    inserted = connection.execute(
                        "INSERT OR IGNORE INTO graph_query_nodes VALUES (?,?,?,?)",
                        (job["id"], neighbor, queued["depth"] + 1, row["id"]),
                    ).rowcount
                    if inserted:
                        visited += 1
                        connection.execute(
                            "INSERT INTO graph_query_queue(job,node) VALUES (?,?)",
                            (job["id"], neighbor),
                        )
                    edges.append(
                        {**self._evidence(row), "depth": queued["depth"] + 1, "via": queued["via"]}
                    )
                    if target == neighbor:
                        route_node = neighbor
                        break
                budget = max(0, budget - len(rows))
                if len(rows) < count:
                    connection.execute(
                        "DELETE FROM graph_query_queue WHERE seq=?", (queued["seq"],)
                    )
                else:
                    connection.execute(
                        "UPDATE graph_query_queue SET last=? WHERE seq=?",
                        (rows[-1]["id"], queued["seq"]),
                    )
            more = (
                connection.execute(
                    "SELECT 1 FROM graph_query_queue WHERE job=? LIMIT 1", (job["id"],)
                ).fetchone()
                is not None
            )
            connection.execute(
                "UPDATE graph_queries SET visited=? WHERE id=?", (visited, job["id"])
            )
            detail = {}
            if target is not None:
                edges = []
                if route_node:
                    connection.execute("DELETE FROM graph_query_queue WHERE job=?", (job["id"],))
                    # Trace the BFS parent links backwards in bounded pages;
                    # clients may order by depth to present start-to-target.
                    while route_node != node and len(edges) < limit:
                        parent = connection.execute(
                            "SELECT depth,via FROM graph_query_nodes WHERE job=? AND node=?",
                            (job["id"], route_node),
                        ).fetchone()
                        row = connection.execute(_SELECT + "e.id=?", (parent["via"],)).fetchone()
                        edges.append({**self._evidence(row), "depth": parent["depth"]})
                        route_node = row["owner"] if direction == "outgoing" else row["target"]
                    more = route_node != node
                detail = {
                    "path_found": True if route_node else None if more else False,
                    "path_order": "target_to_start",
                    "target": target,
                }
            return self._finish_page(
                connection,
                job,
                {
                    "edges": edges,
                    "visited_nodes": visited,
                    "direction": direction,
                    "sources": self._sources(connection),
                    **detail,
                },
                more,
                route_node,
            )

    def snapshot(self, *, name: str = "", limit: int = 100, cursor: str | None = None) -> dict:
        self._page_size(limit)
        if not isinstance(name, str):
            raise ValueError("Snapshot name must be a string")
        with self.store._connect(write=True) as connection:
            job, replay, created = self._job(
                connection, "snapshot", {"name": name, "limit": limit}, cursor
            )
            if replay is not None:
                return replay
            if created:
                connection.execute(
                    "INSERT INTO graph_snapshots VALUES (?,?,?,'capturing',0,?,?)",
                    (
                        job["id"],
                        name,
                        job["revision"],
                        _json(self._sources(connection)),
                        time.time(),
                    ),
                )
            rows = connection.execute(
                _SELECT + "e.id>? ORDER BY e.id LIMIT ?", (job["last"], limit + 1)
            ).fetchall()
            more = len(rows) > limit
            edges = [self._evidence(row) for row in rows[:limit]]
            connection.executemany(
                "INSERT INTO graph_snapshot_edges VALUES (?,?,?)",
                ((job["id"], row["id"], _json(row)) for row in edges),
            )
            connection.execute(
                "UPDATE graph_snapshots SET captured=captured+?,state=? WHERE id=?",
                (len(edges), "capturing" if more else "ready", job["id"]),
            )
            snapshot = dict(
                connection.execute(
                    "SELECT * FROM graph_snapshots WHERE id=?", (job["id"],)
                ).fetchone()
            )
            snapshot["sources"] = json.loads(snapshot["sources"])
            return self._finish_page(
                connection,
                job,
                {"snapshot": snapshot, "edges": edges, "sources": snapshot["sources"]},
                more,
                edges[-1]["id"] if edges else job["last"],
            )

    def _snapshot(self, connection, identity: str) -> dict:
        if not isinstance(identity, str) or not identity:
            raise ValueError("Snapshot id is required")
        row = connection.execute(
            "SELECT * FROM graph_snapshots WHERE id=? AND state='ready'", (identity,)
        ).fetchone()
        if row is None:
            raise ValueError("Snapshot is not ready or does not belong to this scope")
        result = dict(row)
        result["sources"] = json.loads(result["sources"])
        current = self._sources(connection)
        if any(current.get(provider) != version for provider, version in result["sources"].items()):
            raise RevisionConflict(
                "Historical external source grant changed; capture a new snapshot"
            )
        return result

    def diff(self, before: str, after: str, *, limit: int = 100, cursor: str | None = None) -> dict:
        self._page_size(limit)
        last = ""
        if cursor is not None:
            position = self._decode(cursor)
            if (
                position.get("before") != before
                or position.get("after") != after
                or not isinstance(position.get("last"), str)
            ):
                raise ValueError("Cursor belongs to another comparison")
            last = position["last"]
        with self.store._connect() as connection:
            old, new = self._snapshot(connection, before), self._snapshot(connection, after)
            # Page candidate IDs before comparing payloads. Filtering unchanged
            # rows first would scan a whole identical snapshot in one request.
            rows = connection.execute(
                """WITH ids AS (
 SELECT id FROM graph_snapshot_edges WHERE snapshot=? AND id>?
 UNION SELECT id FROM graph_snapshot_edges WHERE snapshot=? AND id>?
 ORDER BY id LIMIT ?)
 SELECT ids.id,a.payload AS previous,b.payload AS current FROM ids
 LEFT JOIN graph_snapshot_edges a ON a.snapshot=? AND a.id=ids.id
 LEFT JOIN graph_snapshot_edges b ON b.snapshot=? AND b.id=ids.id
 ORDER BY ids.id""",
                (before, last, after, last, limit + 1, before, after),
            ).fetchall()
            more = len(rows) > limit
            changes, access = [], set()
            for row in rows[:limit]:
                previous = json.loads(row["previous"]) if row["previous"] else None
                current = (
                    previous
                    if row["previous"] == row["current"]
                    else json.loads(row["current"])
                    if row["current"]
                    else None
                )
                # An unchanged comparison is evidence too. Authorize all page
                # candidates before delivering even an empty changes array.
                access.update(
                    (edge["provider"], edge["source_path"])
                    for edge in (previous, current)
                    if edge is not None and edge["provider"].startswith("external:")
                )
                access.update(
                    (edge["provider"], path)
                    for edge in (previous, current)
                    if edge is not None and edge["provider"].startswith("runtime:")
                    for path in (
                        edge.get("source_path", ""),
                        edge.get("provenance", {}).get("target_path", ""),
                    )
                    if path
                )
                if row["previous"] != row["current"]:
                    changes.append(
                        {
                            "id": row["id"],
                            "change": "added"
                            if previous is None
                            else "removed"
                            if current is None
                            else "modified",
                            "before": previous,
                            "after": current,
                        }
                    )
            return {
                "revision": self._revision(connection),
                "before": old,
                "after": new,
                "changes": changes,
                "_history_access": sorted(access),
                "sources": {**old["sources"], **new["sources"]},
                "historical": True,
                "compared": min(limit, len(rows)),
                "next_cursor": self._encode(
                    {"before": before, "after": after, "last": rows[limit - 1]["id"]}
                )
                if more
                else None,
            }

    @staticmethod
    def _release(connection, identity: str) -> bool:
        connection.execute("DELETE FROM graph_query_nodes WHERE job=?", (identity,))
        connection.execute("DELETE FROM graph_query_queue WHERE job=?", (identity,))
        return connection.execute("DELETE FROM graph_queries WHERE id=?", (identity,)).rowcount > 0

    def release(self, identity: str) -> bool:
        with self.store._connect(write=True) as connection:
            row = connection.execute(
                "SELECT state FROM graph_snapshots WHERE id=?", (identity,)
            ).fetchone()
            if row is not None and row[0] != "ready":
                connection.execute("DELETE FROM graph_snapshot_edges WHERE snapshot=?", (identity,))
                connection.execute("DELETE FROM graph_snapshots WHERE id=?", (identity,))
            return self._release(connection, identity)

    def abandon(self, identity: str) -> None:
        """Retire an undelivered first page; explicit cleanup reclaims it in batches."""
        with self.store._connect(write=True) as connection:
            connection.execute("UPDATE graph_queries SET abandoned=1 WHERE id=?", (identity,))

    def delete_snapshot(self, identity: str) -> bool:
        with self.store._connect(write=True) as connection:
            self._release(connection, identity)
            connection.execute("DELETE FROM graph_snapshot_edges WHERE snapshot=?", (identity,))
            return (
                connection.execute("DELETE FROM graph_snapshots WHERE id=?", (identity,)).rowcount
                > 0
            )
