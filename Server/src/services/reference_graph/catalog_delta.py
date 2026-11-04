"""Bounded catalog change identities; missing history always requests a full load."""

MAX_REVISIONS = 128
MAX_GUIDS = 8192


def initialize(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_catalog_revisions(revision INTEGER PRIMARY KEY,changes INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_catalog_deltas(revision INTEGER NOT NULL,guid TEXT NOT NULL,PRIMARY KEY(revision,guid))"
    )


def reset(connection):
    connection.execute("DELETE FROM graph_catalog_deltas")
    connection.execute("DELETE FROM graph_catalog_revisions")


def record(connection, revision: int, guids):
    # Oversized batches invalidate history instead of growing it without bound.
    changed = set()
    for guid in guids:
        changed.add(guid)
        if len(changed) > MAX_GUIDS:
            reset(connection)
            return
    connection.execute("INSERT INTO graph_catalog_revisions VALUES (?,?)", (revision, len(changed)))
    connection.executemany(
        "INSERT INTO graph_catalog_deltas VALUES (?,?)", ((revision, guid) for guid in changed)
    )
    cutoff = revision - MAX_REVISIONS
    overflow = connection.execute(
        "SELECT revision FROM graph_catalog_deltas ORDER BY revision DESC,guid LIMIT 1 OFFSET ?",
        (MAX_GUIDS,),
    ).fetchone()
    if overflow:
        cutoff = max(cutoff, overflow[0])
    connection.execute("DELETE FROM graph_catalog_deltas WHERE revision<=?", (cutoff,))
    connection.execute("DELETE FROM graph_catalog_revisions WHERE revision<=?", (cutoff,))


def changed_guids(connection, known: int, current: int):
    if not 0 <= known < current or current - known > MAX_REVISIONS:
        return None
    revisions, expected = connection.execute(
        "SELECT count(*),coalesce(sum(changes),0) FROM graph_catalog_revisions WHERE revision>? AND revision<=?",
        (known, current),
    ).fetchone()
    actual = connection.execute(
        "SELECT count(*) FROM graph_catalog_deltas WHERE revision>? AND revision<=?",
        (known, current),
    ).fetchone()[0]
    if revisions != current - known or actual != expected:
        return None
    return [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT guid FROM graph_catalog_deltas WHERE revision>? AND revision<=? ORDER BY guid",
            (known, current),
        )
    ]
