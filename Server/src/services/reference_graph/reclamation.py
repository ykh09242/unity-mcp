"""Durable, bounded physical reclamation after public identities are retired."""

QUERY_TABLES = (
    ("graph_query_proofs", "job"),
    ("graph_query_nodes", "job"),
    ("graph_query_queue", "job"),
)
SNAPSHOT_TABLES = (
    ("graph_snapshot_edges", "snapshot"),
    ("graph_snapshot_versions", "backing"),
    ("graph_snapshot_contents", "backing"),
)
RUNTIME_TABLES = (
    ("graph_runtime_events", "session"),
    ("graph_runtime_handles", "session"),
    ("graph_runtime_blocked_handles", "session"),
    ("graph_runtime_bindings", "session"),
)
RUNTIME_EVIDENCE = (("edges", "provider"), ("evidence_context", "provider"))


def owner_in_use(owner):
    """Shared owner metadata survives while any enabled evidence uses it."""
    return (
        "(EXISTS (SELECT 1 FROM observations live JOIN sources s USING(provider) "
        f"WHERE live.owner={owner} AND s.enabled=1) OR "
        "EXISTS (SELECT 1 FROM edges live JOIN sources s USING(provider) "
        f"WHERE live.owner={owner} AND s.enabled=1) OR "
        "EXISTS (SELECT 1 FROM edges live JOIN sources s USING(provider) "
        f"WHERE live.target={owner} AND s.enabled=1))"
    )


def initialize(connection):
    for statement in (
        "CREATE TABLE IF NOT EXISTS graph_reclamation(kind TEXT NOT NULL,identity TEXT NOT NULL,PRIMARY KEY(kind,identity))",
        "CREATE TABLE IF NOT EXISTS graph_staging_cleanup(id INTEGER PRIMARY KEY CHECK(id=1),after_rowid INTEGER NOT NULL DEFAULT 0,through_rowid INTEGER,worker TEXT)",
        "INSERT OR IGNORE INTO graph_staging_cleanup(id) VALUES (1)",
    ):
        connection.execute(statement)


def enqueue(connection, kind, identity):
    connection.execute("INSERT OR IGNORE INTO graph_reclamation VALUES (?,?)", (kind, identity))


def delete_rows(connection, tables, identity, budget):
    """Every backing predicate has an index; LIMIT applies before deletion."""
    removed = 0
    for table, column in tables:
        if removed == budget:
            break
        changed = connection.execute(
            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {column}=? LIMIT ?)",
            (identity, budget - removed),
        ).rowcount
        removed += changed
        if table == "edges" and changed:
            # Same-revision physical cleanup must not strand bounded row anchors.
            # The generation and deletion commit or roll back together.
            connection.execute("UPDATE graph_cursor_state SET reclamation=reclamation+1 WHERE id=1")
    remaining = any(
        connection.execute(
            f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (identity,)
        ).fetchone()
        for table, column in tables
    )
    return removed, remaining


def runtime(connection, identity, budget):
    removed, remaining = delete_rows(connection, RUNTIME_TABLES, identity, budget)
    if remaining or removed == budget:
        return removed, True
    provider = "runtime:" + identity
    changed, remaining = delete_rows(connection, RUNTIME_EVIDENCE, provider, budget - removed)
    removed += changed
    if remaining or removed == budget:
        return removed, True
    # Keep the observation until its owner metadata is reclaimed. Each candidate
    # is indexed and bounded, even when shared owners cannot be removed. A small
    # budget may stop between metadata deletions and resume the same owner later.
    owners = list(
        connection.execute(
            "SELECT owner FROM observations WHERE provider=? ORDER BY owner LIMIT ?",
            (provider, budget - removed),
        )
    )
    for row in owners:
        owner = row[0]
        shared = connection.execute("SELECT " + owner_in_use("?1"), (owner,)).fetchone()[0]
        if not shared:
            for table in ("graph_excluded_nodes", "nodes"):
                if removed == budget:
                    return removed, True
                removed += connection.execute(f"DELETE FROM {table} WHERE id=?", (owner,)).rowcount
        if removed == budget:
            return removed, True
        removed += connection.execute(
            "DELETE FROM observations WHERE provider=? AND owner=?", (provider, owner)
        ).rowcount
    return removed, connection.execute(
        "SELECT 1 FROM observations WHERE provider=? LIMIT 1", (provider,)
    ).fetchone() is not None


def reclaim(connection, budget):
    removed = 0
    # Queue entries are already eligible. Bound empty-job processing too, and
    # keep a partially reclaimed entry at the front until its backing is gone.
    rows = list(
        connection.execute(
            "SELECT kind,identity FROM graph_reclamation ORDER BY rowid LIMIT ?", (budget,)
        )
    )
    for kind, identity in rows:
        if kind == "runtime":
            changed, remaining = runtime(connection, identity, budget - removed)
        else:
            changed, remaining = delete_rows(
                connection,
                QUERY_TABLES if kind == "query" else SNAPSHOT_TABLES,
                identity,
                budget - removed,
            )
        removed += changed
        if remaining:
            break
        connection.execute(
            "DELETE FROM graph_reclamation WHERE kind=? AND identity=?", (kind, identity)
        )
        if removed == budget:
            break
    return removed, connection.execute(
        "SELECT 1 FROM graph_reclamation LIMIT 1"
    ).fetchone() is not None


def staging(connection, limit, now):
    worker = connection.execute(
        "SELECT owner FROM graph_worker WHERE id=1 AND expires>?", (now,)
    ).fetchone()
    owner = worker[0] if worker else None
    scan = connection.execute("SELECT * FROM graph_staging_cleanup WHERE id=1").fetchone()
    after, through = scan["after_rowid"], scan["through_rowid"]
    if through is None or scan["worker"] != owner:
        after = 0
        through = connection.execute("SELECT coalesce(max(rowid),0) FROM graph_staging").fetchone()[
            0
        ]
    # Page before eligibility filtering. A finite high-water mark lets a sweep
    # finish while publishers keep inserting; later sweeps inspect new rows.
    rows = list(
        connection.execute(
            "SELECT rowid,worker FROM graph_staging WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
            (after, through, limit + 1),
        )
    )
    abandoned = [(row["rowid"],) for row in rows[:limit] if row["worker"] != owner]
    connection.executemany("DELETE FROM graph_staging WHERE rowid=?", abandoned)
    more = len(rows) > limit
    connection.execute(
        "UPDATE graph_staging_cleanup SET after_rowid=?,through_rowid=?,worker=? WHERE id=1",
        (rows[limit - 1]["rowid"] if more else 0, through if more else None, owner),
    )
    return len(abandoned), more
