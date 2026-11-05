"""Refresh the C# binding index from durable document and catalog generations."""

from typing import NamedTuple

from services.reference_graph import document_delta
from services.reference_graph.analysis import AnalysisIndex, Facts


class AnalysisCache(NamedTuple):
    rules_revision: int
    catalog_revision: int
    document_generation: int
    index: AnalysisIndex
    blocked: set[str]


def refresh(workspace, cache: AnalysisCache | None, rules: dict, check):
    """Return the candidate cache and the owners needing durable publication.

    The caller discards the candidate and any in-place edits if the enqueue
    fails its fences. Payload reads can therefore use short transactions.
    """
    with workspace.store.read_snapshot():
        blocked = workspace.blocked_analysis()
        changed_inputs = workspace.changed_inputs()
        with workspace.store._connect() as db:
            generation = document_delta.generation(db)
            changed = (
                document_delta.changed_owners(db, cache.document_generation, generation)
                if cache and cache.rules_revision == rules["revision"]
                else None
            )
        revision, catalog = workspace.refresh_catalog(
            (cache.catalog_revision, cache.index.catalog) if cache else None
        )

    def load(owners):
        for row in workspace.documents(owners):
            check()
            if row["format"] == "csharp":
                yield Facts.from_dict(row["data"])

    if changed is None:

        def all_facts():
            after = ""
            while True:
                check()
                with workspace.store._connect() as db:
                    owners = document_delta.csharp_page(db, after, 256)
                if not owners:
                    return
                yield from load([owner for owner in owners if owner not in blocked])
                after = owners[-1]

        index = AnalysisIndex(
            all_facts(),
            catalog,
            [rule["config"] for rule in rules["rules"] if rule["config"].get("enabled", True)],
            check=check,
        )
        # Changed or unavailable declarations can rebind callers that had
        # ambiguous/missing names and therefore no stored dependency edge.
        affected = set(index.facts) if changed_inputs or blocked else set()
    else:
        index = cache.index
        index.catalog = catalog
        candidates = sorted((changed | changed_inputs | (cache.blocked - blocked)) - blocked)
        removed = {owner for owner in blocked - cache.blocked if owner in index.facts}

        facts = []
        for offset in range(0, len(candidates), 256):
            check()
            owners = candidates[offset : offset + 256]
            with workspace.store._connect() as db:
                csharp = document_delta.changed_csharp(db, owners)
            removed.update(
                owner for owner in owners if owner in index.facts and owner not in csharp
            )
            if csharp:
                facts.extend(load(csharp))
        affected = index.update(facts, removed=removed) if facts or removed else set()
        affected.update(index.affected(changed_inputs))
    return AnalysisCache(
        rules["revision"], revision, generation, index, blocked
    ), affected - blocked
