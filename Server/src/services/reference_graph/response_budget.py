"""Bound graph pages before SQLite values become retained Python evidence."""

import sqlite3

from models.response_limits import (
    MAX_RESPONSE_BYTES,
    MAX_RESPONSE_DEPTH,
    MAX_RESPONSE_NODES,
    MAX_RESPONSE_RETAINED_BYTES,
    response_size,
)


# Reserve space for graph status and for MCP's text/structured representations.
MAX_GRAPH_RESPONSE_BYTES = MAX_RESPONSE_BYTES // 4
MAX_PAGE_BYTES = MAX_GRAPH_RESPONSE_BYTES // 2
# Persisted jobs contain the admitted response plus continuation metadata. Replay
# must not reapply the smaller raw-row assembly charge to enriched evidence.
MAX_REPLAY_BYTES = MAX_GRAPH_RESPONSE_BYTES * 2
_JSON_COLUMNS = frozenset({"payload", "config", "previous", "current", "result"})


class GraphResponseTooLarge(ValueError):
    def __init__(self):
        super().__init__(
            "Reference graph response exceeds its byte budget; an individual item or metadata "
            "cannot be delivered. Narrow the request or reduce the stored item."
        )


def require_response(value: object) -> None:
    if (
        response_size(
            value,
            max_bytes=MAX_GRAPH_RESPONSE_BYTES,
            max_depth=MAX_RESPONSE_DEPTH - 8,
            max_nodes=MAX_RESPONSE_NODES // 4,
            max_retained=MAX_RESPONSE_RETAINED_BYTES // 4,
        )
        is None
    ):
        raise GraphResponseTooLarge()


def bounded_rows(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple = (),
    *,
    max_bytes: int | None = None,
):
    """Project an oversize row to nulls in SQLite, before crossing into Python.

    Statements and column names are server-owned. JSON columns already include
    escaping; other strings reserve worst-case escaping and evidence enrichment.
    The size marker survives suppression so callers never silently omit a row.
    """
    capacity = MAX_PAGE_BYTES if max_bytes is None else max_bytes
    metadata = connection.execute(f"SELECT * FROM ({statement}) LIMIT 0", parameters)
    columns = [item[0] for item in metadata.description]
    metadata.close()
    quoted = {name: '"' + name.replace('"', '""') + '"' for name in columns}
    cost = "512+" + "+".join(
        f"{1 if name in _JSON_COLUMNS else 6}*coalesce(length(cast({column} AS BLOB)),0)"
        for name, column in quoted.items()
    )
    projection = ",".join(
        f"CASE WHEN ({cost})<={capacity} THEN {column} END AS {column}"
        for column in quoted.values()
    )
    return connection.execute(
        f"SELECT {projection},({cost}) AS _response_bytes FROM ({statement})", parameters
    )


class PageBudget:
    """Request-local accumulator; mutate only after admitting a complete row."""

    def __init__(self, *, max_bytes: int | None = None):
        self.used = 0
        self.capacity = MAX_PAGE_BYTES if max_bytes is None else max_bytes

    def admit(self, row: sqlite3.Row) -> bool:
        size = row["_response_bytes"]
        if self.used + size > self.capacity:
            if not self.used:
                raise GraphResponseTooLarge()
            return False
        self.used += size
        return True

    def require(self, row: sqlite3.Row) -> None:
        if not self.admit(row):
            raise GraphResponseTooLarge()
