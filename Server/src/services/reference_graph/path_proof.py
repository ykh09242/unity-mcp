"""Scope-bound durable path evidence, streamed without caching authorization."""

import json

from services.reference_graph.response_budget import PageBudget, bounded_rows
from services.reference_graph.store import RevisionConflict


def initialize(connection):
    connection.execute(
        "CREATE TABLE graph_query_proofs(seq INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT NOT NULL,identity TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(job,identity))"
    )
    connection.execute("CREATE INDEX graph_query_proofs_page ON graph_query_proofs(job,seq)")


def marker(connection, scope: str, job: str) -> dict:
    through, count = connection.execute(
        "SELECT coalesce(max(seq),0),count(*) FROM graph_query_proofs WHERE job=?", (job,)
    ).fetchone()
    return {"scope": scope, "through": through, "count": count}


def batches(store, result: dict):
    """Recheck ownership and revision per bounded batch; hold no DB while validating."""
    proof = result["_search_proof"]
    if (
        not isinstance(proof, dict)
        or proof.get("scope") != store.scope.key
        or any(type(proof.get(key)) is not int or proof[key] < 0 for key in ("through", "count"))
    ):
        raise RevisionConflict("Path proof belongs to another scope or is invalid")
    after, count = 0, 0
    while True:
        with store._connect() as connection:
            job = connection.execute(
                "SELECT revision,abandoned FROM graph_queries WHERE id=? AND kind='path'",
                (result["query_id"],),
            ).fetchone()
            revision = connection.execute("SELECT revision FROM graph_meta").fetchone()[0]
            if (
                job is None
                or job["abandoned"]
                or job["revision"] != revision
                or revision != result["revision"]
            ):
                raise RevisionConflict("Path proof changed or was released; start a new query")
            rows = bounded_rows(
                connection,
                "SELECT seq,payload FROM graph_query_proofs WHERE job=? AND seq>? AND seq<=? ORDER BY seq LIMIT 128",
                (result["query_id"], after, proof["through"]),
            )
            budget, batch = PageBudget(), []
            for row in rows:
                if not budget.admit(row):
                    break
                evidence = json.loads(row["payload"])
                # External owners and manual endpoints expand to source configuration
                # during authorization. Compact proof bytes cannot bound that expansion;
                # authorize each such record independently while retaining runtime batches.
                expanded = evidence["provider"] == "manual" or evidence["provider"].startswith(
                    "external:"
                )
                if expanded and batch:
                    break
                batch.append(evidence)
                after = row["seq"]
                if expanded:
                    break
            rows.close()
        if not batch:
            if count != proof["count"]:
                raise RevisionConflict("Path proof is incomplete; start a new query")
            return
        count += len(batch)
        yield batch
