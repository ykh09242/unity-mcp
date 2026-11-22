"""Bounded late interaction over the same embedded token vectors used for retrieval."""

from collections.abc import Callable
from functools import lru_cache
from heapq import nlargest
from itertools import islice
import math
from operator import mul

from services.registry import semantic_encoder as encoder
from services.registry import semantic_coverage as coverage


_CANDIDATES = 64
_MAX_WORDS = 48
_NEIGHBORHOOD = 0.12

try:
    from math import sumprod as _dot
except ImportError:

    def _dot(left, right):
        return sum(map(mul, left, right))


def _unit(values):
    norm = math.sqrt(_dot(values, values))
    return (tuple(value / norm for value in values), norm) if norm else ((), 0.0)


@lru_cache(maxsize=2048)
def _word_vector(offsets):
    data = encoder._model()[1]
    values = [0.0] * encoder.DIMENSIONS
    for offset in offsets:
        scale, *codes = encoder._ROW.unpack_from(data, offset)
        for i, code in enumerate(codes):
            values[i] += scale * code
    vector, _ = _unit(values)
    return vector, 1.0


@lru_cache(maxsize=2048)
def _unigram_vector(index):
    # The norm of a learned Unigram row is its contribution to the original pooled query.
    return _unit(coverage._token_vector(coverage._load(), index))


def _word_offsets(text):
    vocab, _ = encoder._model()
    return tuple(
        islice(
            (
                tuple(ids)
                for word in encoder._words(text)
                if (ids := encoder._piece_offsets(word, vocab))
            ),
            _MAX_WORDS,
        )
    )


def _unigram_ids(text):
    return tuple(islice(coverage._load().tokenize(text), _MAX_WORDS))


_document_words = lru_cache(maxsize=2048)(_word_offsets)
_document_ids = lru_cache(maxsize=2048)(_unigram_ids)


def _align(query_terms, documents, candidates, document_terms: Callable, encode: Callable):
    query = [(vector, weight) for vector, weight in map(encode, query_terms) if vector]
    if not query:
        return {}
    query_weight = sum(weight for _, weight in query)
    matches = {}
    aligned = {}
    for i in candidates:
        best_query = [-1.0] * len(query)
        document_sum = document_weight = 0.0
        for term in document_terms(documents[i][: encoder._MAX_CHARS]):
            if term not in matches:
                vector, weight = encode(term)
                scores = tuple(_dot(q, vector) for q, _ in query) if vector else ()
                matches[term] = scores, weight
            scores, weight = matches[term]
            if scores:
                document_sum += weight * max(scores)
                document_weight += weight
                best_query = list(map(max, best_query, scores))
        if document_weight:
            query_sum = sum(best * w for best, (_, w) in zip(best_query, query))
            aligned[i] = (query_sum / query_weight + document_sum / document_weight) / 2
    return aligned


def _corroborate(combined, candidates, pooled, word):
    """Use independently aligned WordPiece scores to resolve close joint candidates."""
    weight = min(0.75, max(0.0, 1.5 * (0.9 - max(pooled))))
    aligned = {
        i: (1 - weight) * pooled[i] + weight * word.get(i, pooled[i])
        for i in set(combined).union(candidates)
    }
    winner = max(combined, key=combined.__getitem__)
    # A shared topic can match several passages. Independent support for the same
    # winner widens its lead over an alternative that matches the words less well.
    for i in combined:
        combined[i] -= max(0.0, aligned[winner] - aligned[i])

    alternative = max(candidates, key=aligned.__getitem__)
    first, second = nlargest(2, combined.values())
    if alternative not in combined or first - combined[alternative] > _NEIGHBORHOOD:
        return
    advantage = aligned[alternative] - aligned[winner]
    if advantage > max(_NEIGHBORHOOD / 2, 2 * (first - second)):
        combined[alternative] = first + advantage
    elif combined[alternative] < second:
        combined[alternative] = (first + second) / 2


def _single_model_scores(pooled, aligned):
    """Retain sentence-level confidence when only Unigram can represent the query."""
    winner = max(range(len(pooled)), key=pooled.__getitem__)
    peak = pooled[winner]
    agreement = winner == max(range(len(aligned)), key=aligned.__getitem__)
    adjustment = (1 - peak) ** 2
    scores = [
        sentence + adjustment * (local - sentence) - (peak - sentence if agreement else 0.0)
        for sentence, local in zip(pooled, aligned, strict=True)
    ]
    shift = peak - max(scores)
    return tuple(max(-1.0, score + shift) for score in scores)


def rerank(
    query: str,
    documents: tuple[str, ...],
    pooled: tuple[float, ...],
    model: tuple[dict[str, int], bytes] | None,
    *,
    unigram_weight: float = 0.0,
    primary: tuple[float, ...] = (),
) -> tuple[float, ...]:
    """Align the joint pooled shortlist in each participating model's own vector space."""
    strongest = max(pooled, default=0.0)
    alignment_weight = min(0.65, max(0.0, 1.5 * (0.9 - strongest)))
    if strongest <= 0 or alignment_weight == 0:
        return pooled
    candidates = nlargest(_CANDIDATES, range(len(pooled)), key=pooled.__getitem__)
    primary_candidates = nlargest(_CANDIDATES, range(len(primary)), key=primary.__getitem__)
    query = query[: encoder._MAX_CHARS]
    word = (
        _align(
            _word_offsets(query),
            documents,
            dict.fromkeys(candidates + primary_candidates),
            _document_words,
            _word_vector,
        )
        if model
        else {}
    )
    unigram = (
        _align(_unigram_ids(query), documents, candidates, _document_ids, _unigram_vector)
        if unigram_weight
        else {}
    )
    if not word and not unigram:
        return pooled
    combined = {}
    for i in candidates:
        aligned = (1 - unigram_weight) * word.get(i, pooled[i]) + unigram_weight * unigram.get(
            i, pooled[i]
        )
        combined[i] = alignment_weight * aligned + (1 - alignment_weight) * pooled[i]

    if primary_candidates and len(combined) >= 2:
        _corroborate(combined, primary_candidates, primary, word)
    # Absolute WordPiece cosine anchors the existing catalog confidence thresholds.
    # Unigram-only calls retain the observed Unigram cosine instead.
    shift = max(primary, default=strongest) - max(combined.values())
    tail_shift = min(0.0, min(combined.values()) + shift - pooled[candidates[-1]])
    aligned = tuple(
        max(-1.0, combined[i] + shift if i in combined else value + tail_shift)
        for i, value in enumerate(pooled)
    )
    return aligned if model else _single_model_scores(pooled, aligned)
