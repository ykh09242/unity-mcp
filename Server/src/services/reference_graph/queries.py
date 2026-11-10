"""Revision-bound exploration with resumable SQLite queues and snapshots.

Only a page is materialized in Python. Query jobs are scoped by their owning
GraphStore, do not retain connections, and have no total traversal/depth deadline.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from models.response_limits import response_size
from services.reference_graph.store import (
    GraphStore,
    RevisionConflict,
    _hash,
    _json,
    edge_candidates,
)
from services.reference_graph import path_proof, reclamation, snapshot_storage
from services.reference_graph.response_budget import (
    MAX_REPLAY_BYTES,
    GraphResponseTooLarge,
    PageBudget,
    bounded_rows,
    require_response,
)


_SELECT = """SELECT e.*,o.state,n.present,n.path AS target_path,src.path AS source_path,
 c.published AS provenance FROM edges e JOIN sources s ON s.provider=e.provider
 LEFT JOIN observations o ON o.owner=e.owner AND o.provider=e.provider
 LEFT JOIN evidence_context c ON c.owner=e.owner AND c.provider=e.provider
 LEFT JOIN nodes n ON n.id=e.target LEFT JOIN nodes src ON src.id=e.owner
 WHERE s.enabled=1 AND (SELECT ready FROM graph_admission WHERE id=1)=1
 AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=e.owner OR x.id=e.target)
 AND """


def encode_cursor(scope: str, value: dict) -> str:
    """Encode scoped continuation metadata without opening query storage."""
    return base64.urlsafe_b64encode(_json({"scope": scope, **value}).encode()).decode()


def decode_cursor(scope: str, cursor: str) -> dict:
    """Reject malformed or cross-scope continuations before using their anchors."""
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError("Invalid cursor")
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or data.get("scope") != scope:
            raise ValueError("Cursor belongs to another scope")
        return data
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("Invalid graph query cursor") from error


class GraphQueries:
    def __init__(self, store: GraphStore):
        self.store = store
        with store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graph_queries_schema(version INTEGER NOT NULL)"
            )
            version = connection.execute("SELECT version FROM graph_queries_schema").fetchone()
            if version and version[0] not in (1, 2, 3, 4, 5):
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
            if version is None or version[0] < 3:
                path_proof.initialize(connection)
                connection.execute("UPDATE graph_queries_schema SET version=3")
            if version is None or version[0] < 4:
                for statement in (
                    "CREATE INDEX IF NOT EXISTS graph_query_cleanup_order ON graph_queries(used,id)",
                    "CREATE TABLE IF NOT EXISTS graph_query_cleanup(id INTEGER PRIMARY KEY CHECK(id=1),after_used REAL,after_id TEXT,cutoff REAL,revision INTEGER)",
                    "INSERT OR IGNORE INTO graph_query_cleanup(id) VALUES (1)",
                    # A candidate becoming eligible behind a saved scan must be revisited.
                    # A revision change resets the scan in cleanup itself. Ineligible
                    # query progress and deleting candidates need no invalidation.
                    "CREATE TRIGGER IF NOT EXISTS graph_query_cleanup_insert AFTER INSERT ON graph_queries BEGIN UPDATE graph_query_cleanup SET after_used=NULL,after_id=NULL WHERE id=1 AND (NEW.used,NEW.id)<=(after_used,after_id) AND (NEW.finished=1 OR NEW.abandoned=1 OR NEW.revision<>(SELECT revision FROM graph_meta)); END",
                    "CREATE TRIGGER IF NOT EXISTS graph_query_cleanup_update AFTER UPDATE OF used,id,finished,abandoned,revision ON graph_queries BEGIN UPDATE graph_query_cleanup SET after_used=NULL,after_id=NULL WHERE id=1 AND (NEW.used,NEW.id)<=(after_used,after_id) AND (NEW.finished=1 OR NEW.abandoned=1 OR NEW.revision<>(SELECT revision FROM graph_meta)); END",
                ):
                    connection.execute(statement)
                connection.execute("UPDATE graph_queries_schema SET version=4")
            if version is None or version[0] < 5:
                # Empty work queues preserve existing jobs and legacy snapshots.
                reclamation.initialize(connection)
                connection.execute("UPDATE graph_queries_schema SET version=5")
            snapshot_storage.initialize(connection)

    @staticmethod
    def _page_size(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page size must be from 1 to 1000; continue for remaining results")

    @staticmethod
    def _revision(connection) -> int:
        return connection.execute("SELECT revision FROM graph_meta").fetchone()[0]

    @staticmethod
    def _sources(connection) -> dict:
        budget, sources = PageBudget(), {}
        for row in bounded_rows(
            connection,
            "SELECT provider,version FROM sources WHERE enabled=1 AND (provider LIKE 'external:%' OR provider LIKE 'runtime:%')",
        ):
            budget.require(row)
            sources[row["provider"]] = row["version"]
        return sources

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
        return encode_cursor(self.store.scope.key, value)

    def _decode(self, cursor: str) -> dict:
        return decode_cursor(self.store.scope.key, cursor)

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
        row = bounded_rows(
            connection,
            "SELECT * FROM graph_queries WHERE id=?",
            (identity,),
            max_bytes=MAX_REPLAY_BYTES,
        ).fetchone()
        if row is not None:
            PageBudget(max_bytes=MAX_REPLAY_BYTES).require(row)
        if row is None or row["abandoned"] or row["kind"] != kind or row["signature"] != signature:
            raise ValueError("Cursor belongs to another or released query")
        if row["revision"] != revision:
            raise RevisionConflict("Graph changed during exploration; start a new query")
        if sequence == row["sequence"] - 1 and row["result"] is not None:
            result = json.loads(row["result"])
            if kind == "snapshot":
                result = snapshot_storage.replay(connection, result)
            require_response(result)
            return dict(row), result, False
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
        # Admission must precede committing continuation state or snapshot history.
        require_response(result)
        persisted = result
        if job["kind"] == "snapshot":
            persisted = {
                **{key: value for key, value in result.items() if key != "edges"},
                "_snapshot_after": job["last"],
                "_snapshot_count": len(result["edges"]),
            }
        connection.execute(
            "UPDATE graph_queries SET sequence=?,last=?,result=?,finished=?,used=? WHERE id=?",
            (next_sequence, last, _json(persisted), int(not more), time.time(), job["id"]),
        )
        # Retain the last response for a lost-response retry. Explicit bounded
        # cleanup reclaims completed traversal scratch, including large visited sets.
        return result

    def explain(self, identity: str) -> dict:
        if not isinstance(identity, str) or not identity:
            raise ValueError("An edge id is required")
        with self.store._connect() as connection:
            rows = bounded_rows(connection, _SELECT + "e.id=?", (identity,))
            budget, edges = PageBudget(), []
            for row in rows:
                budget.require(row)
                edges.append(self._evidence(row))
            return {
                "revision": self._revision(connection),
                "edges": edges,
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
            if connection.execute(
                "SELECT 1 FROM graph_excluded_nodes WHERE id=? OR id=?", (node, target)
            ).fetchone():
                return {
                    "revision": self._revision(connection),
                    "edges": [],
                    "nodes": [],
                    "next_cursor": None,
                    "excluded": True,
                    "reason": "excluded_by_collection_policy",
                    "sources": self._sources(connection),
                }
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
            if target is not None and not created and job["result"]:
                if not {"_search_evidence", "_search_proof"}.intersection(
                    json.loads(job["result"])
                ):
                    raise RevisionConflict(
                        "Path predates evidence authorization; start a new query"
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

            # Search progress depends on every examined protected edge. Its
            # compact proof belongs to the durable job, never the response.
            def evidence_key(edge):
                if edge["provider"].startswith("external:"):
                    return (edge["provider"], edge["owner"])
                return (edge["provider"], edge["id"])

            prior = json.loads(job["result"]) if target is not None and job["result"] else {}

            def retain_evidence(edge):
                key = _hash(evidence_key(edge))
                provider = edge["provider"]
                compact = {"provider": provider, "owner": edge["owner"]}
                if not provider.startswith("external:"):
                    compact["id"] = edge["id"]
                if provider == "manual":
                    compact["target"] = edge["target"]
                elif provider.startswith("runtime:"):
                    compact["source_path"] = edge.get("source_path", "")
                    provenance = edge.get("provenance", {})
                    compact["provenance"] = {
                        name: provenance[name]
                        for name in ("path", "target_path")
                        if name in provenance
                    }
                proof_budget = PageBudget()
                charge = response_size(
                    compact, max_bytes=proof_budget.capacity, max_retained=proof_budget.capacity
                )
                if charge is None:
                    raise GraphResponseTooLarge()
                proof_budget.require({"_response_bytes": charge})
                connection.execute(
                    "INSERT OR IGNORE INTO graph_query_proofs(job,identity,payload) VALUES (?,?,?)",
                    (job["id"], key, _json(compact)),
                )

            for edge in prior.get("_search_evidence", []):
                retain_evidence(edge)
            edges, budget = [], limit * 2
            page_budget, full = PageBudget(), False
            examined = 0
            route_node = job["last"] or (node if target == node else "")
            while not route_node and budget and examined < limit and not full:
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
                count = min(limit - examined, max(1, budget), 256)
                rows = edge_candidates(
                    connection,
                    f"{column}=? AND id>?",
                    (queued["node"], queued["last"]),
                    count,
                )
                processed, last_edge = 0, None
                for row in rows:
                    if row["admitted"] is None:
                        PageBudget().require(row)
                    if not row["admitted"]:
                        examined += 1
                        processed += 1
                        last_edge = row["id"]
                        continue
                    if target is None:
                        if not page_budget.admit(row):
                            full = True
                            break
                    else:
                        # Enforce the per-edge budget even when no route is returned.
                        PageBudget().require(row)
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
                    if target is None:
                        edges.append(
                            {
                                **self._evidence(row),
                                "depth": queued["depth"] + 1,
                                "via": queued["via"],
                            }
                        )
                    else:
                        # Discovery still rejects corrupt evidence outside the
                        # chosen route, but need not retain its decoded payload.
                        evidence = self._evidence(row)
                        if evidence["provider"].startswith(("external:", "runtime:")) or (
                            evidence["provider"] == "manual"
                            and (
                                evidence["owner"].startswith("external:")
                                or evidence["target"].startswith("external:")
                            )
                        ):
                            retain_evidence(evidence)
                    examined += 1
                    processed += 1
                    last_edge = row["id"]
                    if target == neighbor:
                        route_node = neighbor
                        break
                rows.close()
                budget = max(0, budget - processed)
                if processed < count and not full:
                    connection.execute(
                        "DELETE FROM graph_query_queue WHERE seq=?", (queued["seq"],)
                    )
                elif processed:
                    connection.execute(
                        "UPDATE graph_query_queue SET last=? WHERE seq=?",
                        (last_edge, queued["seq"]),
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
                    # Trace the BFS parent links backwards in bounded pages;
                    # clients may order by depth to present start-to-target.
                    while route_node != node and len(edges) < limit:
                        parent = connection.execute(
                            "SELECT depth,via FROM graph_query_nodes WHERE job=? AND node=?",
                            (job["id"], route_node),
                        ).fetchone()
                        row = bounded_rows(
                            connection, _SELECT + "e.id=?", (parent["via"],)
                        ).fetchone()
                        if not page_budget.admit(row):
                            break
                        edges.append({**self._evidence(row), "depth": parent["depth"]})
                        route_node = row["owner"] if direction == "outgoing" else row["target"]
                    more = route_node != node
                detail = {
                    "path_found": True if route_node else None if more else False,
                    "path_order": "target_to_start",
                    "target": target,
                    "_search_proof": path_proof.marker(connection, self.store.scope.key, job["id"]),
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
            if not connection.execute("SELECT ready FROM graph_admission WHERE id=1").fetchone()[0]:
                raise RevisionConflict("Graph admission is incomplete; retry snapshot capture")
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
                snapshot_storage.begin(
                    connection,
                    job["id"],
                    snapshot_storage.SnapshotVersion(
                        job["revision"],
                        self._sources(connection),
                        connection.execute("PRAGMA schema_version").fetchone()[0],
                    ),
                )
            rows = edge_candidates(connection, "id>?", (job["last"],), limit + 1)
            edges, page_budget, more, examined, last = [], PageBudget(), False, 0, job["last"]
            for row in rows:
                if examined == limit:
                    more = True
                    break
                if row["admitted"] is None:
                    page_budget.require(row)
                if row["admitted"] and not page_budget.admit(row):
                    more = True
                    break
                examined += 1
                last = row["id"]
                if not row["admitted"]:
                    continue
                edges.append(self._evidence(row))
            rows.close()
            snapshot_storage.append(connection, job["id"], edges)
            connection.execute(
                "UPDATE graph_snapshots SET captured=captured+?,state=? WHERE id=?",
                (len(edges), "capturing" if more else "ready", job["id"]),
            )
            if not more:
                snapshot_storage.finish(connection, job["id"])
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
                last,
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
        admission = connection.execute(
            "SELECT revision,ready FROM graph_admission WHERE id=1"
        ).fetchone()
        if not admission[1] or result["revision"] < admission[0]:
            raise RevisionConflict(
                "Historical graph collection policy changed; excluded evidence requires a new snapshot"
            )
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
        position = None
        if cursor is not None:
            position = self._decode(cursor)
            if position.get("before") != before or position.get("after") != after:
                raise ValueError("Cursor belongs to another comparison")
            if "last_row" in position:
                if (
                    type(position["last_row"]) is not int
                    or not 0 < position["last_row"] < 2**63
                    or position.get("last_side") not in ("before", "after")
                    or not isinstance(position.get("last_hash"), str)
                ):
                    raise ValueError("Invalid comparison cursor anchor")
            elif isinstance(position.get("last"), str):
                last = position["last"]
            else:
                raise ValueError("Invalid legacy comparison cursor anchor")
        with self.store._connect() as connection:
            old, new = self._snapshot(connection, before), self._snapshot(connection, after)
            old_backing = snapshot_storage.backing(connection, before)
            new_backing = snapshot_storage.backing(connection, after)
            if position is not None and "last_row" in position:
                anchor = connection.execute(
                    "SELECT id FROM graph_snapshot_edges WHERE rowid=? AND snapshot=?",
                    (
                        position["last_row"],
                        old_backing if position["last_side"] == "before" else new_backing,
                    ),
                ).fetchone()
                if anchor is None or _hash(anchor["id"]) != position["last_hash"]:
                    raise ValueError("Invalid comparison cursor anchor; restart the comparison")
                last = anchor["id"]
            # Page candidate IDs before comparing payloads. Filtering unchanged
            # rows first would scan a whole identical snapshot in one request.
            rows = bounded_rows(
                connection,
                """WITH ids AS (
 SELECT id FROM graph_snapshot_edges WHERE snapshot=? AND id>?
 UNION SELECT id FROM graph_snapshot_edges WHERE snapshot=? AND id>?
 ORDER BY id LIMIT ?)
 SELECT ids.id,a.rowid AS old_row,b.rowid AS new_row,
 a.payload AS previous,b.payload AS current FROM ids
 LEFT JOIN graph_snapshot_edges a ON a.snapshot=? AND a.id=ids.id
 LEFT JOIN graph_snapshot_edges b ON b.snapshot=? AND b.id=ids.id
 ORDER BY ids.id""",
                (old_backing, last, new_backing, last, limit + 1, old_backing, new_backing),
            )
            more, compared, anchor, page_budget = False, 0, None, PageBudget()
            changes, access, manual_nodes = [], set(), set()
            for row in rows:
                if compared == limit or not page_budget.admit(row):
                    more = True
                    break
                compared += 1
                anchor = row
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
                manual_nodes.update(
                    node
                    for edge in (previous, current)
                    if edge is not None and edge["provider"] == "manual"
                    for node in (edge["owner"], edge["target"])
                    if node.startswith("external:")
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
            rows.close()
            return {
                "revision": self._revision(connection),
                "before": old,
                "after": new,
                "changes": changes,
                "_history_access": sorted(access),
                "_history_nodes": sorted(manual_nodes),
                "sources": {**old["sources"], **new["sources"]},
                "historical": True,
                "compared": compared,
                "next_cursor": self._encode(
                    {
                        "before": before,
                        "after": after,
                        # Immutable snapshot rows recover the ordered ID without
                        # copying an unbounded declaration identity into a token.
                        "last_row": anchor["old_row"] or anchor["new_row"],
                        "last_side": "before" if anchor["old_row"] else "after",
                        "last_hash": _hash(anchor["id"]),
                    }
                )
                if more
                else None,
            }

    @staticmethod
    def _release(connection, identity: str) -> bool:
        # Removing the public row fences cursors and proof readers immediately;
        # the same transaction durably records the backing that cleanup owns.
        removed = (
            connection.execute("DELETE FROM graph_queries WHERE id=?", (identity,)).rowcount > 0
        )
        if removed:
            reclamation.enqueue(connection, "query", identity)
        return removed

    @staticmethod
    def _mutation_owned(connection, identity: str) -> bool:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_mutations'"
        ).fetchone():
            return False
        return (
            connection.execute(
                "SELECT 1 FROM graph_mutations WHERE before_snapshot=? OR after_snapshot=? LIMIT 1",
                (identity, identity),
            ).fetchone()
            is not None
        )

    def release(self, identity: str) -> bool:
        with self.store._connect(write=True) as connection:
            if self._mutation_owned(connection, identity):
                return False
            row = connection.execute(
                "SELECT state FROM graph_snapshots WHERE id=?", (identity,)
            ).fetchone()
            if row is not None and row[0] != "ready":
                snapshot_storage.discard(connection, identity)
            return self._release(connection, identity)

    def abandon(self, identity: str) -> None:
        """Retire an undelivered first page; explicit cleanup reclaims it in batches."""
        with self.store._connect(write=True) as connection:
            connection.execute("UPDATE graph_queries SET abandoned=1 WHERE id=?", (identity,))
            # Cleanup can retire a finished query while its first response is
            # still being validated. Recreate only its cleanup marker if the
            # undisclosed ready snapshot survived; never reload evidence or a
            # replay payload, and never resurrect an explicitly deleted capture.
            connection.execute(
                "INSERT OR IGNORE INTO graph_queries(id,kind,signature,revision,sequence,last,finished,used,abandoned) SELECT id,'snapshot','',revision,0,'',1,created,1 FROM graph_snapshots WHERE id=? AND state='ready'",
                (identity,),
            )

    def delete_snapshot(self, identity: str) -> bool:
        with self.store._connect(write=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=?", (identity,)
            ).fetchone() or self._mutation_owned(connection, identity):
                return False
            self._release(connection, identity)
            return snapshot_storage.discard(connection, identity)
