"""Recover a declared state or inventory beyond the first retrieved operation family."""

from collections import defaultdict
from functools import lru_cache
from heapq import nlargest
from statistics import fmean

from services.registry import semantic_metric as metric, semantic_read_scope as scope
from services.registry.semantic_alignment import _dot
from services.registry.semantic_catalog import SEMANTIC_EVIDENCE_FLOOR, _NEIGHBORHOOD
from services.registry.semantic_direction import _matches, _snippets, _words


def _visible_bindings(catalog, operations):
    bindings = {}
    for key, op in operations.items():
        if key in catalog and (op not in bindings or key[1] == op.owner):
            bindings[op] = key
    return bindings


def _matching_subject(query, ranked, operations, effects, bindings):
    """Resolve competing reads with the same action and different named subjects."""
    model, scores = ranked
    if not scores:
        return ranked
    lead = max(scores, key=scores.__getitem__)
    incumbent = operations.get(lead)
    if effects.get(lead) != "observe" or not incumbent:
        return ranked
    title = _words(incumbent.selector)
    if len(title) < 2:
        return ranked
    titles = {op: _words(op.selector) for op in bindings if op.owner == incumbent.owner}
    siblings = {
        op
        for op, words in titles.items()
        if len(words) > 1 and words[0] == title[0] and effects.get(bindings[op]) == "observe"
    }
    if len(siblings) < 2:
        return ranked
    terms = tuple(dict.fromkeys(word for words in titles.values() for word in words))
    matches = _matches(_snippets(query), terms, model)
    if matches.get(title[0], 0.0) < SEMANTIC_EVIDENCE_FLOOR + _NEIGHBORHOOD:
        return ranked
    strength = {op: min((matches[w] for w in words), default=0.0) for op, words in titles.items()}
    winner = max(siblings, key=strength.__getitem__)
    if winner == incumbent or strength[winner] < SEMANTIC_EVIDENCE_FLOOR:
        return ranked
    # Every term of the winner must beat alternative subjects and competing
    # writes. A match to the shared action alone cannot decide the result.
    competition = max(
        strength[op]
        for op in titles
        if op != winner and (op in siblings or effects.get(bindings[op]) != "observe")
    )
    if strength[winner] - competition < _NEIGHBORHOOD:
        return ranked
    return model, {**scores, bindings[winner]: min(1.0, scores[lead] + _NEIGHBORHOOD)}


@lru_cache(maxsize=512)
def _family_center(members, index):
    # Its dot product is the mean similarity of the visible, distinct operations.
    predicates = [scope._declaration(op, index)[0] for op in members]
    return tuple(sum(column) / len(members) for column in zip(*(p for p in predicates if p)))


@lru_cache(maxsize=512)
def _read_pairs(reads, indices):
    weights = scope._weights()
    details, inventories = [], []
    for op in reads:
        extents = [scope._score(scope._declaration(op, i)[0], weights[2 * i + 1]) for i in indices]
        if min(extents) > 0:
            details.append(op)
        elif max(extents) < 0:
            inventories.append(op)
    pairs = []
    for detail in details:
        for inventory in inventories:
            subject = scope._shared_subject(detail, inventory)
            if subject is None:
                continue
            # An identifier can establish the narrower read. Peers with the
            # same inputs must agree on the entire property they describe.
            detail_args, list_args = set(detail.required), set(inventory.required)
            if list_args < detail_args or (
                list_args == detail_args
                and subject
                and set(subject) == scope._effect_terms(detail)[0]
            ):
                pairs.append((detail, inventory))
    return tuple(pairs)


def _best_family(families, similarity, predicate):
    # The center compares all families cheaply. A few matching declarations
    # must corroborate it; forwarding aliases contribute only one vote.
    near = nlargest(
        3,
        families,
        key=lambda owner: similarity(lambda i: _family_center(tuple(families[owner]), i)),
    )
    votes = {owner: fmean(nlargest(3, map(predicate, families[owner]))) for owner in near}
    owner = max(votes, key=votes.__getitem__)
    support = votes.pop(owner)
    return owner, support, support - max(votes.values(), default=0.0)


def _preferred_pair(pairs, vectors, weights, similarity, predicate):
    """Blend the extent requested in the query with both declarations' relevance."""
    evidence = {
        op: (predicate(op) + similarity(lambda i: scope._declaration(op, i)[1])) / 2
        for op in dict.fromkeys(op for pair in pairs for op in pair)
    }

    def pair_score(pair):
        detail, inventory = pair
        affinity = sum(
            f * _dot(scope._declaration(detail, i)[0], scope._declaration(inventory, i)[0])
            for i, f, _ in vectors
        )
        return evidence[detail] + evidence[inventory] + affinity / 2

    detail, inventory = max(pairs, key=pair_score)
    extents = [scope._score(q, weights[2 * i]) for i, _, q in vectors]
    extent = sum(f * x for (_, f, _), x in zip(vectors, extents, strict=True))
    delta = evidence[detail] - evidence[inventory] + _NEIGHBORHOOD * extent
    winner = detail if delta >= 0 else inventory
    agreement = all(x * delta > 0 for x in extents)
    return detail, inventory, winner, abs(delta), agreement


def _recall(query, catalog, operations, effects, ranked, bindings):
    model, scores = ranked
    weights, direction = scope._weights(), metric._weights()
    if not bindings or not weights or direction is None:
        return ranked
    vectors = tuple(metric._query_vectors(query, model))
    if not vectors or (not scores and metric.encoder.fragmented_label(query, model)):
        return ranked
    read_votes = [scope._score(q, direction.effects[i]) for i, _, q in vectors]
    if sum(f * v for (_, f, _), v in zip(vectors, read_votes, strict=True)) >= 0:
        return ranked
    # A split read/edit vote can offer an alternative only when the retriever
    # already points to observations. Forwarding aliases count as one operation.
    leaders = {}
    for key in sorted(scores, key=scores.__getitem__, reverse=True):
        leaders.setdefault(operations.get(key, key), key)
        if len(leaders) == 3:
            break
    leading = tuple(leaders.values())
    read_agreement = max(read_votes) < 0
    if not read_agreement and (not leading or any(effects.get(k) != "observe" for k in leading)):
        return ranked
    families = defaultdict(list)
    for op in bindings:
        families[op.owner].append(op)

    def similarity(encode):
        return sum(f * _dot(q, v) for i, f, q in vectors if (v := encode(i)))

    def predicate(op):
        return similarity(lambda i: scope._declaration(op, i)[0])

    owner, support, family_margin = _best_family(families, similarity, predicate)
    if support < _NEIGHBORHOOD:
        return ranked
    indices = tuple(i for i, _, _ in vectors)

    def pairs_for(family):
        reads = tuple(o for o in families[family] if effects.get(bindings[o]) == "observe")
        return _read_pairs(reads, indices)

    pairs = pairs_for(owner)
    if not pairs:
        return ranked
    detail, inventory, winner, margin, agreement = _preferred_pair(
        pairs, vectors, weights, similarity, predicate
    )
    topic = predicate(winner)
    # A family can expose another kind of inventory or a job-status read.
    # A clearly better predicate for that subject invalidates this pair.
    pair_topic = max(predicate(detail), predicate(inventory))
    if any(
        predicate(op) > pair_topic + _NEIGHBORHOOD
        for op in families[owner]
        if op not in (detail, inventory) and effects.get(bindings[op]) == "observe"
    ):
        return ranked
    incumbent_topic = 0.0
    seen = protected = False
    preserve_first = False
    if leading:
        incumbent = leading[0]
        leader = operations.get(incumbent)
        preserve_first = (
            effects.get(incumbent) == "observe" and scores[incumbent] >= SEMANTIC_EVIDENCE_FLOOR
        )
        if leader in (detail, inventory):
            return ranked
        seen = any(operations[k].owner == owner for k in leading if k in operations)
        if leader:
            words = scope._terms(query)
            _, _, heads, subjects = scope._fields(leader)
            if words and words[0] in heads and subjects.intersection(words):
                return ranked
            incumbent_topic = predicate(leader)
            if effects.get(incumbent) == "observe":
                protected = any(leader in pair for pair in pairs_for(leader.owner))
        else:
            purpose = metric.document_fields(incumbent, catalog[incumbent], None)[1]
            incumbent_topic = similarity(lambda i: metric._encode(purpose, i, document=True))
        if not seen and incumbent_topic > topic + _NEIGHBORHOOD / 3:
            return ranked

    if not agreement and (
        protected or margin < _NEIGHBORHOOD / 3 or incumbent_topic > topic + _NEIGHBORHOOD
    ):
        return ranked
    if (
        not agreement
        and winner == inventory
        and set(detail.required) > set(inventory.required)
        and metric._QUOTED.search(query)
    ):
        return ranked
    if not leading:
        return model, {bindings[winner]: support}
    peak = scores[leading[0]]
    if (
        agreement
        and not protected
        and not preserve_first
        and read_agreement
        and min(family_margin, margin) >= _NEIGHBORHOOD / 6
        and (seen or topic > incumbent_topic + _NEIGHBORHOOD)
    ):
        score = min(1.0, peak + _NEIGHBORHOOD)
    else:
        runner = max((v for v in scores.values() if v < peak), default=peak - 0.002)
        score = (peak + runner) / 2
    key = bindings[winner]
    return model, {**scores, key: max(score, scores.get(key, 0.0))}


def rank_observations(query, catalog, operations, effects, ranked):
    try:
        bindings = _visible_bindings(catalog, operations)
        matched = _matching_subject(query, ranked, operations, effects, bindings)
        if matched is not ranked:
            return matched
        local = scope.rank_observations(query, catalog, operations, effects, ranked)
        if local is not ranked:
            return local
        return _recall(query, catalog, operations, effects, ranked, bindings)
    except metric.encoder.SemanticModelUnavailable:
        return ranked
