"""Multilingual retrieval over the current catalog's individual operations."""

import re
import unicodedata

import regex

from services.registry import semantic_profile
from services.registry.semantic_encoder import MODEL_ID as STATIC_MODEL_ID
from services.registry.semantic_encoder import fragmented_label, semantic_scores as encode_scores
from services.registry.semantic_worker_client import rerank_scores


RouteKey = tuple[str, str, str]
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_QUOTED = re.compile(r'''`[^`\n]+`|"[^"\n]+"''')
_TOKENS = re.compile(r"[\w.-]+(?:[/\\][\w.-]+)*")
_NAME_SUFFIX = re.compile(r"['’][^\W\d_]+")
_PLATFORM = re.compile(r"\bUnity\b", re.IGNORECASE)
_SENTENCE_TERMINAL = regex.compile(r"\p{Sentence_Terminal}")
_DOCUMENTED_ACTION = re.compile(r"^\s*[-*]\s+`?([\w]+)`?\s*:\s*(\S.*)$", re.MULTILINE)


_MAX_RERANK_GROUPS = 24


class RerankedScores(dict[RouteKey, float]):
    """First-stage similarities and separate cross-encoder logits for the same bindings."""

    def __init__(self, similarities: dict[RouteKey, float], logits: dict[RouteKey, float]):
        super().__init__(similarities)
        self.reranker_logits = logits


def _rerank(
    query: str,
    catalog: dict[RouteKey, tuple[str, ...]],
    similarities: dict[str, float],
    scores: dict[RouteKey, float],
    peak: float,
) -> RerankedScores | None:
    # Equivalent tool/resource bindings share passages. Count them once so aliases cannot
    # consume the shortlist. Text tie breaks keep dynamic quantization batches reproducible.
    groups: dict[tuple[str, ...], list[RouteKey]] = {}
    threshold = max(0.16, peak - 0.25)
    for key, score in scores.items():
        if score >= threshold:
            group = tuple(sorted(catalog[key]))
            groups.setdefault(group, []).append(key)
    if not groups:
        return None
    shortlist = sorted(groups, key=lambda texts: (-scores[groups[texts][0]], texts))[
        :_MAX_RERANK_GROUPS
    ]
    passages = {
        group: sorted(group, key=lambda text: (-similarities[text], text))[:2]
        for group in shortlist
    }
    texts = tuple(
        sorted({text for items in passages.values() for text in items}, key=lambda t: (len(t), t))
    )
    logits = rerank_scores(query, texts)
    if logits is None:
        return None
    by_text = dict(zip(texts, logits, strict=True))
    by_route = {
        key: max(by_text[text] for text in passages[group])
        for group in shortlist
        for key in groups[group]
    }
    return RerankedScores({key: scores[key] for key in by_route}, by_route)


def _label(identifier: str) -> str:
    return _CAMEL.sub(" ", identifier.replace("_", " ").replace(".", " ")).strip()


def documented_actions(description: str) -> dict[str, str]:
    """Extract action-specific descriptions that dispatchers publish in their schemas."""
    return dict(_DOCUMENTED_ACTION.findall(description))


def _has_multiple_clauses(text: str) -> bool:
    seen_letter = seen_break = False
    for index, char in enumerate(text):
        if char.isalpha():
            if seen_break:
                return True
            seen_letter = True
        elif seen_letter and (
            _SENTENCE_TERMINAL.fullmatch(char) or unicodedata.name(char, "").endswith("SEMICOLON")
        ):
            if char == "." and text[index - 1].isalnum() and text[index + 1 : index + 2].isalnum():
                continue  # A dot inside an identifier or version number.
            seen_break = True
    return False


def _request_context(query: str, *, compact: bool = False) -> str:
    """Remove literal paths and conventionally capitalized names for a second comparison."""
    text = _QUOTED.sub(" ", query)
    spans = []
    for token in _TOKENS.finditer(text):
        word = token[0]
        path = "/" in word or "\\" in word
        if not path and not (word[0].isupper() and any(c.islower() for c in word)):
            continue
        if spans and text[spans[-1][1] : token.start()].isspace():
            start, _, _ = spans[-1]
            spans[-1] = (start, token.end(), True)
        else:
            spans.append((token.start(), token.end(), path))
    for start, end, is_literal in reversed(spans):
        if is_literal:
            if compact and (suffix := _NAME_SUFFIX.match(text, end)):
                end = suffix.end()
            text = text[:start] + " " + text[end:]
    if compact and (text.strip() != query.strip() or _has_multiple_clauses(query)):
        # Keep a simple instruction intact. For a request with separate arguments or
        # clauses, its second view emphasizes the operation in a catalog entirely for Unity.
        text = _PLATFORM.sub(" ", text)
    return text.strip()


def search_texts(
    name: str,
    action: str | None,
    purpose: str,
    *,
    values=(),
    examples=(),
) -> tuple[str, ...]:
    """Use positive capability descriptions; other operations on the tool are separate documents."""
    name = name.removeprefix("manage_")
    label = _label(name)
    operation = _label(action) if action else ""
    sentence = purpose.split(". ", 1)[0].split("\n", 1)[0].strip()
    field_names = ", ".join(_label(value) for value in values)
    identifier = " ".join(word for word in (label, operation) if word)
    description = f"{identifier}. {sentence}" if sentence else identifier
    if field_names:
        description += f". Returns {field_names}."
    examples = examples[:8] if isinstance(examples, (tuple, list)) else ()
    return tuple(
        dict.fromkeys(
            [description, *(e[:512] for e in examples if isinstance(e, str) and e.strip())]
        )
    )


def semantic_scores(
    query: str, catalog: dict[RouteKey, tuple[str, ...]]
) -> tuple[str, dict[RouteKey, float]]:
    """Embed each distinct description once and score a binding by its closest description."""
    texts = tuple(dict.fromkeys(text for descriptions in catalog.values() for text in descriptions))
    query = unicodedata.normalize("NFKC", query)
    model, values = encode_scores(query, texts)
    # Paths, quoted values and multiword product names can occur in unrelated
    # operations. Also compare the surrounding request to the same declarations.
    compact = model == STATIC_MODEL_ID
    context = _request_context(query, compact=compact)
    if context != (query.strip() if compact else query) and sum(c.isalpha() for c in context) >= 5:
        context_model, surrounding = encode_scores(context, texts)
        if context_model == model:
            original_weight, context_weight = (1 / 3, 2 / 3) if compact else (0.4, 0.6)
            values = tuple(
                original_weight * a + context_weight * b
                for a, b in zip(values, surrounding, strict=True)
            )
    similarities = dict(zip(texts, values, strict=True))
    scores = {
        key: max(similarities[text] for text in descriptions)
        for key, descriptions in catalog.items()
    }
    # Keep a bounded neighborhood of the closest match. Very weak or out-of-vocabulary
    # input should still allow the ordinary no-match and catalog navigation paths.
    peak = max(scores.values())
    if peak < 0.4 and fragmented_label(query, model):
        return model, {}
    if model == semantic_profile.MODEL_ID:
        ranked = _rerank(query, catalog, similarities, scores, peak)
        if ranked is not None:
            return semantic_profile.COMBINED_MODEL_ID, ranked
    threshold = max(0.16, peak - 0.12)
    return model, {key: value for key, value in scores.items() if value >= threshold}
