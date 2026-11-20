"""Capture the selected page's native inputs under the graph's existing admission fence."""

from hashlib import sha256
import json
import re

from services.reference_graph.store import InvalidGraphRequest, RevisionConflict
from services.verification.input_chunks import validate_input_paths

COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
NODE = re.compile(r"asset:[0-9a-f]{32}")
MAX_ASSETS = 32


def page_digest(page):
    payload = {
        key: value
        for key, value in page.items()
        if not key.startswith("_") and key not in {"scope", "graph_status", "commit_evidence"}
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def page_nodes(page):
    endpoints = [page.get("node")]
    edges = page.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("graph_commit_invalid_edges")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("graph_commit_invalid_edge")
        endpoints.extend((edge.get("owner"), edge.get("target")))
    if not all(isinstance(node, str) and NODE.fullmatch(node) for node in endpoints):
        raise ValueError("graph_commit_non_asset_endpoint")
    nodes = set(endpoints)
    if len(nodes) > MAX_ASSETS:
        raise ValueError("graph_commit_asset_limit: reduce the page size")
    return sorted(nodes)


def published_inputs(store, page):
    nodes = page_nodes(page)
    with store.read_snapshot(), store._connect() as connection:
        if store.revision() != page["revision"]:
            raise RevisionConflict("Graph changed before commit capture")
        inputs = {}
        for node in nodes:
            row = connection.execute(
                "SELECT n.path,o.fingerprint,json_extract(v.payload,'$.stamp'),"
                "json_extract(v.payload,'$.meta_stamp'),json_extract(v.payload,'$.import_witness') "
                "FROM nodes n JOIN observations o ON o.owner=n.id "
                "JOIN graph_collected_versions v ON v.guid=substr(n.id,7) "
                "WHERE n.id=? AND n.present=1 AND o.provider='unity' AND o.state='ready' "
                "AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=n.id)",
                (node,),
            ).fetchone()
            if not row or not row[0].startswith("Assets/"):
                raise InvalidGraphRequest("graph_commit_missing_admitted_input")
            inputs[node] = {
                "path": row[0],
                "import_hash": row[1],
                "stamp": row[2],
                "meta_stamp": row[3],
                "import_witness": json.loads(row[4]) if row[4] else None,
            }
    validate_input_paths([value["path"] for value in inputs.values()])
    return inputs


async def capture(entry, page, commit, editor):
    from services.reference_graph.reconcile import drained_thread

    store = entry.workspace.store
    inputs = await drained_thread(published_inputs, store, page)
    paths = [value["path"] for value in inputs.values()]
    before = await entry.collect("commit_inputs", paths=paths)
    observed = await entry.collect("commit_links", paths=paths)
    current = await drained_thread(published_inputs, store, page)
    after = await entry.collect("commit_inputs", paths=paths, closing=True)
    if current != inputs or any(
        observation.get("epoch") != editor.get("epoch")
        or observation.get("revision") != editor.get("revision")
        for observation in (before, observed, after)
    ):
        raise RevisionConflict("graph_commit_collection_changed")
    return {
        "version": 1,
        "commit": commit,
        "scope": store.scope.key,
        "revision": page["revision"],
        "page_sha256": page_digest(page),
        "published": inputs,
        "native_links": observed.get("links"),
        "imports_required": [
            row["path"]
            for row in after.get("assets", [])
            if not isinstance(row.get("import_witness"), dict)
            or any(
                row["import_witness"].get(key) != row.get(key)
                for key in ("guid", "import_hash", "asset_sha256", "meta_sha256")
            )
        ],
        "before": before,
        "after": after,
    }
