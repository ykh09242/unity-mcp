"""Bounded local task matching over declarations; never an admission mechanism."""

import unicodedata
from collections import Counter
from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from services.registry.action_catalog import (
    ActionIntent,
    CatalogEntry as SearchEntry,
    CatalogResource,
    Observation,
    CatalogTool as SearchResult,
    RESOURCE_TEMPLATE_EXPANSION,
    action_guidance,
    action_intent,
    catalog_navigation,
    concrete_resources,
    resource_observation,
    resource_templates,
    tool_capability,
    tool_description,
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: StrictStr = Field(min_length=1, max_length=512)
    limit: StrictInt = Field(default=5, ge=1, le=20)
    catalog_query: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    intent: ActionIntent | None = None

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


MatchBasis = Literal["exact_identifier", "multiple_terms", "single_term", "incidental", "none"]


class SearchResponse(TypedDict):
    scope: Literal["local_catalog", "mcp_context"]
    query: str
    matched_query: str
    matching: Literal["lexical"]
    catalog_language: Literal["en"]
    next_action: Literal["inspect_candidates", "provide_catalog_query", "broaden_catalog_query"]
    recommended_action: Literal["inspect_candidates", "browse_catalog"]
    match_basis: MatchBasis
    intent: ActionIntent | None
    hint: str
    results: list[SearchResult]
    routes: list["SearchRoute"]
    navigation: dict
    total_matches: int
    truncated: bool
    note: str


class SearchRoute(TypedDict):
    kind: Literal["tool", "resource", "resource_template"]
    tool: NotRequired[str]
    operation: NotRequired[str]
    uri: NotRequired[str]
    uri_template: NotRequired[str]
    name: NotRequired[str]
    required_parameters: NotRequired[list[str]]
    optional_parameters: NotRequired[list[str]]
    purpose: str
    evidence_limit: NotRequired[str]
    observation: NotRequired[Observation]
    advertised: bool | None
    readiness: Literal["unobserved"]
    intent: NotRequired[ActionIntent | None]
    matched_terms: NotRequired[list[str]]
    distinctive_terms: NotRequired[list[str]]


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


def _route_evidence(
    route: SearchRoute, query: str, words: set[str], distinctive: set[str]
) -> MatchBasis:
    identifiers = [
        value
        for value in (
            route.get("tool"),
            route.get("operation"),
            route.get("uri"),
            route.get("uri_template"),
            route.get("name"),
        )
        if value
    ]
    terms = sorted(words & _words(" ".join([*identifiers, route["purpose"]])))
    route["matched_terms"] = terms
    route["distinctive_terms"] = [word for word in terms if word in distinctive]
    if any(query == _normalized(identifier) for identifier in identifiers):
        return "exact_identifier"
    if len(route["distinctive_terms"]) >= 2:
        return "multiple_terms"
    return "single_term" if route["distinctive_terms"] else "incidental"


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
    occurrences: Counter[str] = Counter()
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
        entry_terms = set(terms)
        for action in actions:
            guidance = action_guidance(entry, action)
            action_terms = _term_evidence(
                query_words, (action, 9), (guidance.purpose if guidance else "", 6)
            )
            if not action_terms:
                continue
            entry_terms.update(action_terms)
            # A repeated domain word in tool/title/action cannot crowd out the words
            # that distinguish an operation, such as performing or observing an input.
            weight = sum(
                max(0, strength - terms.get(word, 0)) for word, strength in action_terms.items()
            )
            if query == _normalized(action):
                weight += 150
            action_matches.append((weight, action, guidance))
        occurrences.update(entry_terms)
        action_matches.sort(key=lambda item: (-item[0], item[1]))
        if not score and not action_matches:
            continue
        best = score + action_matches[0][0] if action_matches else score
        matches.append((-best, name, result))
        suggested_actions = action_matches
        if request.intent:
            suggested_actions = [
                item for item in action_matches if action_intent(item[2], result) == request.intent
            ]
        for weight, action, guidance in suggested_actions[:3]:
            routes.append(
                (
                    -(weight + score),
                    name,
                    action,
                    {
                        "kind": "tool",
                        "tool": name,
                        "operation": action,
                        **(
                            {"observation": guidance.observation}
                            if guidance and guidance.observation
                            else {}
                        ),
                        "intent": action_intent(guidance, result),
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
                        "intent": action_intent(None, result),
                        "advertised": result["advertised"],
                        "readiness": "unobserved",
                    },
                )
            )
    available_resources = concrete_resources(resources or [])
    available_templates = resource_templates(resources or [])
    for resource in [*available_resources, *available_templates]:
        description = resource.get("description") or ""
        summary = description.split("\n", 1)[0]
        purpose = summary.split(". ", 1)[0]
        route: SearchRoute = {
            "kind": "resource",
            "purpose": summary[:320],
            "advertised": True if visible_names is not None else None,
            "readiness": "unobserved",
            "intent": "observe",
        }
        exact_name = False
        if "uri_template" in resource:
            identifier = resource["uri_template"]
            route["kind"] = "resource_template"
            route["uri_template"] = identifier
            route["required_parameters"] = resource["required_parameters"]
            route["optional_parameters"] = resource["optional_parameters"]
            if "observation" in resource:
                route["observation"] = resource["observation"]
            route["evidence_limit"] = RESOURCE_TEMPLATE_EXPANSION
            exact_name = query == _normalized(resource["name"])
            evidence = _term_evidence(
                query_words,
                (resource["name"], 6),
                (purpose, 5),
                (identifier.partition("://")[2], 4),
            )
        else:
            identifier = resource["uri"]
            route.update(resource_observation(resource))
            route["uri"] = identifier
            evidence = _term_evidence(query_words, (resource["name"], 8), (purpose, 6))
        route["name"] = resource["name"]
        occurrences.update(evidence.keys())
        weight = sum(evidence.values())
        if exact_name or query == _normalized(identifier):
            weight += 1000
        if weight:
            routes.append((-weight, identifier, "", route))
    matches.sort(key=lambda item: (item[0], item[1]))
    if request.intent:
        routes = [item for item in routes if item[3].get("intent") == request.intent]
    routes.sort(key=lambda item: item[:3])
    selected_routes = [item[3] for item in routes[: request.limit]]
    # Words shared by more than a quarter of this inventory cannot independently
    # corroborate a route. This derives common vocabulary from the catalog itself.
    documents = len(entries) + len(available_resources) + len(available_templates)
    distinctive = {term for term, count in occurrences.items() if count <= max(1, documents // 4)}
    bases = [_route_evidence(route, query, query_words, distinctive) for route in selected_routes]
    basis = bases[0] if bases else "none"
    if matches:
        lexical_action = "inspect_candidates"
    elif request.catalog_query is None:
        lexical_action = "provide_catalog_query"
    else:
        lexical_action = "broaden_catalog_query"
    navigation = catalog_navigation(entries, available_resources, available_templates)
    if request.intent:
        for domain in navigation["groups"]:
            domain["browse"]["intent"] = request.intent
    return {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "query": request.query,
        "matched_query": matched_query,
        "matching": "lexical",
        "catalog_language": "en",
        "next_action": lexical_action,
        "recommended_action": "inspect_candidates"
        if basis in {"multiple_terms", "exact_identifier"}
        else "browse_catalog",
        "match_basis": basis,
        "intent": request.intent,
        "hint": (
            "Interpret the original request in its own language. Use navigation to choose by purpose "
            "even when words do not match or lexical suggestions are incidental. Browse the selected "
            "domain and compare action outcomes or read a listed resource. Supply intent with a tool or tool group in catalog "
            "to browse classified actions without matching any words. catalog_query is optional."
        ),
        "navigation": navigation,
        "results": [item[2] for item in matches[: request.limit]],
        "routes": selected_routes,
        "total_matches": len(matches),
        "truncated": len(matches) > request.limit,
        "note": (
            "Suggestions only; search never changes selection, permissions or Unity state. "
            "Advertised reflects this MCP context only, or is null for an offline catalog. "
            "Readiness is unobserved; package requirements and official skills are guidance. "
            "Maturity is support status, independent of enabled state and runtime readiness."
        ),
    }
