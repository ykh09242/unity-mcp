"""Distinguish one requested state from an inventory among visible sibling observations."""

import hashlib
import struct
from functools import lru_cache
from pathlib import Path

import regex

from services.registry import semantic_metric as metric
from services.registry.semantic_alignment import _dot
from services.registry.semantic_catalog import _NEIGHBORHOOD, _has_multiple_clauses, _label
from services.registry.semantic_direction import _matches, _snippets
from services.registry.semantic_operations import _effect_terms, _fields, _terms

_ARTIFACT = Path(__file__).with_name("model") / "observation-scope-v1.q8"
_SHA256 = "88fd6c7d7fb040b7db79224041b98f2533feec054d80654d07594f9b807b9d70"
_HEADER = b"UMRSCP01"
_AXIS = struct.Struct("<f256bf")
# A period inside an identifier is part of the subject of the request.
_BREAK = regex.compile(r"(?<!\w)\.+|\.+(?!\w)|[\p{Terminal_Punctuation}\n--[.]]+", regex.V1)


@lru_cache(maxsize=1)
def _weights():
    size = len(_HEADER) + 4 * _AXIS.size
    try:
        with _ARTIFACT.open("rb") as stream:
            data = stream.read(size + 1)
    except OSError:
        return ()
    if (
        len(data) != size
        or not data.startswith(_HEADER)
        or hashlib.sha256(data).hexdigest() != _SHA256
    ):
        return ()
    return tuple(
        (tuple(scale * code for code in codes), offset)
        for scale, *codes, offset in _AXIS.iter_unpack(data[len(_HEADER) :])
    )


def _clauses(text):
    return tuple(part.strip() for part in _BREAK.split(text) if any(c.isalnum() for c in part))


def _score(vector, weights):
    axis, bias = weights
    return _dot(axis, vector) + bias if vector else 0.0


@lru_cache(maxsize=2048)
def _declaration(op, index):
    # The leading predicate identifies the answer. A subsequent list of fields
    # can describe attributes of one record as well as columns of an inventory.
    label = _label(op.selector)
    purpose = op.purpose.partition(". ")[0]
    parts = _clauses(purpose)
    predicate = metric._encode(parts[0] if parts else label, index, document=True)
    heading = metric._encode(
        f"{_label(op.owner.removeprefix('manage_'))} {label}", index, document=True
    )
    return predicate, heading


def _shared_subject(detail, inventory):
    """Find the selector subject in the inventory's declared data, or a pair of unqualified reads."""
    targets, _ = _effect_terms(detail)
    selector = [word for word in _terms(_label(detail.selector)) if word in targets]
    inventory_targets, declared = _effect_terms(inventory)
    if selector:
        return (
            tuple(word for word in selector if word in declared)
            if selector[0] in declared
            else None
        )
    # Bare selectors carry no named property. Their own purposes must share
    # context, and the request must independently prefer the proposed purpose.
    shared = _effect_terms(detail)[1] & declared
    return () if not inventory_targets and shared else None


def _request_scope(query, model, weights):
    direction = metric._weights()
    if direction is None:
        return None
    vectors = tuple(metric._query_vectors(query, model))
    if not vectors or sum(fr * _score(v, direction.effects[i]) for i, fr, v in vectors) >= 0:
        return None
    # Preserve sentence boundaries before masking named arguments. The final
    # clause may specify one result within the collection mentioned earlier.
    raw, _ = metric.query_views(query)
    parts = _clauses(metric._QUOTED.sub(" ", raw))
    signs = []
    focused = []
    combined = 0.0
    for i, fraction, v in vectors:
        score = _score(v, weights[2 * i])
        focus = metric._encode(parts[-1], i, document=False) if len(parts) > 1 else ()
        if focus:
            score = (score + _score(focus, weights[2 * i])) / 2
        focused.append((i, fraction, focus or v))
        signs.append(score)
        combined += fraction * score
    # A field to annotate can look like a request for one item. Overriding the
    # inventory requires independent support from the combined request scores.
    if max(signs) == 0 or (
        min(signs) <= 0 < max(signs) and (combined <= 0 or _has_multiple_clauses(raw))
    ):
        return None
    return parts, focused, signs


def _evidence(op, focused, weights, sign):
    extents = []
    topic = 0.0
    for i, fraction, vector in focused:
        predicate, heading = _declaration(op, i)
        extents.append(sign * _score(predicate, weights[2 * i + 1]))
        topic += fraction * sum(_dot(vector, field) for field in (predicate, heading) if field) / 2
    return extents, topic


def _links(candidate, opposite, sign):
    anchors, contracts = {}, set()
    for peer in opposite:
        detail, inventory = (candidate, peer) if sign > 0 else (peer, candidate)
        subject = _shared_subject(detail, inventory)
        if subject is not None:
            anchors[peer] = subject
            if set(inventory.required) < set(detail.required):
                contracts.add(peer)
    return anchors, contracts


def _rank(query, catalog, operations, effects, ranked):
    model, scores = ranked
    primary = max(scores, key=scores.__getitem__)
    incumbent = operations.get(primary)
    if not incumbent:
        return ranked
    # Collapse forwarding bindings before comparing operations. Only advertised
    # keys with the requested intent and surface can supply a result.
    peers = {}
    for key, op in operations.items():
        if key in catalog and op.owner == incumbent.owner and effects.get(key) == "observe":
            if op not in peers or key[1] == op.owner:
                peers[op] = key
    words = _terms(query)
    _, _, heads, owner_words = _fields(incumbent)
    if len(peers) < 2 or (words and words[0] in heads and owner_words.intersection(words)):
        return ranked
    weights = _weights()
    if not weights or not (request := _request_scope(query, model, weights)):
        return ranked
    parts, focused, signs = request
    sign = 1 if max(signs) > 0 else -1
    disputed = min(signs) <= 0 < max(signs)
    observations = {op: _evidence(op, focused, weights, sign) for op in peers}
    matches = {op: topic for op, (extents, topic) in observations.items() if min(extents) > 0}
    opposite = [op for op, (extents, _) in observations.items() if max(extents) < 0]
    if not matches or not opposite:
        return ranked
    ordered = sorted(matches, key=matches.__getitem__, reverse=True)
    best = ordered[0]
    anchors, contracts = _links(best, opposite, sign)
    if not anchors:
        # A detail operation may need an identifier in addition to all the
        # inventory parameters. That declared relationship can recover a pair
        # even when a generic status check has a higher semantic similarity.
        for candidate in ordered[1:]:
            if matches[candidate] < matches[best] - _NEIGHBORHOOD:
                break
            alternatives, identified = _links(candidate, opposite, sign)
            if identified:
                best, anchors, contracts = candidate, alternatives, identified
                break
    if not anchors or best == incumbent:
        return ranked
    # Singular/plural wording alone is ambiguous. Require the detail actually
    # requested, or independent topical support from the question's own clause.
    anchor = (
        incumbent
        if incumbent in anchors
        else max(contracts or anchors, key=lambda op: observations[op][1])
    )
    subject = anchors[anchor]
    if not subject and matches[best] <= observations[anchor][1]:
        return ranked
    if disputed and (not subject or incumbent != anchor):
        return ranked
    if subject and sign > 0:
        field = _matches(_snippets(parts[-1] if parts else query), subject, model)
        strength = max(field.values(), default=0.0)
        if disputed and strength < max(_NEIGHBORHOOD, -min(signs)):
            return ranked
        clarified = len(parts) > 1 and matches[best] > observations[anchor][1]
        if strength < max(_NEIGHBORHOOD, observations[anchor][1]) and not clarified:
            return ranked
    key = peers[best]
    peak = scores[primary]
    if incumbent in anchors:
        # A third read may concern a different subject in the same family. A
        # scope comparison cannot override its stronger match to the request.
        pair_topic = max(matches[best], observations[incumbent][1])
        for key_in_scores in scores:
            peer = operations.get(key_in_scores)
            if peer in observations and peer not in anchors and observations[peer][1] > pair_topic:
                return ranked
        weight = min(1.0, peak + _NEIGHBORHOOD)
    else:
        # An independently retrieved family can offer the requested scope as
        # an alternative. Preserve a stronger result with a different subject.
        leading = tuple(
            dict.fromkeys(
                operations.get(k, k) for k in sorted(scores, key=scores.__getitem__, reverse=True)
            )
        )[:3]
        if not contracts and not any(op in anchors or op == best for op in leading):
            return ranked
        allowance = _NEIGHBORHOOD if contracts else 0.0
        if matches[best] < max(topic for _, topic in observations.values()) - allowance:
            return ranked
        runner_up = max((s for s in scores.values() if s < peak), default=peak - 0.002)
        weight = (peak + runner_up) / 2
    return model, {**scores, key: max(weight, scores.get(key, 0.0))}


def rank_observations(query, catalog, operations, effects, ranked):
    if not ranked[1]:
        return ranked
    try:
        return _rank(query, catalog, operations, effects, ranked)
    except metric.encoder.SemanticModelUnavailable:
        return ranked
