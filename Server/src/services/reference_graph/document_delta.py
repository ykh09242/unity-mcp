"""Bounded document identity history and durable pending code publications."""

from collections.abc import Iterable

from services.reference_graph.store import RevisionConflict, _json

MAX_EVENTS = 8192


def initialize(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_document_clock(id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL)"
    )
    connection.execute("INSERT OR IGNORE INTO graph_document_clock VALUES (1,0)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_document_changes(generation INTEGER PRIMARY KEY,owner TEXT NOT NULL)"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS graph_pending_analysis(owner TEXT PRIMARY KEY)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS graph_documents_format_owner ON graph_documents(format,owner)"
    )
    dirty = """
        INSERT INTO graph_state VALUES ('analysis_dirty','true')
        ON CONFLICT(key) DO UPDATE SET value='true' WHERE value<>'true';
    """
    for action, identity, changed_csharp in (
        ("INSERT", "NEW", "NEW.format='csharp'"),
        ("UPDATE", "NEW", "OLD.format='csharp' OR NEW.format='csharp'"),
        ("DELETE", "OLD", "OLD.format='csharp'"),
    ):
        owner = identity + ".owner"
        # Owners are immutable source identities. A deletion records the same
        # identity as an edit: absence at the captured generation is its tombstone.
        queue = (
            f"DELETE FROM graph_pending_analysis WHERE owner={owner};"
            if action == "DELETE"
            else f"INSERT INTO graph_pending_analysis VALUES ({owner}) ON CONFLICT DO NOTHING;"
        )
        connection.execute(f"""
            CREATE TRIGGER IF NOT EXISTS graph_document_{action} AFTER {action} ON graph_documents
            BEGIN
                UPDATE graph_document_clock SET generation=generation+1 WHERE id=1;
                INSERT INTO graph_document_changes
                    SELECT generation,{owner} FROM graph_document_clock WHERE id=1;
                DELETE FROM graph_document_changes WHERE generation<=
                    (SELECT generation FROM graph_document_clock WHERE id=1)-{MAX_EVENTS};
                {queue}
                INSERT INTO graph_changed_inputs SELECT {owner} WHERE {changed_csharp}
                    ON CONFLICT DO NOTHING;
                {dirty}
            END
        """)

    # These triggers also cover rule changes, rebuilds and streamed promotion,
    # which modify observations directly rather than saving a document.
    for action in ("INSERT", "UPDATE", "DELETE"):
        owner = "OLD.owner" if action == "DELETE" else "NEW.owner"
        provider = "OLD.provider" if action == "DELETE" else "NEW.provider"
        if action == "DELETE":
            queue = f"INSERT INTO graph_pending_analysis VALUES ({owner}) ON CONFLICT DO NOTHING;"
        else:
            queue = f"""
                DELETE FROM graph_pending_analysis WHERE owner={owner} AND NEW.state='ready';
                INSERT INTO graph_pending_analysis SELECT {owner} WHERE NEW.state<>'ready'
                    ON CONFLICT DO NOTHING;
            """
        connection.execute(f"""
            CREATE TRIGGER IF NOT EXISTS graph_code_observation_{action}
            AFTER {action} ON observations
            WHEN {provider}='code' AND EXISTS(SELECT 1 FROM graph_documents WHERE owner={owner})
            BEGIN
                {queue}
                {dirty}
            END
        """)
    connection.execute("""
        INSERT INTO graph_pending_analysis
        SELECT d.owner FROM graph_documents d LEFT JOIN observations o
            ON o.owner=d.owner AND o.provider='code'
        WHERE o.state IS NULL OR o.state<>'ready' ON CONFLICT DO NOTHING
    """)
    if connection.execute("SELECT 1 FROM graph_pending_analysis LIMIT 1").fetchone():
        connection.execute(dirty)


def reset(connection):
    """A workspace reset breaks continuity even when the old collection was empty."""
    connection.execute("UPDATE graph_document_clock SET generation=generation+1 WHERE id=1")
    connection.execute("DELETE FROM graph_document_changes")


def generation(connection) -> int:
    row = connection.execute("SELECT generation FROM graph_document_clock WHERE id=1").fetchone()
    return row[0]


def require_generation(connection, expected: int | None):
    if expected is not None and expected != generation(connection):
        raise RevisionConflict("Documents changed during analysis")


def changed_owners(connection, known: int, current: int) -> set[str] | None:
    if not 0 <= known <= current or current - known > MAX_EVENTS:
        return None
    rows = connection.execute(
        "SELECT owner FROM graph_document_changes WHERE generation>? AND generation<=?",
        (known, current),
    ).fetchall()
    # Generations are unique; the exact number in the closed interval proves
    # continuity, including the newest event even if its journal row was lost.
    return {row[0] for row in rows} if len(rows) == current - known else None


def csharp_page(connection, after: str, limit: int) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT owner FROM graph_documents WHERE format='csharp' AND owner>? ORDER BY owner LIMIT ?",
            (after, limit),
        )
    ]


def changed_csharp(connection, owners: Iterable[str]) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT d.owner FROM json_each(?) q CROSS JOIN graph_documents d "
            "ON d.owner=q.value WHERE d.format='csharp'",
            (_json(list(owners)),),
        )
    }


def queue(connection, owners: Iterable[str]):
    connection.executemany(
        "INSERT INTO graph_pending_analysis SELECT owner FROM graph_documents WHERE owner=? "
        "ON CONFLICT DO NOTHING",
        ((owner,) for owner in owners),
    )


def queue_all(connection):
    connection.execute(
        "INSERT OR IGNORE INTO graph_pending_analysis SELECT owner FROM graph_documents"
    )


def pending(connection, after: str, limit: int) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT owner FROM graph_pending_analysis WHERE owner>? ORDER BY owner LIMIT ?",
            (after, limit),
        )
    ]


def has_eligible_pending(connection, blocked: set[str]) -> bool:
    # A concurrent observation invalidation can land behind the page cursor.
    # Unavailable owners stay queued until their failure closure recovers.
    return any(
        row[0] not in blocked
        for row in connection.execute("SELECT owner FROM graph_pending_analysis")
    )
