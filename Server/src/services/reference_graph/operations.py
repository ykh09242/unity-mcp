"""Scoped operational views and conservative, explicitly requested maintenance."""

from __future__ import annotations

import json
import math
import time

from services.reference_graph.store import InvalidGraphRequest, _hash
from services.reference_graph import reclamation, snapshot_storage
from services.reference_graph.storage_budget import file_sizes
from services.reference_graph.response_budget import PageBudget, bounded_rows


CONFIG_FIELDS = {
    "rule": {"call", "argument", "scheme", "mode", "prefix", "suffix", "enabled"},
    "source": {"root", "include", "exclude", "mappings", "git_revision", "adapter"},
    "policy": {"exclude", "respect_gitignore"},
}


def configuration(kind, value):
    if kind not in CONFIG_FIELDS or not isinstance(value, dict):
        raise InvalidGraphRequest("Configuration kind must be rule, source or policy")
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
        raise InvalidGraphRequest("Configuration contains unsupported fields")
    if kind == "source" and options.get("reauthorize") is not True:
        raise InvalidGraphRequest(
            "Importing a source requires explicit reauthorize=true for this scope"
        )
    if kind != "source" and ("enabled" in options or "reauthorize" in options):
        raise InvalidGraphRequest("Only sources accept enabled and reauthorize")
    result = {"config": clean, "expected_version": options.get("expected_version")}
    if kind != "policy":
        result["id"] = options.get("id")
    elif options.get("id", "policy") != "policy":
        raise InvalidGraphRequest("Policy id must be policy")
    if kind == "source":
        result["enabled"] = options.get("enabled", True)
    return {"rule": "set_rule", "source": "set_source", "policy": "set_policy"}[kind], result


def export_config(workspace, *, kind, after="", limit=100):
    if kind not in CONFIG_FIELDS or not isinstance(after, str):
        raise InvalidGraphRequest(
            "Export kind must be rule, source or policy with a string after cursor"
        )
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidGraphRequest("Use 1-1000 entries per page and continue with next_after")
    items, budget, more = [], PageBudget(), False
    with workspace.store._connect() as db:
        if kind == "policy":
            if not after:
                row = bounded_rows(
                    db, "SELECT value AS payload FROM graph_state WHERE key='policy'"
                ).fetchone()
                if row is not None:
                    budget.require(row)
                policy = (
                    json.loads(row["payload"]) if row is not None else {"version": 0, "exclude": []}
                )
                items.append(
                    {
                        "id": "policy",
                        "version": policy["version"],
                        "config": {
                            "exclude": policy["exclude"],
                            "respect_gitignore": policy.get("respect_gitignore", True),
                        },
                    }
                )
        elif kind == "rule":
            for row in bounded_rows(
                db, "SELECT * FROM rules WHERE id>? ORDER BY id LIMIT ?", (after, limit + 1)
            ):
                if len(items) == limit or not budget.admit(row):
                    more = True
                    break
                items.append(
                    {
                        "id": row["id"],
                        "version": row["version"],
                        "config": configuration(kind, json.loads(row["payload"])),
                    }
                )
        else:
            for row in bounded_rows(
                db,
                "SELECT * FROM sources WHERE provider LIKE 'external:%' AND provider>? ORDER BY provider LIMIT ?",
                ("external:" + after, limit + 1),
            ):
                if len(items) == limit or not budget.admit(row):
                    more = True
                    break
                items.append(
                    {
                        "id": row["provider"][9:],
                        "version": row["version"],
                        "enabled": bool(row["enabled"]),
                        "config": configuration(kind, json.loads(row["config"])),
                    }
                )
    return {
        "schema_version": 1,
        "kind": kind,
        "items": items,
        "next_after": items[-1]["id"] if more else None,
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
                jobs[table] = {
                    str(state): count
                    for state, count in db.execute(
                        f"SELECT {column},count(*) FROM {table} GROUP BY {column}"
                    )
                }
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
            "physical_bytes": sum(file_sizes(store.path).values()),
            "main_capacity_bytes": store._budget.capacity,
            "reserved_bytes": store._budget.reserved,
            "budget_enforcement": "hard_scope_admission_and_main_pages_cooperative_sidecars",
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
        "retention": "Saved history and authored configuration are never automatically deleted. Unchanged full-graph snapshots share evidence; explicit cleanup releases query scratch and abandoned staging. Operator storage budgets refuse new scopes or writes when exhausted; sidecar sampling is cooperative, not a filesystem quota.",
    }


def cleanup(workspace, *, before, limit=100):
    if type(before) not in (int, float) or not math.isfinite(before) or before < 0:
        raise InvalidGraphRequest("before must be a nonnegative Unix timestamp")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidGraphRequest("Use 1-1000 records per cleanup batch; repeat while more is true")
    # Initialize/migrate before taking the cleanup writer transaction. All selection
    # and deletion below share its revision and exclude concurrent continuations.
    workspace.queries()
    with workspace.store._connect(write=True, public_edit=True) as db:
        revision = db.execute("SELECT revision FROM graph_meta").fetchone()[0]
        scan = db.execute("SELECT * FROM graph_query_cleanup WHERE id=1").fetchone()
        after = (
            (scan["after_used"], scan["after_id"])
            if scan["after_used"] is not None
            and scan["revision"] == revision
            and before >= scan["cutoff"]
            else None
        )
        # Bound candidate work as well as deletions. Filtering eligibility in SQL
        # would scan arbitrarily many active jobs even with the ordering index.
        position = " AND (used,id)>(?,?)" if after is not None else ""
        rows = list(
            db.execute(
                f"SELECT id,finished,abandoned,revision,used FROM graph_queries WHERE used<?{position} ORDER BY used,id LIMIT ?",
                (before, *(after or ()), limit + 1),
            )
        )
        removed, obsolete, scratch, budget = 0, 0, 0, limit
        examined = 0
        for row in rows[:limit]:
            identity, finished, abandoned = row["id"], row["finished"], row["abandoned"]
            if not (finished or abandoned or row["revision"] != revision):
                after = (row["used"], identity)
                examined += 1
                continue
            reclaim_snapshot = db.execute(
                "SELECT 1 FROM graph_snapshots WHERE id=? AND (state<>'ready' OR ?=1)",
                (identity, abandoned),
            ).fetchone()
            changed, remaining = reclamation.delete_rows(
                db, reclamation.QUERY_TABLES, identity, budget
            )
            scratch += changed
            budget -= changed
            if remaining:
                break
            if (
                reclaim_snapshot
                and db.execute(
                    "SELECT 1 FROM graph_snapshot_refs WHERE snapshot=?", (identity,)
                ).fetchone()
            ):
                if not budget:
                    break
                scratch += 1
                budget -= 1
            db.execute("DELETE FROM graph_queries WHERE id=?", (identity,))
            if reclaim_snapshot:
                snapshot_storage.discard(db, identity)
            removed += int(bool(finished))
            obsolete += int(not finished)
            after = (row["used"], identity)
            examined += 1
            if not budget:
                break
        queries_remain = examined < len(rows)
        # A partially reclaimed job stays ahead of the anchor. Completing the
        # sweep resets it so a later explicit cleanup can observe new history.
        db.execute(
            "UPDATE graph_query_cleanup SET after_used=?,after_id=?,cutoff=?,revision=? WHERE id=1",
            (*(after if queries_remain and after is not None else (None, None)), before, revision),
        )
        reclaimed, released_remain = reclamation.reclaim(db, budget)
        scratch += reclaimed
        abandoned, staging_remain = reclamation.staging(db, limit, time.time())
    return {
        "completed_queries_removed": removed,
        "obsolete_queries_removed": obsolete,
        "query_scratch_rows_removed": scratch,
        "abandoned_staging_removed": abandoned,
        "more": queries_remain or released_remain or staging_remain,
        "disk_reclamation": "reusable_sqlite_pages_no_vacuum",
    }


def retry(workspace):
    with workspace.store._connect(write=True) as db:
        # Explicit retry must also join the ready queue, including while a
        # collector resumes an older deadline cursor. Keep the failure records
        # so clean_asset still invalidates dependent analysis after recovery.
        db.execute("UPDATE graph_asset_failures SET retry_at=0")
        if db.execute(
            "UPDATE graph_assets SET dirty=1 WHERE dirty<>1 "
            "AND guid IN (SELECT guid FROM graph_asset_failures)"
        ).rowcount:
            db.execute(
                "INSERT INTO graph_state VALUES ('summary_generation','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
            )
