"""Offline Unigram embeddings for queries the primary WordPiece vocabulary cannot cover."""

from array import array
from functools import lru_cache
from math import sqrt
from operator import mul
from pathlib import Path
import hashlib
import re
import string
import struct
import sys
from threading import Lock


REVISION = "6fea87d68483744bdb34f8ddab7f5c31a94dc0b8"
MODEL_ID = f"minishlab/potion-multilingual-128M@{REVISION}/256d-q2"
_ARTIFACT = Path(__file__).with_name("model") / "potion-256.q2"
_SHA256 = "f74128b8d2e7a18623eb0c1441c4ad478a296ec013eae1ab5861f6c15518c221"
_BYTES = 41_617_999
_LIMIT = 1024
_LOAD_LOCK = Lock()
_SPLIT_SPECIAL = re.compile(r"\[(?:PAD|UNK)\]")
_SPACE_PUNCTUATION = str.maketrans({c: f" {c} " for c in string.punctuation})
_HEADER = struct.Struct("<4sIII")
_F32 = struct.Struct("<f")
_SCALE = struct.Struct("<e")
_ROW_BYTES = 66
_LEVELS = (-0.453, -1.51, 0.453, 1.51)
_GROUPS = tuple(tuple(_LEVELS[(byte >> bit) & 3] for bit in (0, 2, 4, 6)) for byte in range(256))


def needs_fallback(query: str, primary_vocab: dict[str, int]) -> bool:
    """Select by lexical coverage, independently of the language or catalog."""
    from services.registry.semantic_encoder import _piece_offsets, _words

    total = known = words = pieces = 0
    for word in _words(query[:_LIMIT]):
        if not any(char.isalnum() for char in word):
            continue
        words += 1
        total += len(word)
        offsets = _piece_offsets(word, primary_vocab)
        if offsets:
            known += len(word)
            pieces += len(offsets)
    # Ignore spaces and punctuation. Recognizing only isolated letters in long words
    # is also weak evidence even if every character has a vocabulary entry.
    return total > 0 and (known < 0.80 * total or (pieces > 2 * words and pieces == known))


class _CoverageModel:
    def __init__(self, data: bytes):
        _, count, token_bytes, charsmap_bytes = _HEADER.unpack_from(data)
        start = _HEADER.size
        end = start + token_bytes
        tokens = data[start:end].decode("utf-8").split("\n")
        self.vocab = {token: i for i, token in enumerate(tokens)}
        self.max_piece = max(map(len, tokens))
        # SentencePiece embeds a Darts double-array trie and a NUL-delimited string table.
        trie_bytes = struct.unpack_from("<I", data, end)[0]
        trie = array("I")
        trie.frombytes(data[end + 4 : end + 4 + trie_bytes])
        if sys.byteorder != "little":
            trie.byteswap()
        self.normalizer = trie
        self.replacements = data[end + 4 + trie_bytes : end + charsmap_bytes]
        self.data = data
        self.scores = end + charsmap_bytes
        self.rows = self.scores + count * 4

    def normalize(self, text: str) -> str:
        """Apply the pinned SentencePiece precompiled normalization map."""
        source = text.encode("utf-8", "replace")
        result = bytearray()
        pos = 0
        trie = self.normalizer
        while pos < len(source):
            node = _offset(trie[0])
            matched = 0
            replacement = 0
            # The upstream Precompiled normalizer only attempts multi-code-point
            # replacement for graphemes shorter than six UTF-8 bytes.
            for i in range(pos, min(pos + 5, len(source))):
                unit = trie[node ^ source[i]]
                if unit & 0x800000FF != source[i]:
                    break
                node ^= source[i] ^ _offset(unit)
                if unit & 0x100:
                    matched = i - pos + 1
                    replacement = trie[node] & 0x7FFFFFFF
            if matched:
                end = self.replacements.index(0, replacement)
                result.extend(self.replacements[replacement:end])
                pos += matched
            else:
                # The map has UTF-8 keys. A code point without a match is copied in full.
                lead = source[pos]
                length = 1 if lead < 128 else 2 if lead < 224 else 3 if lead < 240 else 4
                result.extend(source[pos : pos + length])
                pos += length
        return result.decode("utf-8").translate(_SPACE_PUNCTUATION)

    def tokenize(self, text: str):
        for ordinary in _SPLIT_SPECIAL.split(text[:_LIMIT]):
            for word in self.normalize(ordinary).split():
                yield from self.segment("▁" + word)

    def segment(self, word: str):
        """Find the maximum log-probability Unigram segmentation for a normalized word."""
        size = len(word)
        scores = [-float("inf")] * (size + 1)
        sources = [(0, 1)] * (size + 1)
        scores[0] = 0.0
        for start in range(size):
            for end in range(start + 1, min(size, start + self.max_piece) + 1):
                index = self.vocab.get(word[start:end])
                if index is None:
                    if end != start + 1:
                        continue
                    index = 1
                    score = -30.3647518157959  # Minimum vocabulary score minus 10.
                else:
                    score = _F32.unpack_from(self.data, self.scores + index * 4)[0]
                score += scores[start]
                if score > scores[end]:
                    scores[end] = score
                    sources[end] = (start, index)
        result = []
        while size:
            size, index = sources[size]
            if index > 1:
                result.append(index)
        return reversed(result)


def _offset(unit: int) -> int:
    return (unit >> 10) << ((unit & 0x200) >> 6)


def _read_weights() -> bytes:
    from services.registry.semantic_encoder import SemanticModelUnavailable

    try:
        with _ARTIFACT.open("rb") as source:
            data = source.read(_BYTES + 1)
    except OSError as exc:
        raise SemanticModelUnavailable("Bundled Unigram search encoder is unavailable.") from exc
    if len(data) != _BYTES or hashlib.sha256(data).hexdigest() != _SHA256:
        raise SemanticModelUnavailable("Bundled Unigram search encoder failed its integrity check.")
    return data


def verify_model() -> None:
    """Check the current packaged bytes independently of the inference cache."""
    _read_weights()


@lru_cache(maxsize=1)
def _load() -> _CoverageModel:
    return _CoverageModel(_read_weights())


@lru_cache(maxsize=2048)
def _token_vector(model: _CoverageModel, index: int):
    start = model.rows + index * _ROW_BYTES
    scale = _SCALE.unpack_from(model.data, start)[0]
    return tuple(
        scale * value
        for byte in model.data[start + 2 : start + _ROW_BYTES]
        for value in _GROUPS[byte]
    )


def _encode(model: _CoverageModel, text: str):
    values = [0.0] * 256
    for index in model.tokenize(text):
        vector = _token_vector(model, index)
        for i, value in enumerate(vector):
            values[i] += value
    norm = sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values) if norm else ()


_document_vector = lru_cache(maxsize=2048)(_encode)


def similarities(query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
    """Compare arbitrary registered descriptions in the same 256-dimensional space."""
    if not documents:
        return ()
    with _LOAD_LOCK:
        model = _load()
    vector = _encode(model, query)
    if not vector:
        return (0.0,) * len(documents)
    return tuple(
        sum(map(mul, vector, _document_vector(model, document[:_LIMIT]))) for document in documents
    )
