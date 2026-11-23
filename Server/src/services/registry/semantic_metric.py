"""Recalibrate multilingual retrieval with a shared low-rank query/document metric."""

import hashlib
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path

import regex
from services.registry import semantic_coverage as coverage
from services.registry import semantic_encoder as encoder
from services.registry.semantic_alignment import _dot, _unit
from services.registry.semantic_catalog import _NEIGHBORHOOD, _label, _request_context
from services.registry.semantic_operations import Operation, _fields, _terms

_ARTIFACT = Path(__file__).with_name("model") / "operation-metric-v1.q8"
_SHA256 = "f7e5cf1573b141f2bd907a85ea7d1990ec42ddb73ac44530b42860cd2e75ec56"
_HEADER = b"UMOPMET1"
_RANK = 12
_ROW = struct.Struct("<f256b")
_SIZE = len(_HEADER) + 4 * _RANK * _ROW.size
_QUOTED = regex.compile(
    r"`[^`\n]+`|\p{Quotation_Mark}[^\n\p{Quotation_Mark}]+\p{Quotation_Mark}|\b\w+_\d+\b"
)


@lru_cache(maxsize=1)
def _weights():
    try:
        with _ARTIFACT.open("rb") as stream:
            data = stream.read(_SIZE + 1)
    except OSError:
        return None
    if (
        len(data) != _SIZE
        or not data.startswith(_HEADER)
        or hashlib.sha256(data).hexdigest() != _SHA256
    ):
        return None
    rows = tuple(
        tuple(scale * code for code in codes)
        for scale, *codes in _ROW.iter_unpack(data[len(_HEADER) :])
    )
    return tuple(rows[i : i + _RANK] for i in range(0, len(rows), _RANK))


def _weighted_unit(vectors, weights):
    vectors = [(v, weight) for v, weight in zip(vectors, weights, strict=True) if v]
    if not vectors:
        return ()
    if len(vectors) == 1:
        return vectors[0][0]
    total = [0.0] * encoder.DIMENSIONS
    for vector, weight in vectors:
        for i, value in enumerate(vector):
            total[i] += value * weight
    return _unit(total)[0]


def document_fields(key, texts, operation: Operation | None):
    heading, _, local = texts[0].partition(". ")
    owner = (
        _label((operation.owner if operation else key[1]).removeprefix("manage_"))
        if key[0] == "tool"
        else heading
    )
    label = _label(operation.selector if operation else key[2]) or heading
    source_purpose = operation.purpose.partition(". ")[0].strip(". ") if operation else ""
    purpose = source_purpose or local.partition(". ")[0] or label
    examples = (operation.examples if operation else ()) or texts[1:] or (purpose,)
    return label, purpose, tuple(examples[:8]), label.partition(" ")[2] or owner


def query_views(query):
    raw = unicodedata.normalize("NFKC", query)
    context = _QUOTED.sub(" ", _request_context(raw, compact=True)).strip() or raw
    return raw, context


def _encode(text, index, *, document):
    if index == 0:
        return (
            encoder._document_vector(text) if document else encoder._encode(text, encoder._model())
        )
    model = coverage._load()
    return coverage._document_vector(model, text) if document else coverage._encode(model, text)


def _project(vector, weights):
    return tuple(_dot(vector, row) for row in weights) if vector else ()


@lru_cache(maxsize=encoder._MAX_CACHED_DOCUMENTS)
def _document(fields, index):
    weights = _weights()
    label, purpose, examples, tail = fields
    passages = [
        _weighted_unit([_encode(t, index, document=True) for t in field], [1] * len(field))
        for field in ((label,), (purpose,), examples, (tail,))
    ]
    vector = _weighted_unit(passages, (0.20, 0.30, 0.35, 0.15))
    return vector, _project(vector, weights[2 * index + 1])


def _adapted_scores(query, catalog, operations, base):
    model, previous = base
    weights = _weights()
    if weights is None:
        return None
    if model == encoder.COMBINED_MODEL_ID:
        encoders = ((0, 0.6), (1, 0.4))
    elif model == encoder.MODEL_ID:
        encoders = ((0, 1.0),)
    elif model == coverage.MODEL_ID:
        encoders = ((1, 1.0),)
    else:
        return None
    raw, context = query_views(query)
    scores = {key: 0.22 * previous.get(key, 0.0) for key in catalog}
    supported = set()
    for index, fraction in encoders:
        q = _encode(raw, index, document=False)
        if context != raw:
            q = _weighted_unit((q, _encode(context, index, document=False)), (1, 1))
        if not q:
            continue
        projected = _project(q, weights[2 * index])
        direct = {}
        for key, texts in catalog.items():
            fields = document_fields(key, texts, operations.get(key))
            vector, low_rank = _document(fields, index)
            similarity = _dot(q, vector) if vector else 0.0
            direct[key] = similarity + 0.22 * previous.get(key, 0.0)
            scores[key] += fraction * (
                similarity + (0.12 * _dot(projected, low_rank) if vector else 0.0)
            )
        # Require an independent vote from the ordinary field geometry. Aliases
        # of a single operation cannot consume multiple places in this vote.
        distinct = dict.fromkeys(
            operations.get(k, k) for k in sorted(direct, key=direct.__getitem__, reverse=True)
        )
        supported.update(tuple(distinct)[:3])
    return scores, supported


def recall_operation(query, catalog, operations, baseline, canonical):
    """Offer one alternative from the learned metric within the original visible catalog."""
    model, scores = canonical
    if not scores or not baseline[1]:
        return canonical
    primary = max(scores, key=scores.__getitem__)
    words = _terms(query)
    if primary in operations and _fields(operations[primary])[3].intersection(words):
        return canonical
    try:
        adapted = _adapted_scores(query, catalog, operations, baseline)
    except encoder.SemanticModelUnavailable:
        return canonical
    if not adapted:
        return canonical
    revised, supported = adapted
    winner = max(revised, key=revised.__getitem__)
    if (
        operations.get(winner, winner) not in supported
        and revised[winner] - revised[primary] <= _NEIGHBORHOOD
    ):
        return canonical
    operation = operations.get(winner)

    def same(key):
        return key == winner or (operation is not None and operations.get(key) == operation)

    if same(primary):
        return canonical
    if operation:
        siblings = (
            key
            for key in scores
            if (peer := operations.get(key))
            and peer.owner == operation.owner
            and peer.selector != operation.selector
        )
        sibling = max(siblings, key=scores.__getitem__, default=None)
        if sibling is not None:
            # A missing score was below the retained catalog neighborhood. Its
            # weakest retained score gives a lower bound on the sibling's lead.
            previous = max((scores[k] for k in scores if same(k)), default=min(scores.values()))
            if revised[winner] - revised[sibling] <= scores[sibling] - previous:
                return canonical
    distinct = sorted(set(scores.values()), reverse=True)
    alternative = (distinct[0] + distinct[1]) / 2 if len(distinct) > 1 else distinct[0] - 0.001
    result = scores.copy()
    candidates = [key for key in catalog if same(key)]
    preferred = next((key for key in candidates if operation and key[1] == operation.owner), winner)
    result[preferred] = max(result.get(preferred, 0.0), alternative)
    return model, result
