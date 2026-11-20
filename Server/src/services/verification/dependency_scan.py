"""Page-atomic, disk-backed analysis of retained outgoing Reference Graph evidence."""

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import stat

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from services.reference_graph.queries import decode_cursor
from services.verification.dependency_closure import (
    COMMIT,
    GUID_LINE,
    IDENTIFIER,
    NODE,
    _asset,
    _page,
)
from services.verification.dependency_slots import serialized_slots
from services.verification.git_draft import _Git, _blob_ids
from services.verification.input_chunks import decode_object

PAGE_BYTES = 4 * 1024**2
_SCHEMA = (
    "CREATE TABLE scan (id INTEGER PRIMARY KEY CHECK(id=1), commit_id TEXT, root TEXT,"
    " root_node TEXT, scope TEXT, revision INTEGER, query_id TEXT, guard TEXT,"
    " graph_commit_binding TEXT, native_catalog_unfiltered INTEGER, next_cursor TEXT,"
    " pages INTEGER, scanned_edges INTEGER)",
    "CREATE TABLE pages (sequence INTEGER PRIMARY KEY, digest TEXT)",
    "CREATE TABLE assets (node TEXT PRIMARY KEY, path TEXT UNIQUE, source_hash TEXT, meta_hash TEXT)",
    "CREATE TABLE slots (owner TEXT, field TEXT, target TEXT, file_id TEXT, PRIMARY KEY(owner,field))",
    "CREATE TABLE edges (id TEXT PRIMARY KEY, owner TEXT, target TEXT)",
    "CREATE INDEX outgoing ON edges(owner,target)",
    "CREATE INDEX incoming ON edges(target,owner)",
    "CREATE TABLE issues (id TEXT PRIMARY KEY, reason TEXT)",
    "CREATE TABLE reachable (node TEXT PRIMARY KEY)",
)
_APP_ID = 0x56445331


def checkpoint_path(path):
    path = safe_local_path(path)
    for suffix in ("", "-journal", "-wal", "-shm"):
        item = safe_local_path(Path(str(path) + suffix))
        if item.exists() and not stat.S_ISREG(item.lstat().st_mode):
            raise ValueError("candidate_dependency_checkpoint_regular_file_required")
        if suffix and item.exists() and not path.exists():
            raise ValueError("candidate_dependency_orphaned_checkpoint_sidecar")
    return path


def _setup(db):
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA cache_size=-2048")
    db.execute("PRAGMA temp_store=FILE")
    db.row_factory = sqlite3.Row


def _initialize(db):
    application = db.execute("PRAGMA application_id").fetchone()[0]
    if application == _APP_ID:
        return
    if application or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
        raise ValueError("candidate_dependency_checkpoint_format_mismatch")
    for statement in _SCHEMA:
        db.execute(statement)
    db.execute(f"PRAGMA application_id={_APP_ID}")


def _asset_hashes(db, project, commit, paths):
    missing = [
        path
        for path in sorted(paths)
        if not db.execute("SELECT 1 FROM assets WHERE path=?", (path,)).fetchone()
    ]
    # The underlying Git reader bounds each blob to 4 MiB and each batch to
    # 32 MiB. One page never loads the whole closure into Python.
    git = _Git(project)
    names = [name for path in missing for name in (path, path + ".meta")]
    selected = _blob_ids(git, commit, names)
    pending = [missing[i : i + 64] for i in range(0, len(missing), 64)]
    while pending:
        batch = pending.pop()
        blobs = {
            name: selected[name]
            for path in batch
            for name in (path, path + ".meta")
            if name in selected
        }
        digests, guids, fields = {}, {}, {}
        try:
            with git.blobs(blobs) as results:
                for name, data in results:
                    digests[name] = sha256(data).hexdigest()
                    if name.endswith(".meta"):
                        ids = GUID_LINE.findall(data)
                        if len(ids) == 1:
                            guids[name[:-5]] = "asset:" + ids[0].decode()
                    else:
                        fields[name] = serialized_slots(data)
        except ValueError as error:
            if not str(error).startswith("candidate_draft_source_byte_limit") or len(batch) <= 4:
                raise
            middle = len(batch) // 2
            pending.extend((batch[:middle], batch[middle:]))
            continue
        for path in batch:
            node = guids.get(path)
            if node and path in digests:
                if db.execute("SELECT 1 FROM assets WHERE node=?", (node,)).fetchone():
                    raise ValueError("candidate_dependency_ambiguous_guid")
                db.execute(
                    "INSERT INTO assets VALUES (?,?,?,?)",
                    (node, path, digests[path], digests[path + ".meta"]),
                )
                db.executemany(
                    "INSERT INTO slots VALUES (?,?,?,?)",
                    ((node, *slot) for slot in fields[path]),
                )


def _supported(edge):
    return _observed(edge) and _asset(edge.get("source_path")) and _asset(edge.get("target_path"))


def _observed(edge):
    return (
        edge.get("provider") == "unity"
        and edge.get("kind") == "unity_dependency"
        and edge.get("confidence") == "observed"
        and edge.get("stale") is False
        and edge.get("target_exists") is True
    )


def _record_edges(db, rows):
    for edge in rows:
        identity = edge.get("id")
        if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
            raise ValueError("candidate_invalid_graph_edge")
        if db.execute(
            "SELECT 1 FROM edges WHERE id=? UNION ALL SELECT 1 FROM issues WHERE id=?",
            (identity, identity),
        ).fetchone():
            raise ValueError("candidate_repeated_graph_edge")
        if _supported(edge):
            owner, target = edge.get("owner"), edge.get("target")
            if all(
                isinstance(node, str)
                and NODE.fullmatch(node)
                and db.execute(
                    "SELECT 1 FROM assets WHERE node=? AND path=?", (node, path)
                ).fetchone()
                for node, path in ((owner, edge["source_path"]), (target, edge["target_path"]))
            ):
                db.execute("INSERT INTO edges VALUES (?,?,?)", (identity, owner, target))
                continue
            reason = "committed_asset_identity_mismatch"
        elif _observed(edge) and all(
            isinstance(edge.get(field), str) and edge[field]
            for field in ("source_path", "target_path")
        ):
            reason = "outside_selected_asset_types"
        elif edge.get("provider") == "unity":
            reason = "native_dependency_unresolved"
        else:
            reason = "outside_observed_native_dependencies"
        db.execute("INSERT INTO issues VALUES (?,?)", (identity, reason))


def _append(db, project, commit, root, document, digest):
    from services.verification.graph_commit_evidence import capture_guard, verify_impact_page

    page, (scope, revision) = _page(document)
    origin, query, sequence = (page.get(key) for key in ("node", "query_id", "page_sequence"))
    rows = page.get("edges")
    if (
        page.get("direction") != "outgoing"
        or page.get("max_depth", False) is not None
        or not isinstance(origin, str)
        or not NODE.fullmatch(origin)
        or not isinstance(query, str)
        or len(query) != 32
        or any(c not in "0123456789abcdef" for c in query)
        or type(sequence) is not int
        or sequence < 1
        or "path_found" in page
        or "next_cursor" not in page
        or not isinstance(rows, list)
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise ValueError("candidate_sequenced_unlimited_outgoing_impact_required")
    cursor = page["next_cursor"]
    visibility = page.get("native_asset_catalog_unfiltered")
    if visibility is not None and type(visibility) is not bool:
        raise ValueError("candidate_dependency_invalid_catalog_visibility")
    if cursor is not None:
        position = decode_cursor(scope, cursor)
        if (
            position.get("id") != query
            or type(position.get("sequence")) is not int
            or position["sequence"] != sequence
        ):
            raise ValueError("candidate_dependency_cursor_mismatch")
    scan = db.execute("SELECT * FROM scan").fetchone()
    if scan and (scan["commit_id"], scan["root"]) != (commit, root):
        raise ValueError("candidate_dependency_checkpoint_selection_mismatch")
    if not scan and _Git(project).read("cat-file", "-t", commit, limit=32).strip() != b"commit":
        raise ValueError("candidate_dependency_git_commit_required")
    if scan:
        if (scope, revision, query, origin) != tuple(
            scan[key] for key in ("scope", "revision", "query_id", "root_node")
        ):
            raise ValueError(
                "candidate_dependency_revision_or_query_changed: recollect this root from "
                "page 1 into a new checkpoint; retain the other revision's scan"
            )
        if visibility != scan["native_catalog_unfiltered"]:
            raise ValueError(
                "candidate_dependency_visibility_changed: recollect this root from page 1"
            )
        if old := db.execute("SELECT digest FROM pages WHERE sequence=?", (sequence,)).fetchone():
            if old[0] == digest:
                return
            raise ValueError("candidate_dependency_page_changed: recollect this root from page 1")
    if sequence != (scan["pages"] + 1 if scan else 1) or (scan and not scan["next_cursor"]):
        raise ValueError("candidate_dependency_page_sequence_gap: supply the next original page")
    status = page.get("graph_status")
    if (
        not isinstance(status, dict)
        or status.get("native_current") is not True
        or status.get("admission_pending")
    ):
        raise ValueError("candidate_dependency_collection_partial: refresh and recollect this root")
    binding = "verified" if verify_impact_page(project, commit, page) else "unverified"
    guard = json.dumps(capture_guard(page), sort_keys=True)
    if scan and (guard, binding) != (scan["guard"], scan["graph_commit_binding"]):
        raise ValueError("candidate_dependency_capture_changed: recollect this root from page 1")
    paths = {root}
    for edge in rows:
        if _supported(edge):
            paths.update((edge["source_path"], edge["target_path"]))
    _asset_hashes(db, project, commit, paths)
    if not db.execute("SELECT 1 FROM assets WHERE path=? AND node=?", (root, origin)).fetchone():
        raise ValueError("candidate_dependency_committed_root_mismatch")
    if not scan:
        db.execute(
            "INSERT INTO scan VALUES (1,?,?,?,?,?,?,?,?,?,NULL,0,0)",
            (commit, root, origin, scope, revision, query, guard, binding, visibility),
        )
    _record_edges(db, rows)
    db.execute("INSERT INTO pages VALUES (?,?)", (sequence, digest))
    db.execute(
        "UPDATE scan SET next_cursor=?, pages=?, scanned_edges=scanned_edges+?",
        (cursor, sequence, len(rows)),
    )
    if cursor is None:
        db.execute(
            "WITH RECURSIVE walk(node) AS (SELECT root_node FROM scan UNION "
            "SELECT e.target FROM walk JOIN edges e ON e.owner=walk.node) "
            "INSERT INTO reachable SELECT node FROM walk"
        )


def scan_summary(db, schema="main"):
    row = db.execute(f"SELECT * FROM {schema}.scan").fetchone()
    if not row:
        raise ValueError("candidate_dependency_scan_empty: supply page 1")

    def count(table):
        return db.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]

    revision = None
    if row["next_cursor"] is None:
        digest = sha256()
        for path, value in db.execute(
            f"SELECT path,source_hash FROM {schema}.reachable JOIN {schema}.assets USING(node) "
            f"UNION ALL SELECT path||'.meta',meta_hash FROM {schema}.reachable "
            f"JOIN {schema}.assets USING(node) ORDER BY 1"
        ):
            digest.update(f"{path}\n{value}\n".encode())
        revision = digest.hexdigest()
    return {
        "schema_version": 1,
        "kind": "verification_dependency_scan",
        "commit": row["commit_id"],
        "root": row["root"],
        "root_node": row["root_node"],
        "graph_scope": row["scope"],
        "graph_revision": row["revision"],
        "query_id": row["query_id"],
        "pages": row["pages"],
        "scanned_edges": row["scanned_edges"],
        "reachable_assets": count("reachable") if row["next_cursor"] is None else None,
        "closure_source_revision": revision,
        "issues": count("issues"),
        "next_cursor": row["next_cursor"],
        "state": "complete" if row["next_cursor"] is None else "awaiting_page",
        "graph_commit_binding": row["graph_commit_binding"],
        "coverage": "partial_static",
        "native_asset_catalog_unfiltered": None
        if row["native_catalog_unfiltered"] is None
        else bool(row["native_catalog_unfiltered"]),
    }


def scan_pages(project: Path, checkpoint: Path, commit: str, root: str, files):
    if not COMMIT.fullmatch(commit) or not _asset(root):
        raise ValueError("candidate_dependency_commit_and_asset_required")
    project, checkpoint = safe_local_path(project), checkpoint_path(checkpoint)
    with closing(sqlite3.connect(checkpoint)) as db:
        _setup(db)
        with db:
            db.execute("BEGIN IMMEDIATE")
            _initialize(db)
            scan = db.execute("SELECT commit_id, root FROM scan").fetchone()
            if scan and tuple(scan) != (commit, root):
                raise ValueError("candidate_dependency_checkpoint_selection_mismatch")
        for path in files:
            raw = read_bytes(path, PAGE_BYTES)
            with db:
                db.execute("BEGIN IMMEDIATE")
                _append(db, project, commit, root, decode_object(raw), sha256(raw).hexdigest())
        db.execute("BEGIN")
        return scan_summary(db)
