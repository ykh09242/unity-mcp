"""Bounded local task matching over declarations; never an admission mechanism."""

import unicodedata
from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from services.registry.action_catalog import (
    CatalogEntry as SearchEntry,
    CatalogResource,
    CatalogTool as SearchResult,
    action_guidance,
    catalog_navigation,
    concrete_resources,
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
    recommended_action: Literal["inspect_candidates", "browse_catalog"]
    hint: str
    results: list[SearchResult]
    routes: list["SearchRoute"]
    navigation: dict
    total_matches: int
    truncated: bool
    note: str


class SearchRoute(TypedDict):
    kind: Literal["tool", "resource"]
    tool: NotRequired[str]
    operation: NotRequired[str]
    uri: NotRequired[str]
    purpose: str
    evidence_limit: NotRequired[str]
    advertised: bool | None
    readiness: Literal["unobserved"]


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


def _term_evidence(words: set[str], *fields: tuple[str, int]) -> dict[str, int]:
    """Count a matching word once, at the strongest field in which it appears."""
    evidence: dict[str, int] = {}
    for value, strength in fields:
        for word in words & _words(value):
            evidence[word] = max(strength, evidence.get(word, 0))
    return evidence


def search_catalog(
    request: SearchRequest,
    entries: list[SearchEntry],
    *,
    visible_names: set[str] | None = None,
    resources: list[CatalogResource] | None = None,
) -> SearchResponse:
    """Rank actions and readable resources while retaining the tool-level result contract."""
    matches: list[tuple[int, str, SearchResult]] = []
    routes: list[tuple[int, str, str, SearchRoute]] = []
    matched_query = request.catalog_query or request.query
    query = _normalized(matched_query)
    query_words = _words(query)
    for entry in entries:
        name = entry["name"]
        result = tool_capability(entry, visible_names=visible_names)
        description = tool_description(entry)
        aliases = result["aliases"]
        actions = result["actions"]
        title = result["title"]
        terms = _term_evidence(
            query_words,
            (name, 6),
            (title, 4),
            (" ".join(aliases), 2),
            (description + " " + (entry.get("group") or ""), 1),
        )
        score = sum(terms.values())
        score += 1000 if query == _normalized(name) else 0
        score += 100 if query in _normalized(name) else 0
        action_matches = []
        for action in actions:
            guidance = action_guidance(entry, action)
            action_terms = _term_evidence(
                query_words, (action, 9), (guidance.purpose if guidance else "", 6)
            )
            if not action_terms:
                continue
            # A repeated domain word in tool/title/action cannot crowd out the words
            # that distinguish an operation, such as performing or observing an input.
            weight = sum(
                max(0, strength - terms.get(word, 0)) for word, strength in action_terms.items()
            )
            if query == _normalized(action):
                weight += 150
            action_matches.append((weight, action, guidance))
        action_matches.sort(key=lambda item: (-item[0], item[1]))
        if not score and not action_matches:
            continue
        best = score + action_matches[0][0] if action_matches else score
        matches.append((-best, name, result))
        for weight, action, guidance in action_matches[:3]:
            routes.append(
                (
                    -(weight + score),
                    name,
                    action,
                    {
                        "kind": "tool",
                        "tool": name,
                        "operation": action,
                        "purpose": guidance.purpose
                        if guidance and guidance.purpose
                        else action.replace("_", " "),
                        **(
                            {"evidence_limit": guidance.evidence_limit}
                            if guidance and guidance.evidence_limit
                            else {}
                        ),
                        "advertised": result["advertised"],
                        "readiness": "unobserved",
                    },
                )
            )
        if not action_matches:
            routes.append(
                (
                    -score,
                    name,
                    "",
                    {
                        "kind": "tool",
                        "tool": name,
                        "purpose": result["description"][:320],
                        "advertised": result["advertised"],
                        "readiness": "unobserved",
                    },
                )
            )
    available_resources = concrete_resources(resources or [])
    for resource in available_resources:
        uri = resource["uri"]
        description = resource.get("description") or ""
        purpose = description.split("\n", 1)[0].split(". ", 1)[0]
        weight = sum(_term_evidence(query_words, (resource["name"], 8), (purpose, 6)).values())
        if query == _normalized(uri):
            weight += 1000
        if weight:
            routes.append(
                (
                    -weight,
                    uri,
                    "",
                    {
                        "kind": "resource",
                        "uri": uri,
                        "purpose": description.split("\n", 1)[0][:320],
                        "advertised": True if visible_names is not None else None,
                        "readiness": "unobserved",
                    },
                )
            )
    matches.sort(key=lambda item: (item[0], item[1]))
    routes.sort(key=lambda item: item[:3])
    if matches:
        lexical_action = "inspect_candidates"
    elif request.catalog_query is None:
        lexical_action = "provide_catalog_query"
    else:
        lexical_action = "broaden_catalog_query"
    return {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "query": request.query,
        "matched_query": matched_query,
        "matching": "lexical",
        "catalog_language": "en",
        "next_action": lexical_action,
        "recommended_action": "inspect_candidates" if routes else "browse_catalog",
        "hint": (
            "Interpret the original request in its own language. Use navigation to choose by purpose "
            "even when words do not match or lexical suggestions are incidental. Browse the selected "
            "domain and compare action outcomes or read a listed resource. catalog_query is optional."
        ),
        "navigation": catalog_navigation(entries, available_resources),
        "results": [item[2] for item in matches[: request.limit]],
        "routes": [item[3] for item in routes[: request.limit]],
        "total_matches": len(matches),
        "truncated": len(matches) > request.limit,
        "note": (
            "Suggestions only; search never changes selection, permissions or Unity state. "
            "Advertised reflects this MCP context only, or is null for an offline catalog. "
            "Readiness is unobserved; package requirements and official skills are guidance. "
            "Maturity is support status, independent of enabled state and runtime readiness."
        ),
    }
