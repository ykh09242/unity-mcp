"""Offline multilingual catalog inference with the two bundled static embedding models."""

import hashlib
import math
import re
import string
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path
from threading import Lock

import regex

from services.registry import semantic_coverage

MODEL_REVISION = "b68f4122911bcffcd6e1f695f2d99cd6788972d8"
MODEL_ID = f"sentence-transformers/static-similarity-mrl-multilingual-v1@{MODEL_REVISION}/256d-q8"
COMBINED_MODEL_ID = f"{MODEL_ID}+{semantic_coverage.MODEL_ID}"
DIMENSIONS = 256
_ARTIFACT = Path(__file__).with_name("model") / "static-multilingual-v1.q8"
_BYTES = 28_400_434
_SHA256 = "f2d9069c23e858304ee4aa95be7b5f8b441d8e660e6a052c3d13ac4ea3faeec2"
_ROW = struct.Struct(f"<f{DIMENSIONS}b")
_MAX_CHARS = 1024
_MAX_CACHED_DOCUMENTS = 2048
_CACHE_LOCK = Lock()
_SPECIAL = re.compile(r"\[(?:PAD|UNK|CLS|SEP|MASK)\]")
# WordPiece isolates unified ideographs and their canonically equivalent forms before NFD.
_IDEOGRAPHS = regex.compile(r"\p{Unified_Ideograph}|(?=\p{Ideographic})\p{dt=Canonical}")


class SemanticModelUnavailable(RuntimeError):
    """The packaged encoder data is missing or damaged."""


def _read_weights() -> bytes:
    try:
        with _ARTIFACT.open("rb") as file:
            data = file.read(_BYTES + 1)
    except OSError as exc:
        raise SemanticModelUnavailable("Bundled catalog encoder is unavailable.") from exc
    if len(data) != _BYTES or hashlib.sha256(data).hexdigest() != _SHA256:
        raise SemanticModelUnavailable("Bundled catalog encoder failed its integrity check.")
    return data


def verify_model() -> None:
    """Check the current packaged bytes independently of the inference cache."""
    _read_weights()


@lru_cache(maxsize=1)
def _model() -> tuple[dict[str, int], bytes]:
    data = _read_weights()
    start = 4 + int.from_bytes(data[:4], "little")
    vocab = data[4:start].decode("utf-8").split("\n")
    offsets = {token: start + index * _ROW.size for index, token in enumerate(vocab)}
    return offsets, data


def _words(text: str):
    cleaned = (
        c
        for c in _SPECIAL.sub(" ", text)
        if c in "\t\r\n" or (c != "\ufffd" and not unicodedata.category(c).startswith("C"))
    )
    normalized = unicodedata.normalize("NFD", _IDEOGRAPHS.sub(r" \g<0> ", "".join(cleaned)))
    word = []
    for c in normalized:
        category = unicodedata.category(c)
        if category == "Mn":
            continue
        punctuation = category.startswith("P") or c in string.punctuation
        if c.isspace() or punctuation:
            if word:
                yield "".join(word)
                word.clear()
            if punctuation:
                yield c
        else:
            word.append(c.lower())
    if word:
        yield "".join(word)


def _piece_offsets(word: str, vocab: dict[str, int]) -> list[int]:
    """Greedy WordPiece. Treat an entire unsegmentable word as unknown."""
    if len(word) > 100:
        return []
    offsets = []
    pos = 0
    while pos < len(word):
        prefix = "##" if pos else ""
        for end in range(len(word), pos, -1):
            offset = vocab.get(prefix + word[pos:end])
            if offset is not None:
                offsets.append(offset)
                pos = end
                break
        else:
            return []
    return offsets


def _encode(text: str, model: tuple[dict[str, int], bytes]) -> tuple[float, ...]:
    vocab, data = model
    vector = [0.0] * DIMENSIONS
    for word in _words(text[:_MAX_CHARS]):
        for offset in _piece_offsets(word, vocab):
            scale, *codes = _ROW.unpack_from(data, offset)
            for index, code in enumerate(codes):
                vector[index] += scale * code
    # L2-normalizing the sum is equivalent to L2-normalizing the token mean.
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm else ()


def fragmented_label(text: str, model_id: str) -> bool:
    """Whether a single unrecognized label was split into many WordPiece fragments."""
    if model_id == semantic_coverage.MODEL_ID or not text.isalpha():
        return False
    words = list(_words(text))
    if len(words) != 1:
        return False
    try:
        vocab, _ = _model()
    except SemanticModelUnavailable:
        return False
    return len(_piece_offsets(words[0], vocab)) >= 6


@lru_cache(maxsize=_MAX_CACHED_DOCUMENTS)
def _document_vector(text: str) -> tuple[float, ...]:
    return _encode(text, _model())


def _pooled_scores(query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
    from services.registry.semantic_alignment import _dot

    # Only shared initialization needs serialization. Query scoring uses immutable data.
    with _CACHE_LOCK:
        model = _model()
    vectors = (
        (_document_vector(text[:_MAX_CHARS]) for text in documents)
        if len(documents) <= _MAX_CACHED_DOCUMENTS
        else (_encode(text, model) for text in documents)
    )
    query_vector = _encode(query, model)
    return tuple(
        _dot(query_vector, vector) if query_vector and vector else 0.0 for vector in vectors
    )


def semantic_scores(query: str, documents: tuple[str, ...]) -> tuple[str, tuple[float, ...]]:
    """Score with verified bundled weights and return the encoder identity."""
    from services.registry.semantic_alignment import rerank

    if not documents:
        return MODEL_ID, ()
    try:
        with _CACHE_LOCK:
            model = _model()
    except SemanticModelUnavailable:
        model = None
    try:
        other = semantic_coverage.similarities(query, documents)
    except SemanticModelUnavailable:
        if model is None:
            raise
        return MODEL_ID, rerank(query, documents, _pooled_scores(query, documents), model)
    if model is None or semantic_coverage.needs_fallback(query, model[0]):
        return semantic_coverage.MODEL_ID, rerank(query, documents, other, None, unigram_weight=1.0)
    primary = _pooled_scores(query, documents)
    if not any(primary):
        # A well-covered query can still face an entirely unrepresented catalog.
        if any(other) and any(char.isalnum() for char in query[:_MAX_CHARS]):
            return semantic_coverage.MODEL_ID, rerank(
                query, documents, other, None, unigram_weight=1.0
            )
        return MODEL_ID, primary
    if not any(other):
        return MODEL_ID, rerank(query, documents, primary, model)
    # Both models see the same text. Their vocabularies and learned spaces were trained independently.
    unigram_weight = 0.4
    pooled = tuple(
        (1 - unigram_weight) * a + unigram_weight * b for a, b in zip(primary, other, strict=True)
    )
    return COMBINED_MODEL_ID, rerank(
        query, documents, pooled, model, unigram_weight=unigram_weight, primary=primary
    )


def semantic_similarities(query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
    return semantic_scores(query, documents)[1]
