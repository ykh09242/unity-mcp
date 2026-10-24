"""Scoped operational views and conservative, explicitly requested maintenance."""

from __future__ import annotations

import json
import math
import time

from services.reference_graph.store import _hash


CONFIG_FIELDS = {
    "rule": {"call", "argument", "scheme", "mode", "prefix", "suffix", "enabled"},
    "source": {"root", "include", "exclude", "mappings", "git_revision", "adapter"},
    "policy": {"exclude"},
}


def configuration(kind, value):
    if kind not in CONFIG_FIELDS or not isinstance(value, dict):
        raise ValueError("Configuration kind must be rule, source or policy")
    clean = {key: item for key, item in value.items() if key in CONFIG_FIELDS[kind]}
    if kind == "source":
        if "mappings" in clean:
            clean["mappings"] = [
                {
                    key: value
                    for key, value in mapping.items()
                    if key in {"field", "scheme", "mode", "prefix", "suffix", "values"}
                }
                for mapping in clean["mappings"]
                if isinstance(mapping, dict)
            ]
        if isinstance(clean.get("adapter"), dict):
            clean["adapter"] = {
                key: value for key, value in clean["adapter"].items() if key in {"name", "version"}
            }
    return clean


def import_action(options):
    kind = options.get("kind")
    clean = configuration(kind, options.get("config"))
    if clean != options["config"]:
        raise ValueError("Configuration contains unsupported fields")
    if kind == "source" and options.get("reauthorize") is not True:
        raise ValueError("Importing a source requires explicit reauthorize=true for this scope")
    if kind != "source" and ("enabled" in options or "reauthorize" in options):
        raise ValueError("Only sources accept enabled and reauthorize")
    result = {"config": clean, "expected_version": options.get("expected_version")}
    if kind != "policy":
        result["id"] = options.get("id")
    elif options.get("id", "policy") != "policy":
        raise ValueError("Policy id must be policy")
    if kind == "source":
        result["enabled"] = options.get("enabled", True)
    return {"rule": "set_rule", "source": "set_source", "policy": "set_policy"}[kind], result


def export_config(workspace, *, kind, after="", limit=100):
    if kind not in CONFIG_FIELDS or not isinstance(after, str):
        raise ValueError("Export kind must be rule, source or policy with a string after cursor")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Use 1-1000 entries per page and continue with next_after")
    with workspace.store._connect() as db:
        if kind == "policy":
            policy = workspace._state(db, "policy", {"version": 0, "exclude": []})
            rows = (
                []
                if after
                else [
                    {
                        "id": "policy",
                        "version": policy["version"],
                        "config": {"exclude": policy["exclude"]},
                    }
                ]
            )
        elif kind == "rule":
            rows = [
                {
                    "id": row["id"],
                    "version": row["version"],
                    "config": configuration(kind, json.loads(row["payload"])),
                }
                for row in db.execute(
                    "SELECT * FROM rules WHERE id>? ORDER BY id LIMIT ?", (after, limit + 1)
                )
            ]
        else:
            rows = [
                {
                    "id": row["provider"][9:],
                    "version": row["version"],
                    "enabled": bool(row["enabled"]),
                    "config": configuration(kind, json.loads(row["config"])),
                }
                for row in db.execute(
                    "SELECT * FROM sources WHERE provider LIKE 'external:%' AND provider>? ORDER BY provider LIMIT ?",
                    ("external:" + after, limit + 1),
                )
            ]
    return {
        "schema_version": 1,
        "kind": kind,
        "items": rows[:limit],
        "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
        "transfer": "per_item_versions_not_an_atomic_snapshot",
        "source_reauthorization_required": True,
    }


def overview(entry):
    workspace, store = entry.workspace, entry.workspace.store
    with store._connect() as db:
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs = {}
        for table, column in (
            ("graph_queries", "finished"),
            ("graph_snapshots", "state"),
            ("graph_mutations", "state"),
            ("graph_runtime_sessions", "state"),
        ):
            if table in tables:
                jobs[table] = dict(
                    db.execute(f"SELECT {column},count(*) FROM {table} GROUP BY {column}")
                )
        staging = db.execute("SELECT count(*) FROM graph_staging").fetchone()[0]
    return {
        "identity": {
            "principal": _hash((store.scope.user_id,)),
            "mode": "remote" if store.scope.user_id.startswith("remote:") else "local",
            "instance_id": store.scope.instance_id,
            "project_id": store.scope.project_id,
        },
        "storage": {
            "allocated_bytes": page_size * pages,
            "reusable_bytes": page_size * free,
            "includes_wal": False,
            "staging_rows": staging,
        },
        "memory": {
            "measurement": "cache_presence_only",
            "catalog_cached": entry.engine._catalog_cache is not None,
            "analysis_cached": entry.engine._analysis_cache is not None,
        },
        "jobs": jobs,
        "worker": {
            "running": entry.engine._gate.locked(),
            "watching": entry.watching,
            "active_requests": entry.active_requests,
        },
        "retention": "No automatic history deletion. Explicit cleanup removes completed, obsolete and undelivered query scratch and abandoned staging; ready snapshots, receipts, captures and authored configuration remain.",
    }


def cleanup(workspace, *, before, limit=100):
    if type(before) not in (int, float) or not math.isfinite(before) or before < 0:
        raise ValueError("before must be a nonnegative Unix timestamp")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Use 1-1000 records per cleanup batch; repeat while more is true")
    # Initialize/migrate before taking the cleanup writer transaction. All selection
    # and deletion below share its revision and exclude concurrent continuations.
    workspace.queries()
    with workspace.store._connect(write=True) as db:
        revision = db.execute("SELECT revision FROM graph_meta").fetchone()[0]
        eligible = "used<? AND (finished=1 OR abandoned=1 OR revision<>?)"
        rows = list(
            db.execute(
                f"SELECT id,finished FROM graph_queries WHERE {eligible} ORDER BY used,id LIMIT ?",
                (before, revision, limit),
            )
        )
        removed, obsolete, scratch, budget = 0, 0, 0, limit
        for identity, finished in rows:
            tables = [("graph_query_nodes", "job"), ("graph_query_queue", "job")]
            partial_snapshot = db.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=? AND state<>'ready'", (identity,)
            ).fetchone()
            if partial_snapshot:
                tables.append(("graph_snapshot_edges", "snapshot"))
            for table, column in tables:
                changed = db.execute(
                    f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {column}=? LIMIT ?)",
                    (identity, budget),
                ).rowcount
                scratch += changed
                budget -= changed
            if any(
                db.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (identity,)
                ).fetchone()
                for table, column in tables
            ):
                break
            db.execute("DELETE FROM graph_queries WHERE id=?", (identity,))
            if partial_snapshot:
                db.execute("DELETE FROM graph_snapshots WHERE id=?", (identity,))
            removed += int(bool(finished))
            obsolete += int(not finished)
            if not budget:
                break
        queries_remain = bool(
            db.execute(
                f"SELECT 1 FROM graph_queries WHERE {eligible} LIMIT 1", (before, revision)
            ).fetchone()
        )
        abandoned = list(
            db.execute(
                "SELECT rowid FROM graph_staging s WHERE NOT EXISTS (SELECT 1 FROM graph_worker w WHERE w.owner=s.worker AND w.expires>?) LIMIT ?",
                (time.time(), limit + 1),
            )
        )
        db.executemany("DELETE FROM graph_staging WHERE rowid=?", abandoned[:limit])
    return {
        "completed_queries_removed": removed,
        "obsolete_queries_removed": obsolete,
        "query_scratch_rows_removed": scratch,
        "abandoned_staging_removed": min(limit, len(abandoned)),
        "more": queries_remain or len(abandoned) > limit,
        "disk_reclamation": "reusable_sqlite_pages_no_vacuum",
    }


def retry(workspace):
    with workspace.store._connect(write=True) as db:
        # Failed assets are no longer dirty. Keep their retry eligibility and
        # recovery marker so clean_asset also invalidates dependent analysis.
        db.execute("UPDATE graph_asset_failures SET retry_at=0")
