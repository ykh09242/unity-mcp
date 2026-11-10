"""Refresh the C# binding index from durable document and catalog generations."""

from collections.abc import Callable
from dataclasses import dataclass
import io
from itertools import chain, islice
import json
import sys
import threading
from typing import NamedTuple
import weakref

from services.reference_graph import document_delta
from services.reference_graph.analysis import AnalysisIndex, Facts


MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
_EVIDENCE_OVERHEAD = 512
_evidence_lock = threading.Lock()
_retained_evidence = weakref.WeakKeyDictionary()


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class BindingEvidence:
    """Bounded old binding metadata, without retaining facts or the full index."""

    data: bytes

    def affected(
        self, index: AnalysisIndex, changed: set[str], check: Callable[[], None]
    ) -> set[str]:
        symbols = set()
        for owner in changed:
            check()
            if owner in index.facts:
                symbols.update(index._symbols(index.facts[owner]))
        affected = set(changed)
        reverse = {}
        # Symbols precede their users. Decode one record at a time so operator
        # deadlines/cancellation remain observable during reconstruction.
        for line in io.BytesIO(self.data):
            check()
            kind, key, values = json.loads(line)
            if kind == "symbols" and key in changed:
                symbols.update(values)
            elif kind == "users" and key in symbols:
                affected.update(values)
            elif kind == "reverse":
                reverse.setdefault(key, []).extend(values)
        for symbol in symbols:
            check()
            affected.update(index.symbol_users.get(symbol, ()))
        pending = list(affected)
        while pending:
            check()
            dependency = pending.pop()
            for owner in chain(reverse.get(dependency, ()), index.reverse.get(dependency, ())):
                check()
                if owner not in affected:
                    affected.add(owner)
                    pending.append(owner)
        return affected


class AnalysisCache(NamedTuple):
    rules_revision: int
    catalog_revision: int
    document_generation: int
    index: AnalysisIndex | None
    blocked: set[str]
    evidence: BindingEvidence | None = None


def expire(cache: AnalysisCache | None, check: Callable[[], None]) -> AnalysisCache | None:
    """Release a full index while retaining globally bounded invalidation proof.

    Admission is shared by every scope, and weak ownership releases its charge
    on cache clearing/retirement/replacement. An exhausted budget uses the safe
    cold fallback. Serialization happens only on age expiry, never per edit.
    """
    if cache is None or cache.index is None:
        return cache
    with _evidence_lock:
        available = MAX_EVIDENCE_BYTES - sum(_retained_evidence.values())
    # The expired cache also retains its blocked-owner baseline. Charge that
    # ownership as well, conservatively counting shared strings in full.
    overhead = _EVIDENCE_OVERHEAD + sys.getsizeof(cache.blocked)
    for owner in cache.blocked:
        check()
        overhead += sys.getsizeof(owner)
        if overhead > available:
            return None
    data = bytearray()
    index = cache.index
    records = (
        ("symbols", ((owner, index._symbols(item)) for owner, item in index.facts.items())),
        ("users", index.symbol_users.items()),
        ("reverse", index.reverse.items()),
    )
    for kind, rows in records:
        for key, values in rows:
            check()
            values = iter(values)
            while chunk := list(islice(values, 256)):
                check()
                row = json.dumps([kind, key, chunk], separators=(",", ":")).encode() + b"\n"
                if len(data) + len(row) + overhead > available:
                    return None
                data.extend(row)
    evidence = BindingEvidence(bytes(data))
    size = len(evidence.data) + overhead
    with _evidence_lock:
        if sum(_retained_evidence.values()) + size > MAX_EVIDENCE_BYTES:
            return None
        _retained_evidence[evidence] = size
    return cache._replace(index=None, evidence=evidence)


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
            (cache.catalog_revision, cache.index.catalog) if cache and cache.index else None
        )

    def load(owners):
        for row in workspace.documents(owners):
            check()
            if row["format"] == "csharp":
                yield Facts.from_dict(row["data"])

    if changed is None or cache.index is None:

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
        if changed is not None and cache.evidence is not None:
            affected = cache.evidence.affected(
                index, changed | changed_inputs | (cache.blocked ^ blocked), check
            )
        else:
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
