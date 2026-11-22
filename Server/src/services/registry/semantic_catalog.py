"""Multilingual retrieval over the current catalog's individual operations."""

import re
import unicodedata

import regex

from services.registry.semantic_encoder import COMBINED_MODEL_ID, MODEL_ID as STATIC_MODEL_ID
from services.registry.semantic_encoder import fragmented_label, semantic_scores as encode_scores


RouteKey = tuple[str, str, str]
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_QUOTED = re.compile(r'''`[^`\n]+`|"[^"\n]+"''')
_TOKENS = re.compile(r"[\w.-]+(?:[/\\][\w.-]+)*")
_COMPACT_TOKENS = regex.compile(_TOKENS.pattern)
_QUALIFIED = regex.compile(r"\w\.\w")
_NAME_SUFFIX = re.compile(r"['’][^\W\d_]+")
_PLATFORM = regex.compile(r"(?<![\w.-])Unity(?!\w|[.-]\w)", regex.IGNORECASE)
_SENTENCE_TERMINAL = regex.compile(r"\p{Sentence_Terminal}")
_DOCUMENTED_ACTION = re.compile(r"^\s*[-*]\s+`?([\w]+)`?\s*:\s*(\S.*)$", re.MULTILINE)


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
            if char == "." and regex.match(r"\w", text, pos=index + 1):
                continue  # A qualified identifier, numeric version or file extension.
            seen_break = True
    return False


def _request_context(query: str, *, compact: bool = False) -> str:
    """Remove literal paths and conventionally capitalized names for a second comparison."""
    text = _QUOTED.sub(" ", query)
    compound = compact and _has_multiple_clauses(query)
    spans = []
    for token in (_COMPACT_TOKENS if compact else _TOKENS).finditer(text):
        word = token[0]
        # A standalone platform name is not part of the next named argument.
        # Otherwise "Unity Camera" loses the object of the request along with the platform.
        if compact and _PLATFORM.fullmatch(word):
            continue
        literal = "/" in word or "\\" in word or (compound and bool(_QUALIFIED.search(word)))
        if not literal and not (word[0].isupper() and any(c.islower() for c in word)):
            continue
        if spans and text[spans[-1][1] : token.start()].isspace():
            start, _, _ = spans[-1]
            spans[-1] = (start, token.end(), True)
        else:
            spans.append((token.start(), token.end(), literal))
    for start, end, is_literal in reversed(spans):
        if is_literal:
            if compact and (suffix := _NAME_SUFFIX.match(text, end)):
                end = suffix.end()
            text = text[:start] + " " + text[end:]
    if compact and (text.strip() != query.strip() or compound):
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
    compact = model in (STATIC_MODEL_ID, COMBINED_MODEL_ID)
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
    threshold = max(0.16, peak - 0.12)
    return model, {key: value for key, value in scores.items() if value >= threshold}
