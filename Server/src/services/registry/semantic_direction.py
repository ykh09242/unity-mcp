"""Use competing declarations from the visible catalog to resolve operation direction."""

from collections import defaultdict
from heapq import nlargest
from itertools import islice

import regex

from services.registry import semantic_coverage as coverage, semantic_encoder as encoder
from services.registry.semantic_alignment import _dot


RouteKey = tuple[str, str, str]
_WORDS = regex.compile(r"[\p{L}\p{M}\p{N}]+")
_CAMEL = regex.compile(r"(?<=[\p{Ll}\d])(?=\p{Lu})")
_MAX_SNIPPETS = 48
_MAX_CANDIDATES = 64


def _words(identifier: str) -> tuple[str, ...]:
    return tuple(_WORDS.findall(_CAMEL.sub(" ", identifier).casefold()))


def _snippets(query: str) -> tuple[str, ...]:
    words = _WORDS.findall(query[: encoder._MAX_CHARS])
    phrases = (
        " ".join(words[i : i + size]) for size in (1, 2, 3) for i in range(len(words) - size + 1)
    )
    return tuple(islice(dict.fromkeys(phrases), _MAX_SNIPPETS))


def _matches(views: tuple[str, ...], terms: tuple[str, ...], model: str) -> dict[str, float]:
    """Pool in the same spaces as the retrieval pass. Request vectors live only in this call."""
    if not views or not terms:
        return {}
    spaces = []
    if model in (encoder.MODEL_ID, encoder.COMBINED_MODEL_ID):
        primary = encoder._model()
        spaces.append(
            (
                lambda text: encoder._encode(text, primary),
                0.6 if model == encoder.COMBINED_MODEL_ID else 1.0,
            )
        )
    if model in (coverage.MODEL_ID, encoder.COMBINED_MODEL_ID):
        unigram = coverage._load()
        spaces.append(
            (
                lambda text: coverage._encode(unigram, text),
                0.4 if model == encoder.COMBINED_MODEL_ID else 1.0,
            )
        )
    if not spaces:
        return {}
    queries = [(encode, weight, tuple(map(encode, views))) for encode, weight in spaces]
    matches = {}
    for term in terms:
        scores = [0.0] * len(views)
        for encode, weight, vectors in queries:
            term_vector = encode(term)
            if term_vector:
                for i, vector in enumerate(vectors):
                    if vector:
                        scores[i] += weight * _dot(vector, term_vector)
        matches[term] = max(scores)
    return matches


def _lead(values):
    """Return the unique winner and its margin in a family of two or more actions."""
    first, second = nlargest(2, values, key=values.__getitem__)
    return first, values[first] - values[second]


def _same_subject(subject, label, purpose):
    # A short suffix variation in the registered title is ambiguous by itself.
    # Require the exact candidate term in the incumbent's own prose as well.
    return all(
        word in label
        or (
            len(word) >= 3
            and word in purpose
            and any(part.startswith(word) and len(part) == len(word) + 1 for part in label)
        )
        for word in subject
    )


def _purpose_gain(query, model, family, catalog, winner):
    """Corroborate the prose of an example-free family using its differing action heads."""
    if len(family) > _MAX_CANDIDATES or all(key != winner for key, _ in family):
        return None
    purposes = {key: catalog[key][0].partition(". ")[2] for key, _ in family}
    if not all(purposes.values()):
        return None
    evidence = _matches((query,), tuple(dict.fromkeys(purposes.values())), model)
    if not evidence:
        return None
    candidate, lead = _lead({key: evidence[purpose] for key, purpose in purposes.items()})
    if candidate == winner or lead <= 0:
        return None
    heads = _matches(_snippets(query), tuple(head for _, head in family), model)
    if not heads:
        return None
    head_winner, head_lead = _lead({key: heads[head] for key, head in family})
    return (candidate, min(lead, head_lead)) if candidate == head_winner and head_lead > 0 else None


def contrast_siblings(
    query: str,
    model: str,
    catalog: dict[RouteKey, tuple[str, ...]],
    similarities: dict[str, float],
    scores: dict[RouteKey, float],
    *,
    neighborhood: float,
) -> dict[RouteKey, float]:
    """Reinforce independent wins among actions sharing a dispatcher and a subject."""
    if not scores:
        return scores
    winner = max(scores, key=scores.__getitem__)
    peak = scores[winner]
    label, _, purpose = catalog[winner][0].partition(". ")
    winner_label = set(_words(label))
    purpose_words = _words(purpose)
    winner_head = next(iter(_words(winner[2]) or purpose_words), "")
    groups = defaultdict(list)
    for key in scores:
        parts = _words(key[2])
        if key[0] == "tool" and len(parts) > 1:
            groups[key[1], parts[1:]].append((key, parts[0]))

    gains = {}
    competing = {}
    for (_, subject), family in groups.items():
        if len(family) < 2 or len({head for _, head in family}) != len(family):
            continue
        if any(len(catalog[key]) < 2 for key, _ in family):
            # A runtime dispatcher may publish only a purpose for each action. Within
            # the already winning family, its prose and action heads can still agree.
            if result := _purpose_gain(query, model, family, catalog, winner):
                key, gain = result
                gains[key] = min(gain, neighborhood)
            continue
        declarations = {k: similarities[catalog[k][0]] for k, _ in family}
        candidate, margin = _lead(declarations)
        if margin <= 0 or scores[candidate] < peak - 2 * neighborhood:
            continue
        examples = {k: max(similarities[t] for t in catalog[k][1:]) for k, _ in family}
        example_winner, example_margin = _lead(examples)
        if candidate != example_winner or example_margin <= 0:
            continue
        gain = min(neighborhood, margin, example_margin)
        head = dict(family)[candidate]
        if head == winner_head:
            gains[candidate] = gain
        elif winner_head and _same_subject(subject, winner_label, purpose_words):
            competing[candidate] = (head, gain)
    if competing:
        candidates = nlargest(_MAX_CANDIDATES, competing, key=scores.__getitem__)
        terms = tuple(dict.fromkeys([winner_head, *(competing[k][0] for k in candidates)]))
        evidence = _matches(_snippets(query), terms, model)
        if evidence:
            for k in candidates:
                head, gain = competing[k]
                if evidence[head] > evidence[winner_head]:
                    gains[k] = gain
    if not gains:
        return scores
    adjusted = {k: value + gains.get(k, 0.0) for k, value in scores.items()}
    shift = peak - max(adjusted.values())
    return {k: value + shift for k, value in adjusted.items()}
