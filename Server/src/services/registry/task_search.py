"""Bounded local task matching over declarations; never an admission mechanism."""

import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Final, Literal, NotRequired, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from services.registry.action_catalog import (
    ActionGuidance,
    ActionIntent,
    CatalogEntry as SearchEntry,
    CatalogResource,
    GAME_VIEW_OBSERVATION,
    GRAPH_FILE_OBSERVATION,
    Observation,
    ObservationSource,
    ObservationValue,
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
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS


RequiredValues = Annotated[list[ObservationValue], Field(min_length=1, max_length=32)]
RouteSurfaces = Literal["both", "tools", "resources"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    limit: StrictInt = Field(default=5, ge=1, le=20)
    catalog_query: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    intent: ActionIntent | None = None

    observation_source: ObservationSource | None = None
    required_values: RequiredValues | None = None
    route_surfaces: RouteSurfaces = "both"

    @model_validator(mode="after")
    def has_search_input(self):
        if self.query is None and self.observation_source is None and self.required_values is None:
            raise ValueError("Supply query, observation_source or required_values.")
        return self

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


MatchBasis = Literal[
    "exact_identifier",
    "multiple_terms",
    "single_term",
    "incidental",
    "declared_observation",
    "none",
]


class SearchResponse(TypedDict):
    scope: Literal["local_catalog", "mcp_context"]
    query: str | None
    matched_query: str | None
    matching: Literal["lexical", "observation"]
    observation_source: NotRequired[ObservationSource | None]
    required_values: NotRequired[list[ObservationValue]]
    catalog_language: Literal["en"]
    next_action: Literal[
        "inspect_candidates", "provide_catalog_query", "broaden_catalog_query", "browse_catalog"
    ]
    recommended_action: Literal["inspect_candidates", "browse_catalog"]
    match_basis: MatchBasis
    intent: ActionIntent | None
    hint: str
    results: list[SearchResult]
    routes: list["SearchRoute"]
    route_groups: list["SearchRouteGroup"]
    total_route_groups: int
    route_groups_truncated: bool
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


class SearchRouteGroup(TypedDict):
    representative: SearchRoute
    alternatives: list[SearchRoute]


RouteKey = tuple[str, str, str]
RankedRoute = tuple[int, str, str, SearchRoute]


@dataclass(frozen=True, slots=True)
class EquivalentRead:
    tool: str
    resource: str
    observation: Observation
    path: str | None = None
    template_path: str | None = None
    pagination: tuple[str, ...] = ()

    @property
    def parent(self) -> tuple[str, str]:
        return FIXED_OBSERVATION_ACTIONS[self.tool]

    def bindings(self) -> tuple[RouteKey, ...]:
        parent, action = self.parent
        return (
            ("tool", self.tool, ""),
            ("tool", parent, action),
            ("resource_template" if self.template_path else "resource", self.resource, ""),
        )


# Each resource delegates to the dedicated tool, which binds the same native
# action as its mixed tool. Name similarity and matching result fields are
# insufficient to make a new equivalent route.
_EQUIVALENT_READS: Final = (
    EquivalentRead(
        "get_game_view_size", "mcpforunity://editor/game-view-size", GAME_VIEW_OBSERVATION
    ),
    EquivalentRead(
        "inspect_shader_graph",
        "mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}",
        GRAPH_FILE_OBSERVATION,
        path="path",
        template_path="encoded_path",
        pagination=("page_size", "page_number"),
    ),
)
_READ_BY_ROUTE: Final = {key: read for read in _EQUIVALENT_READS for key in read.bindings()}
_REVIEWED_ACTIONS: Final = {
    name: action or None for kind, name, action in _READ_BY_ROUTE if kind == "tool"
}


def _route_key(route: SearchRoute) -> RouteKey:
    return (
        route["kind"],
        route.get("tool") or route.get("uri") or route["uri_template"],
        route.get("operation", ""),
    )


def _equivalent_binding(route: SearchRoute, read: EquivalentRead) -> SearchRoute | None:
    if route.get("observation") != read.observation or route.get("intent") != "observe":
        return None
    path = read.path if route["kind"] == "tool" else read.template_path
    required = [path] if path else []
    optional = list(read.pagination)
    if route["kind"] == "resource_template" and (
        route.get("required_parameters") != required or route.get("optional_parameters") != optional
    ):
        return None
    return {**route, "required_parameters": required, "optional_parameters": optional}


def _group_routes(
    surfaces: RouteSurfaces,
    ranked: list[RankedRoute],
    available: dict[RouteKey, SearchRoute],
    unavailable_parents: set[str],
) -> list[SearchRouteGroup]:
    """Order a group by its strongest visible match, then choose a usable binding."""
    grouped: dict[RouteKey | str, SearchRouteGroup] = {}
    for _, _, _, route in ranked:
        if route["advertised"] is False:
            continue
        key = _route_key(route)
        read = _READ_BY_ROUTE.get(key)
        if read and read.parent[0] in unavailable_parents:
            continue
        if read and key in available:
            group_key = read.tool
            if group_key in grouped:
                continue
            bindings = [available[key] for key in read.bindings() if key in available]
        else:
            group_key = key
            bindings = [route.copy()]
        bindings = [
            binding
            for binding in bindings
            if surfaces == "both" or (binding["kind"] == "tool") == (surfaces == "tools")
        ]
        if bindings:
            grouped.setdefault(
                group_key, {"representative": bindings[0], "alternatives": bindings[1:]}
            )
    return list(grouped.values())


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


def _tool_route(
    result: SearchResult, action: str | None, guidance: ActionGuidance | None
) -> SearchRoute:
    route: SearchRoute = {
        "kind": "tool",
        "tool": result["name"],
        "purpose": result["description"][:320],
        "intent": action_intent(guidance, result),
        "advertised": result["advertised"],
        "readiness": "unobserved",
    }
    if action:
        route["operation"] = action
        route["purpose"] = (
            guidance.purpose if guidance and guidance.purpose else action.replace("_", " ")
        )
    if guidance and guidance.observation:
        route["observation"] = guidance.observation
    if guidance and guidance.evidence_limit:
        route["evidence_limit"] = guidance.evidence_limit
    return route


def _observation_matches(request: SearchRequest, observation: Observation | None) -> bool:
    needed = set(request.required_values or ())
    return bool(
        observation
        and (not request.observation_source or observation["source"] == request.observation_source)
        and needed.issubset(observation["values"])
        and needed.isdisjoint(observation["unavailable"])
        and all(
            len(needed.intersection(group)) <= 1
            for group in observation.get("mutually_exclusive_values", ())
        )
    )


def search_catalog(
    request: SearchRequest,
    entries: list[SearchEntry],
    *,
    visible_names: set[str] | None = None,
    resources: list[CatalogResource] | None = None,
) -> SearchResponse:
    """Rank actions and readable resources while retaining the tool-level result contract."""
    matches: list[tuple[int, str, SearchResult]] = []
    routes: list[RankedRoute] = []
    additional_routes: list[RankedRoute] = []
    available_equivalents: dict[RouteKey, SearchRoute] = {}
    structured = request.observation_source is not None or request.required_values is not None
    unavailable_parents = (
        {entry["name"] for entry in entries} - visible_names if visible_names is not None else set()
    )
    if structured and visible_names is not None:
        entries = [entry for entry in entries if entry["name"] in visible_names]
    matched_query = None if structured else request.catalog_query or request.query
    query = _normalized(matched_query or "")
    query_words = _words(query)
    occurrences: Counter[str] = Counter()
    for entry in entries:
        name = entry["name"]
        result = tool_capability(entry, visible_names=visible_names)
        if name in _REVIEWED_ACTIONS and result["advertised"] is not False:
            action = _REVIEWED_ACTIONS[name]
            if action in (result["actions"] or [None]):
                key: RouteKey = ("tool", name, action or "")
                read = _READ_BY_ROUTE[key]
                route = _tool_route(result, action, action_guidance(entry, action))
                binding = _equivalent_binding(route, read)
                if binding and read.parent[0] not in unavailable_parents:
                    available_equivalents[key] = binding
        if structured:
            selected_actions = []
            for action in result["actions"] or [None]:
                guidance = action_guidance(entry, action)
                if not guidance or not _observation_matches(request, guidance.observation):
                    continue
                route = _tool_route(result, action, guidance)
                if request.intent and route["intent"] != request.intent:
                    continue
                routes.append((0, name, action or "", route))
                selected_actions.append(action)
            if selected_actions:
                result["actions"] = [action for action in selected_actions if action]
                result["action_maturity"] = {
                    action: maturity
                    for action, maturity in result["action_maturity"].items()
                    if action in selected_actions
                }
                matches.append((0, name, result))
            continue
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
        for index, (weight, action, guidance) in enumerate(suggested_actions):
            ranked = routes if index < 3 else additional_routes
            ranked.append((-(weight + score), name, action, _tool_route(result, action, guidance)))
        if not action_matches:
            routes.append(
                (-score, name, "", _tool_route(result, None, action_guidance(entry, None)))
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
        key = _route_key(route)
        if read := _READ_BY_ROUTE.get(key):
            binding = _equivalent_binding(route, read)
            if binding and read.parent[0] not in unavailable_parents:
                available_equivalents[key] = binding
        if structured:
            if _observation_matches(request, route.get("observation")):
                routes.append((0, identifier, "", route))
            continue
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
    groups = _group_routes(
        request.route_surfaces,
        sorted([*routes, *additional_routes], key=lambda item: item[:3]),
        available_equivalents,
        unavailable_parents,
    )
    # Words shared by more than a quarter of this inventory cannot independently
    # corroborate a route. This derives common vocabulary from the catalog itself.
    documents = len(entries) + len(available_resources) + len(available_templates)
    distinctive = {term for term, count in occurrences.items() if count <= max(1, documents // 4)}
    basis: MatchBasis = "none"
    if structured and selected_routes:
        basis = "declared_observation"
    elif not structured:
        bases = [
            _route_evidence(route, query, query_words, distinctive) for route in selected_routes
        ]
        basis = bases[0] if bases else "none"
    if structured:
        next_action = "inspect_candidates" if selected_routes else "browse_catalog"
    elif matches:
        next_action = "inspect_candidates"
    elif request.catalog_query is None:
        next_action = "provide_catalog_query"
    else:
        next_action = "broaden_catalog_query"
    navigation = catalog_navigation(entries, available_resources, available_templates)
    if request.intent:
        for domain in navigation["groups"]:
            domain["browse"]["intent"] = request.intent
    return {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "query": request.query,
        "matched_query": matched_query,
        "matching": "observation" if structured else "lexical",
        **(
            {
                "observation_source": request.observation_source,
                "required_values": request.required_values or [],
            }
            if structured
            else {}
        ),
        "catalog_language": "en",
        "next_action": next_action,
        "recommended_action": "inspect_candidates"
        if basis in {"multiple_terms", "exact_identifier", "declared_observation"}
        else "browse_catalog",
        "match_basis": basis,
        "intent": request.intent,
        "hint": (
            "Routes match the declared evidence source and every required value. Check prerequisites "
            "and any conditions for obtaining those values in the selected Editor."
        )
        if structured
        else (
            "Interpret the original request in its own language. Use navigation to choose by purpose "
            "even when words do not match or lexical suggestions are incidental. Browse the selected "
            "domain and compare action outcomes or read a listed resource. Supply intent with a tool or tool group in catalog "
            "to browse classified actions without matching any words. catalog_query is optional."
        ),
        "navigation": navigation,
        "results": [item[2] for item in matches[: request.limit]],
        "routes": selected_routes,
        "route_groups": groups[: request.limit],
        "total_route_groups": len(groups),
        "route_groups_truncated": len(groups) > request.limit,
        "total_matches": len(matches),
        "truncated": (len(routes) if structured else len(matches)) > request.limit,
        "note": (
            "Suggestions only; search never changes selection, permissions or Unity state. "
            "Advertised reflects this MCP context only, or is null for an offline catalog. "
            "Readiness is unobserved; package requirements and official skills are guidance. "
            "Maturity is support status, independent of enabled state and runtime readiness."
        ),
    }
