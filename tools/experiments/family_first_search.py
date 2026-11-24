"""Retrieve visible families, then compare every declared operation of those families."""

from collections import defaultdict
from dataclasses import dataclass
from heapq import nlargest
from statistics import fmean
import unicodedata

from services.registry import semantic_encoder as encoder
from services.registry.semantic_alignment import _dot
from services.registry.semantic_catalog import RouteKey, _label
from tools.experiments import family_first_metric as metric

_FAMILIES = 8
_MIN_SIMILARITY = 0.16


@dataclass(frozen=True, slots=True)
class Card:
    family: str | RouteKey
    selector: str
    heading: str
    purpose: str
    contract: str
    effect: str | None
    required: tuple[str, ...]
    examples: tuple[str, ...]
    bindings: tuple[RouteKey, ...]


def _cards(catalog, operations, effects):
    groups = defaultdict(list)
    for key in sorted(catalog):
        groups[operations.get(key, key), effects.get(key)].append(key)
    for (_, effect), keys in groups.items():
        op = operations.get(keys[0])
        if op:
            keys.sort(key=lambda k, owner=op.owner: (k[1] != owner, k))
        key = keys[0]
        texts = tuple(dict.fromkeys(t for k in keys for t in catalog[k]))
        heading, _, description = catalog[key][0].partition(". ")
        purpose = op.purpose if op and op.purpose else description or heading
        if op:
            heading = f"{_label(op.owner.removeprefix('manage_'))} {_label(op.selector)}"
        contract = [heading, purpose]
        if effect:
            contract.append(f"Effect: {effect}.")
        if op and op.required:
            contract.append("Required inputs: " + ", ".join(_label(arg) for arg in op.required))
        contract.extend(
            dict.fromkeys(t.partition(". Returns ")[2] for t in texts if ". Returns " in t)
        )
        examples = tuple(dict.fromkeys((*(op.examples if op else ()), *texts)))
        yield Card(
            op.owner if op else key,
            op.selector if op else key[2],
            heading,
            purpose,
            " ".join(contract),
            effect,
            op.required if op else (),
            examples,
            tuple(keys),
        )


def _select_families(vectors, families):
    """Pool the strongest distinct members so that forwarding aliases cannot add votes."""
    headings = {
        family: _label(family.removeprefix("manage_"))
        if isinstance(family, str)
        else members[0].heading
        for family, members in families.items()
    }
    scores = {}
    for family, members in families.items():
        score = 0.0
        for index, fraction, query, projected in vectors:
            title = metric.encode(headings[family], index, document=True)
            match = _dot(query, title) if title else 0.0
            strongest = nlargest(3, (_affinity(card, index, query, projected) for card in members))
            score += fraction * (2 * strongest[0] + max(match, fmean(strongest))) / 3
        if score >= _MIN_SIMILARITY:
            scores[family] = score
    return {family: scores[family] for family in nlargest(_FAMILIES, scores, key=scores.get)}


def _affinity(card, index, query, projected):
    document, _ = metric.declaration(card, index)
    if not document:
        return 0.0
    return _dot(query, document) + _dot(projected, metric.projected_declaration(card, index)) / 12


def _compare_operations(vectors, cards):
    scores = {}
    for card in cards:
        score = 0.0
        for index, fraction, query, projected in vectors:
            _, fields = metric.declaration(card, index)
            field = max((_dot(query, v) for v in fields), default=0.0)
            score += fraction * (3 * _affinity(card, index, query, projected) + field) / 4
        scores[card] = score
    return scores


def rank_families(query, catalog, operations, effects):
    """Return ordinary semantic scores for the caller's advertised declarations only."""
    query = unicodedata.normalize("NFKC", query)
    families = defaultdict(list)
    for card in _cards(catalog, operations, effects):
        families[card.family].append(card)
    model, raw = metric.query_vectors(query)
    vectors = tuple((i, f, v, metric.project(v, metric.weights()[2 * i])) for i, f, v in raw)
    selected = _select_families(vectors, families) if vectors else {}
    if selected and max(selected.values()) < 0.4 and encoder.fragmented_label(query, model):
        return model, {}
    if not selected:
        return model, {}
    candidates = tuple(card for owner in selected for card in families[owner])
    similarities = _compare_operations(vectors, candidates)
    result = {}
    for card, similarity in similarities.items():
        # One semantic vote per underlying operation; prefer its advertised source binding.
        score = (3 * similarity + selected[card.family]) / 4
        if score >= _MIN_SIMILARITY:
            result[card.bindings[0]] = min(1.0, score)
    return model, result
