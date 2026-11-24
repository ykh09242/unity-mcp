"""Shared query/declaration geometry for the family-first experiment."""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import struct

ARTIFACT = Path(__file__).with_name("family_first_metric.q8")
HEADER = b"UMFAM001"
DIM = 256
RANK = 16
QUERY_ROW = struct.Struct(f"<f{DIM}b")
DOC_ROW = struct.Struct(f"<f{DIM + 4}b")
CHECKSUM = "88ab9a29f997d9cefb3753cc099e66d04aa29eeedb79f6e9c019581d9db6f0bb"


@lru_cache(maxsize=1)
def weights():
    size = len(HEADER) + 2 * RANK * (QUERY_ROW.size + DOC_ROW.size)
    with ARTIFACT.open("rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or not data.startswith(HEADER) or sha256(data).hexdigest() != CHECKSUM:
        raise ValueError("Invalid family-first trial metric")
    start = len(HEADER)
    banks = []
    for row in (QUERY_ROW, DOC_ROW) * 2:
        end = start + RANK * row.size
        banks.append(
            tuple(
                tuple(scale * v for v in codes)
                for scale, *codes in row.iter_unpack(data[start:end])
            )
        )
        start = end
    return tuple(banks)


def encode(text, index, *, document):
    from services.registry import semantic_coverage as coverage, semantic_encoder as encoder

    if index:
        model = coverage._load()
        return coverage._document_vector(model, text) if document else coverage._encode(model, text)
    return encoder._document_vector(text) if document else encoder._encode(text, encoder._model())


def query_vectors(query):
    from services.registry import semantic_coverage as coverage, semantic_encoder as encoder

    try:
        wordpiece = encoder._model()
        first = encode(query, 0, document=False)
    except encoder.SemanticModelUnavailable:
        wordpiece, first = None, ()
    try:
        second = encode(query, 1, document=False)
    except encoder.SemanticModelUnavailable:
        if wordpiece is None:
            raise
        second = ()
    if second and (not first or coverage.needs_fallback(query, wordpiece[0])):
        return coverage.MODEL_ID, ((1, 1.0, second),)
    if first and second:
        return encoder.COMBINED_MODEL_ID, ((0, 0.6, first), (1, 0.4, second))
    model = encoder.MODEL_ID if wordpiece is not None else coverage.MODEL_ID
    return model, ((0, 1.0, first),) if first else ()


def mean(vectors):
    from services.registry.semantic_alignment import _unit

    return _unit(tuple(sum(column) for column in zip(*(v for v in vectors if v))))[0]


@lru_cache(maxsize=2048)
def declaration(card, index):
    fields = [
        encode(text, index, document=True) for text in (card.heading, card.purpose, card.contract)
    ]
    examples = tuple(encode(text, index, document=True) for text in card.examples)
    return mean((*fields, mean(examples))), tuple(v for v in (*fields, *examples) if v)


def project(vector, bank):
    from services.registry.semantic_alignment import _dot

    return tuple(_dot(vector, row) for row in bank) if vector else ()


def contract_vector(card, vector):
    # The query learns a preference for a declared effect and for identified
    # inputs. Missing annotations express no preference in either direction.
    return (
        *vector,
        *(float(card.effect == s) for s in ("observe", "edit", "interact")),
        float(bool(card.required)),
    )


@lru_cache(maxsize=2048)
def projected_declaration(card, index):
    vector, _ = declaration(card, index)
    return project(contract_vector(card, vector), weights()[2 * index + 1]) if vector else ()
