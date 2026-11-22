"""Rerank pooled static matches by how well the words in each text cover the other."""

from functools import lru_cache
from heapq import nlargest
import math
from operator import mul

from services.registry import semantic_encoder as encoder


_CANDIDATES = 64
_MAX_WORDS = 48

# Python 3.12 and later implement sumprod in C.
try:
    from math import sumprod as _dot
except ImportError:

    def _dot(left, right):
        return sum(map(mul, left, right))


def _word_vector(offsets, data):
    if not offsets:
        return ()
    values = [0.0] * encoder.DIMENSIONS
    for offset in offsets:
        scale, *codes = encoder._ROW.unpack_from(data, offset)
        for i, code in enumerate(codes):
            values[i] += scale * code
    norm = math.sqrt(_dot(values, values))
    return tuple(value / norm for value in values) if norm else ()


@lru_cache(maxsize=2048)
def _catalog_word(offsets):
    return _word_vector(offsets, encoder._model()[1])


def _word_offsets(text, vocab):
    words = []
    for word in encoder._words(text[: encoder._MAX_CHARS]):
        offsets = tuple(encoder._piece_offsets(word, vocab))
        if offsets:
            words.append(offsets)
            if len(words) == _MAX_WORDS:
                break
    return words


def rerank(
    query: str,
    documents: tuple[str, ...],
    pooled: tuple[float, ...],
    model: tuple[dict[str, int], bytes],
) -> tuple[float, ...]:
    """Keep pooled confidence and rerank only its strongest candidates using word alignment."""
    strongest = max(pooled, default=0.0)
    # A highly similar whole sentence already carries more evidence about the operation.
    alignment_weight = min(0.75, max(0.0, 1.5 * (0.9 - strongest)))
    if strongest <= 0 or alignment_weight == 0:
        return pooled
    vocab, data = model
    query_words = [
        vector for offsets in _word_offsets(query, vocab) if (vector := _word_vector(offsets, data))
    ]
    if not query_words:
        return pooled

    # The same catalog word often occurs in several candidates. This cache lives for one query.
    matches = {}
    candidates = nlargest(_CANDIDATES, range(len(pooled)), key=pooled.__getitem__)
    combined = {}
    for i in candidates:
        best_query = [-1.0] * len(query_words)
        best_document = []
        for offsets in _word_offsets(documents[i], vocab):
            scores = matches.get(offsets)
            if scores is None:
                vector = _catalog_word(offsets)
                scores = tuple(_dot(term, vector) for term in query_words) if vector else ()
                matches[offsets] = scores
            if scores:
                best_document.append(max(scores))
                best_query = list(map(max, best_query, scores))
        if best_document:
            alignment = (
                sum(best_query) / len(best_query) + sum(best_document) / len(best_document)
            ) / 2
            combined[i] = alignment_weight * alignment + (1.0 - alignment_weight) * pooled[i]
        else:
            combined[i] = pooled[i]

    # Keep the same maximum similarity so weak pooled evidence stays weak after reranking.
    shift = strongest - max(combined.values())
    tail_shift = min(0.0, min(combined.values()) + shift - pooled[candidates[-1]])
    return tuple(
        max(-1.0, combined[i] + shift if i in combined else value + tail_shift)
        for i, value in enumerate(pooled)
    )
