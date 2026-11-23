"""Rank visible bindings against their underlying operation declarations."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from services.registry.action_catalog import TOOL_GUIDANCE
from services.registry.capability_catalog import CAPABILITIES
from services.registry.semantic_catalog import RouteKey, search_texts


@dataclass(frozen=True, slots=True)
class Operation:
    owner: str
    selector: str
    purpose: str
    examples: tuple[str, ...]


_WORDS = re.compile(r"[^\W\d_]+")
_CLAUSES = re.compile(r"[.;\n]+|,\s+|\s+(?:and|then)\s+")
_NEIGHBORHOOD = 0.12
_FORWARDERS = {}
for _capability in CAPABILITIES:
    for _selector in _capability.actions:
        _FORWARDERS.setdefault((_capability.source, _selector), []).append(_capability.name)


def _terms(text):
    return _WORDS.findall(text.casefold())


@lru_cache(maxsize=2048)
def _fields(op: Operation):
    purpose = op.purpose.partition(". ")[0]
    document = search_texts(op.owner, op.selector, purpose, examples=op.examples)
    passages = tuple(
        dict.fromkeys(
            clause.strip(" .")
            for text in (op.purpose, *op.examples)
            for clause in _CLAUSES.split(text)
            if len(clause.split()) >= 3
        )
    )
    heads = frozenset(
        tokens[0] for text in (op.selector, purpose, *op.examples) if (tokens := _terms(text))
    )
    subject = frozenset(_terms(op.owner.removeprefix("manage_")))
    return document, passages, heads, subject


def _shared_declarations(op: Operation):
    copies = []
    for name in (*_FORWARDERS.get((op.owner, op.selector), ()), op.owner):
        guide = TOOL_GUIDANCE.get(name)
        if guide and (card := guide.actions.get(op.selector)):
            copies.append((name, card.purpose, tuple(card.search_examples)))
    if len({(purpose, examples) for _, purpose, examples in copies}) < 2:
        return ()
    texts = []
    for name, purpose, examples in copies:
        doc, clauses, _, _ = _fields(Operation(name, op.selector, purpose, examples))
        texts.extend((*doc, *clauses, op.selector.replace("_", " ")))
    return tuple(dict.fromkeys(texts))


def _lexical_owner(words, by_key, leader, rank_catalog, query, model):
    """Resolve an explicitly named subject against its full set of eligible sibling actions."""
    scoped = {key: op for key, op in by_key.items() if _fields(op)[3].intersection(words)}
    if not scoped:
        return None
    direct = {key: op for key, op in scoped.items() if words[0] in _fields(op)[2]}
    if direct:
        scoped = direct
    elif leader in by_key:
        scoped = {key: op for key, op in scoped.items() if op.owner == by_key[leader].owner}
    else:
        return None
    if not scoped:
        return None
    receipt, local = rank_catalog(query, {key: _fields(op)[0] for key, op in scoped.items()})
    if receipt != model or not local:
        return None
    winner = scoped[max(local, key=local.__getitem__)]
    if not direct:
        # Give a distinctive word its full information value within the named subject.
        descriptions = {op: set(_terms(op.purpose.partition(". ")[0])) for op in scoped.values()}
        frequency = Counter(word for terms in descriptions.values() for word in terms)
        evidence = set(words) - _fields(winner)[3]
        overlap = {
            op: sum(math.log(len(descriptions) / frequency[w]) for w in terms & evidence)
            for op, terms in descriptions.items()
        }
        ordered = sorted(overlap, key=overlap.__getitem__, reverse=True)
        if len(ordered) > 1 and overlap[ordered[0]] - overlap[ordered[1]] >= math.log(len(ordered)):
            winner = ordered[0]
    return winner, bool(direct)


def rank_operations(
    query, catalog: dict[RouteKey, tuple[str, ...]], operations, baseline, rank_catalog
):
    model, scores = baseline
    if not scores:
        return baseline
    # Advertised keys alone determine the output. Source declarations contribute
    # text about an existing binding and never add the source dispatcher itself.
    by_key = {key: op for key, op in operations.items() if key in catalog}
    if not by_key:
        return baseline
    leader = max(scores, key=scores.__getitem__)
    words = _terms(query)
    exact = _lexical_owner(words, by_key, leader, rank_catalog, query, model) if words else None
    if exact:
        winner, direct = exact
        increase = _NEIGHBORHOOD if direct else _NEIGHBORHOOD / 3
    else:
        canonical = catalog.copy()
        for key, op in by_key.items():
            if shared := _shared_declarations(op):
                canonical[key] = tuple(dict.fromkeys((*catalog[key], *shared)))
        if canonical == catalog:
            return baseline
        alternate_model, revised = rank_catalog(query, canonical)
        if alternate_model != model or not revised:
            return baseline
        key = max(revised, key=revised.__getitem__)
        if canonical[key] == catalog[key] or key not in by_key:
            return baseline
        winner = by_key[key]
        # An expanded description can disambiguate a subject that the full request
        # already supports. Repeated aliases do not count as independent support.
        leaders = tuple(
            dict.fromkeys(
                by_key.get(k, k) for k in sorted(scores, key=scores.__getitem__, reverse=True)
            )
        )[:3]
        if not any(isinstance(op, Operation) and op.owner == winner.owner for op in leaders):
            return baseline
        rival = max(
            (v for k, v in revised.items() if by_key.get(k) != winner), default=revised[key]
        )
        increase = max(0.001, min(_NEIGHBORHOOD / 3, revised[key] - rival))
        direct = False
    result = scores.copy()
    if direct:
        for key in scores:
            op = by_key.get(key)
            if op and op.owner == winner.owner and op != winner:
                result[key] -= _NEIGHBORHOOD / 2
    bindings = [key for key, op in by_key.items() if op == winner]
    chosen = next((key for key in bindings if key[1] == winner.owner), bindings[0])
    result[chosen] = min(1.0, max(result.values()) + increase)
    return model, result
