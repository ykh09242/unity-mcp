"""Bounded local task matching over declarations; never an admission mechanism."""

import unicodedata
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from services.registry.action_catalog import (
    CatalogEntry as SearchEntry,
    CatalogTool as SearchResult,
    guidance_for,
    tool_capability,
    tool_description,
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: StrictStr = Field(min_length=1, max_length=512)
    limit: StrictInt = Field(default=5, ge=1, le=20)
    catalog_query: StrictStr | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("query", "catalog_query")
    @classmethod
    def nonempty_query(cls, value: str | None) -> str | None:
        """Normalize surrounding whitespace once at the public boundary."""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Query must contain a non-whitespace character.")
        return value


class SearchResponse(TypedDict):
    scope: Literal["local_catalog", "mcp_context"]
    query: str
    matched_query: str
    matching: Literal["lexical"]
    catalog_language: Literal["en"]
    next_action: Literal["inspect_candidates", "provide_catalog_query", "broaden_catalog_query"]
    hint: str
    results: list[SearchResult]
    total_matches: int
    truncated: bool
    note: str


def _normalized(value: str) -> str:
    # Case folding can produce decomposed sequences, including Greek accents.
    folded = unicodedata.normalize("NFKC", value).casefold()
    return unicodedata.normalize("NFKC", folded)


def _words(value: str) -> set[str]:
    # Keep combining marks with their letters across scripts. Separators (including
    # identifier underscores) form boundaries without language-specific word rules.
    characters = (
        character if unicodedata.category(character)[0] in "LNM" else " "
        for character in _normalized(value)
    )
    return set("".join(characters).split())


def search_catalog(
    request: SearchRequest,
    entries: list[SearchEntry],
    *,
    visible_names: set[str] | None = None,
) -> SearchResponse:
    """Rank names, aliases, descriptions and curated intent words without side effects."""
    matches: list[tuple[int, str, SearchResult]] = []
    matched_query = request.catalog_query or request.query
    query = _normalized(matched_query)
    query_words = _words(query)
    for entry in entries:
        name = entry["name"]
        purpose = guidance_for(name)
        result = tool_capability(entry, visible_names=visible_names)
        description = tool_description(entry)
        aliases = result["aliases"]
        actions = result["actions"]
        title = result["title"]
        # Dedicated names and curated tasks outweigh incidental mentions in prose.
        score = 12 * len(query_words.intersection(_words(name)))
        score += 8 * len(query_words.intersection(_words(purpose.keywords)))
        score += 4 * len(query_words.intersection(_words(" ".join((title, *actions)))))
        score += 2 * len(query_words.intersection(_words(" ".join(aliases))))
        score += len(
            query_words.intersection(_words(" ".join((description, entry.get("group") or ""))))
        )
        score += 1000 if query == _normalized(name) else 0
        score += 100 if query in _normalized(name) else 0
        score += 20 if query in _normalized(purpose.keywords) else 0
        if not score:
            continue
        matches.append((-score, name, result))
    matches.sort(key=lambda item: (item[0], item[1]))
    if matches:
        next_action = "inspect_candidates"
    elif request.catalog_query is None:
        next_action = "provide_catalog_query"
    else:
        next_action = "broaden_catalog_query"
    return {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "query": request.query,
        "matched_query": matched_query,
        "matching": "lexical",
        "catalog_language": "en",
        "next_action": next_action,
        "hint": (
            "For lexical matching, the caller can translate intent into English catalog_query terms "
            "while preserving exact identifiers. For semantic selection, browse the complete catalog "
            "with manage_tools(action='catalog') or tool catalog, compare action purposes and effects, "
            "then describe the chosen tool and operation."
        ),
        "results": [item[2] for item in matches[: request.limit]],
        "total_matches": len(matches),
        "truncated": len(matches) > request.limit,
        "note": (
            "Suggestions only; search never changes selection, permissions or Unity state. "
            "Advertised reflects this MCP context only, or is null for an offline catalog. "
            "Readiness is unobserved; package requirements and official skills are guidance. "
            "Maturity is support status, independent of enabled state and runtime readiness."
        ),
    }
