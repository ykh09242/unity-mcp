"""Multilingual retrieval over the current catalog's individual operations."""

import re
import unicodedata

from services.registry.semantic_encoder import fragmented_label, semantic_scores as encode_scores


RouteKey = tuple[str, str, str]
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_QUOTED = re.compile(r'''`[^`\n]+`|"[^"\n]+"''')
_TOKENS = re.compile(r"[\w.-]+(?:[/\\][\w.-]+)*")
_DOCUMENTED_ACTION = re.compile(r"^\s*[-*]\s+`?([\w]+)`?\s*:\s*(\S.*)$", re.MULTILINE)


def _label(identifier: str) -> str:
    return _CAMEL.sub(" ", identifier.replace("_", " ").replace(".", " ")).strip()


def documented_actions(description: str) -> dict[str, str]:
    """Extract action-specific descriptions that dispatchers publish in their schemas."""
    return dict(_DOCUMENTED_ACTION.findall(description))


def _request_context(query: str) -> str:
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
            text = text[:start] + " " + text[end:]
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
    context = _request_context(query)
    if context != query and sum(c.isalpha() for c in context) >= 5:
        context_model, surrounding = encode_scores(context, texts)
        if context_model == model:
            values = tuple(0.4 * a + 0.6 * b for a, b in zip(values, surrounding, strict=True))
    similarities = dict(zip(texts, values, strict=True))
    scores = {
        key: max(similarities[text] for text in descriptions)
        for key, descriptions in catalog.items()
    }
    # Keep a bounded neighborhood of the closest match. Very weak or out-of-vocabulary
    # input should still allow the ordinary no-match and catalog navigation paths.
    peak = max(scores.values())
    if peak < 0.4 and fragmented_label(query):
        return model, {}
    threshold = max(0.16, peak - 0.12)
    return model, {key: value for key, value in scores.items() if value >= threshold}
