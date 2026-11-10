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
        removed += connection.execute(
            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {column}=? LIMIT ?)",
            (identity, budget - removed),
        ).rowcount
    remaining = any(
        connection.execute(
            f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (identity,)
        ).fetchone()
        for table, column in tables
    )
    return removed, remaining


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
