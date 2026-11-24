"""Bounded local task matching over declarations; never an admission mechanism."""

import unicodedata
from collections import Counter
from collections.abc import Collection
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

from models.host_tool_inventory import HostToolEvidence, HostToolNames
from services.registry.action_catalog import (
    ACTIVE_SCENE_OBSERVATION,
    EDITOR_STATE_OBSERVATION,
    POINTER_ORDER_OBSERVATION,
    ActionGuidance,
    ActionIntent,
    CatalogEntry as SearchEntry,
    CatalogResource,
    GAME_VIEW_OBSERVATION,
    GRAPH_FILE_OBSERVATION,
    OBSERVATION_META,
    Observation,
    ObservationSource,
    ObservationValue,
    CatalogTool as SearchResult,
    RESOURCE_TEMPLATE_EXPANSION,
    SEARCH_TERMS_META,
    TOOL_GUIDANCE,
    action_guidance,
    action_intent,
    catalog_navigation,
    concrete_resources,
    documented_actions,
    operation_owner,
    resource_observation,
    resource_templates,
    tool_capability,
    tool_description,
)
from services.registry.open_shader_graph_observation import OPEN_GRAPH_OBSERVATION
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS
from services.registry.capability_catalog import (
    CAMERA_INVENTORY_TOOL,
    CAMERA_INVENTORY_URI,
    MENU_DECLARATIONS_TOOL,
    MENU_DECLARATIONS_URI,
)
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.registry.integration_action_guidance import MENU_INVENTORY_OBSERVATION
from services.registry.semantic_catalog import (
    SEMANTIC_EVIDENCE_FLOOR,
    search_texts,
    semantic_scores,
)
from services.registry.semantic_encoder import SemanticModelUnavailable
from services.registry.semantic_operations import Operation, rank_operations
from services.registry.semantic_metric import recall_operation
from services.registry.semantic_family_recall import rank_observations


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
    host_tool_names: HostToolNames | None = None
    host_tool_evidence: HostToolEvidence | None = None

    @model_validator(mode="after")
    def has_search_input(self):
        if self.query is None and self.observation_source is None and self.required_values is None:
            raise ValueError("Supply query, observation_source or required_values.")
        if self.host_tool_names is not None and self.host_tool_evidence is not None:
            raise ValueError("Supply host_tool_names or host_tool_evidence.")
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
    "semantic_similarity",
    "none",
]


class SearchHostInventory(TypedDict):
    source: Literal["caller_report"]
    complete: bool
    statuses: dict[str, Literal["host_advertised", "host_filtered", "host_unknown"]]


class SearchResponse(TypedDict):
    scope: Literal["local_catalog", "mcp_context"]
    query: str | None
    matched_query: str | None
    matching: Literal["lexical", "observation", "hybrid"]
    semantic_model: NotRequired[str]
    semantic_status: NotRequired[Literal["unavailable"]]
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
    no_match: NotRequired["NoMatch"]
    host_inventory: NotRequired[SearchHostInventory]
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
    arguments: NotRequired[dict]
    purpose: str
    evidence_limit: NotRequired[str]
    observation: NotRequired[Observation]
    advertised: bool | None
    readiness: Literal["unobserved"]
    intent: NotRequired[ActionIntent | None]
    matched_terms: NotRequired[list[str]]
    semantic_similarity: NotRequired[float]
    distinctive_terms: NotRequired[list[str]]


class SearchRouteGroup(TypedDict):
    representative: SearchRoute
    alternatives: list[SearchRoute]


NoMatchReason = Literal[
    "explicitly_unavailable",
    "undeclared_evidence",
    "wrong_source",
    "incompatible_combination",
    "lexical_miss",
    "no_visible_route",
]


class NoMatch(TypedDict):
    reason: NoMatchReason
    explanation: str
    undeclared_values: list[ObservationValue]
    excluded_values: list[ObservationValue]
    alternatives: list[SearchRoute]
    alternatives_truncated: bool


RouteKey = tuple[str, str, str]
RankedRoute = tuple[float, str, str, SearchRoute]


@dataclass(frozen=True, slots=True)
class EquivalentRead:
    tool: str
    resource: str | None
    observation: Observation
    path: str | None = None
    template_path: str | None = None
    pagination: tuple[str, ...] = ()
    focused_action: tuple[str, str] | None = None
    fallback_tools: tuple[tuple[str, str], ...] = ()

    @property
    def parent(self) -> tuple[str, str] | None:
        return self.focused_action or FIXED_OBSERVATION_ACTIONS.get(self.tool)

    def bindings(self) -> tuple[RouteKey, ...]:
        tools: tuple[RouteKey, ...] = (
            ("tool", self.tool, self.focused_action[1] if self.focused_action else ""),
        )
        if self.parent:
            tools += (("tool", *self.parent),)
        if self.resource is None:
            return tools
        resource: RouteKey = (
            "resource_template" if self.template_path else "resource",
            self.resource,
            "",
        )
        return (*tools, resource, *(("tool", name, action) for name, action in self.fallback_tools))

    def allowed(self, unavailable_parents: set[str]) -> bool:
        # Fixed tools/resources inherit the parent toggle. A focused tool has its own toggle.
        return (
            self.focused_action is not None
            or self.parent is None
            or self.parent[0] not in unavailable_parents
        )


# Each relationship is backed by the same native read implementation.
_EQUIVALENT_READS: Final = (
    EquivalentRead(MENU_DECLARATIONS_TOOL, MENU_DECLARATIONS_URI, MENU_INVENTORY_OBSERVATION),
    EquivalentRead(
        CAMERA_INVENTORY_TOOL,
        CAMERA_INVENTORY_URI,
        CAMERA_INVENTORY_OBSERVATION,
        fallback_tools=(("manage_cameras", "list_cameras"), ("manage_camera", "list_cameras")),
    ),
    EquivalentRead("inspect_editor_state", "mcpforunity://editor/state", EDITOR_STATE_OBSERVATION),
    EquivalentRead("inspect_ugui_hit_order", None, POINTER_ORDER_OBSERVATION),
    EquivalentRead("inspect_open_shader_graph", None, OPEN_GRAPH_OBSERVATION, path="path"),
    EquivalentRead(
        "scene_inspect",
        None,
        ACTIVE_SCENE_OBSERVATION,
        focused_action=("manage_scene", "get_active"),
    ),
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
    required = [path] if path else list(read.observation.get("required_parameters", []))
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
    confirmed_tools: Collection[str],
) -> list[SearchRouteGroup]:
    """Order a group by its strongest visible match, then choose a usable binding."""
    grouped: dict[RouteKey | str, SearchRouteGroup] = {}
    for _, _, _, route in ranked:
        if route["advertised"] is False:
            continue
        key = _route_key(route)
        read = _READ_BY_ROUTE.get(key)
        if read and not read.allowed(unavailable_parents):
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
        if confirmed_tools and len(bindings) > 1:
            # Prefer confirmed tools within the same surface. Preserve resource slots
            # and the declaration order within each group of equally callable tools.
            tools = iter(
                sorted(
                    (binding for binding in bindings if binding["kind"] == "tool"),
                    key=lambda binding: binding["tool"] not in confirmed_tools,
                )
            )
            bindings = [
                next(tools) if binding["kind"] == "tool" else binding for binding in bindings
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


def _lexical_score(evidence: dict[str, int], words: set[str], hybrid: bool) -> int:
    score = sum(evidence.values())
    # A product name alone can match several verbs. Corroborate it with the rest of
    # the request before it outweighs retrieval of the requested operation.
    return round(score * len(evidence) / len(words)) if hybrid and words else score


def semantic_documents(
    capabilities,
    resources,
    unavailable_parents,
    intent=None,
    *,
    exact_query=None,
    surfaces="both",
    operations=None,
    effects=None,
):
    """Build semantic documents from eligible declarations; an exact identifier bypasses ranking."""
    documents: dict[RouteKey, tuple[str, ...]] = {}
    equivalent_keys: dict[str, list[RouteKey]] = {}
    exact_keys: set[RouteKey] = set()
    query = _normalized(exact_query) if exact_query is not None else None

    def add(key, name, action, purpose, observation, examples=()):
        if read := _READ_BY_ROUTE.get(key):
            if not read.allowed(unavailable_parents):
                return
            if observation == read.observation:
                equivalent_keys.setdefault(read.tool, []).append(key)
        documents[key] = search_texts(
            name, action, purpose, values=(observation or {}).get("values", ()), examples=examples
        )

    for entry, result in capabilities:
        name = result["name"]
        if result["advertised"] is False:
            continue
        if surfaces != "resources" and query == _normalized(name):
            return None
        action_descriptions = documented_actions(tool_description(entry))
        for action in result["actions"] or [None]:
            if surfaces != "resources" and action and query == _normalized(action):
                return None
            guidance = action_guidance(entry, action)
            if intent and intent != action_intent(guidance, result):
                continue
            key = ("tool", name, action or "")
            if query == _normalized(name) or (action and query == _normalized(action)):
                exact_keys.add(key)
            owner = operation_owner(name, action) if action else name
            local = action_guidance(entry, action, inherit=False) if owner != name else guidance
            canonical = (
                TOOL_GUIDANCE[owner].actions.get(action)
                if owner in TOOL_GUIDANCE and action
                else None
            )
            purpose = (
                local.purpose
                if local
                else (action_descriptions.get(action, "") if action else result["description"])
            )
            examples = local.search_examples if local else ()
            if canonical and owner != name and local and local.purpose != canonical.purpose:
                examples = tuple(ex for ex in examples if ex not in canonical.search_examples)
            add(
                key,
                name,
                action,
                purpose,
                guidance.observation if guidance else None,
                examples,
            )
            if effects is not None and key in documents:
                effects[key] = action_intent(guidance, result)
            if operations is not None and action and key in documents:
                card = canonical or guidance
                operations[key] = Operation(
                    owner,
                    action,
                    card.purpose if card else purpose,
                    tuple(card.search_examples) if card else (),
                    tuple(card.required) if card else (),
                )
    for resource in resources:
        identifier = resource.get("uri") or resource["uri_template"]
        if surfaces != "tools" and query in (
            _normalized(identifier),
            _normalized(resource["name"]),
        ):
            return None
        if intent not in (None, "observe"):
            continue
        template = "uri_template" in resource
        key = ("resource_template" if template else "resource", identifier, "")
        if query in (_normalized(identifier), _normalized(resource["name"])):
            exact_keys.add(key)
        meta = resource.get("kwargs", {}).get("meta") or {}
        alias = meta.get(SEARCH_TERMS_META) or {}
        add(
            key,
            resource["name"],
            None,
            resource.get("description") or "",
            resource.get("observation") if template else meta.get(OBSERVATION_META),
            alias.get("examples", ()) if isinstance(alias, dict) else (),
        )
        if effects is not None and key in documents:
            effects[key] = "observe"
    for keys in equivalent_keys.values():
        descriptions = tuple(dict.fromkeys(text for key in keys for text in documents[key]))
        for key in keys:
            documents[key] = descriptions
    if surfaces != "both":
        eligible = {key for key in documents if (key[0] == "tool") == (surfaces == "tools")}
        # Keep the tool-level view of a matched resource (and vice versa). These
        # bindings score the same documents and cannot change the cutoff.
        for keys in equivalent_keys.values():
            if eligible.intersection(keys):
                eligible.update(keys)
        documents = {key: texts for key, texts in documents.items() if key in eligible}
    return None if exact_keys.intersection(documents) else documents


def _semantic_catalog(original, capabilities, resources, unavailable_parents, intent, surfaces):
    operations, effects = {}, {}
    documents = semantic_documents(
        capabilities,
        resources,
        unavailable_parents,
        intent,
        exact_query=original,
        surfaces=surfaces,
        operations=operations,
        effects=effects,
    )
    if not documents:
        return None
    baseline = semantic_scores(original, documents)
    scores = baseline[1]
    # A tool and a resource can expose the same observation. If it is the only document
    # in the retrieved neighborhood, there is no competing operation to disambiguate.
    if surfaces == "resources" or (
        len({key[0] == "tool" for key in scores}) == 2
        and len({documents[key] for key in scores}) == 1
    ):
        return baseline
    canonical = rank_operations(original, documents, operations, baseline, semantic_scores)
    ranked = recall_operation(original, documents, operations, baseline, canonical, effects)
    return rank_observations(original, documents, operations, effects, ranked)


def _route_evidence(
    route: SearchRoute, query: str, words: set[str], distinctive: set[str], unique_terms: set[str]
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
    terms = sorted(
        set(route.get("matched_terms", ()))
        | (words & _words(" ".join([*identifiers, route["purpose"]])))
    )
    route["matched_terms"] = terms
    identifier_words = _words(" ".join(identifiers))
    # Short prose terms need corroboration from a route identifier or a term
    # unique to one catalog entry. Apply the same evidence rule to every script.
    route["distinctive_terms"] = [
        word
        for word in terms
        if word in distinctive
        and (
            word in identifier_words
            or word in unique_terms
            or sum(char.isalpha() for char in word) > 2
            or any(char.isdecimal() for char in word)
        )
    ]
    if any(query == _normalized(identifier) for identifier in identifiers):
        return "exact_identifier"
    if len(route["distinctive_terms"]) >= 2:
        return "multiple_terms"
    if route.get("semantic_similarity", 0.0) >= SEMANTIC_EVIDENCE_FLOOR:
        return "semantic_similarity"
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
        observation = guidance.observation
        route["observation"] = observation
        route["arguments"] = {
            **({"action": action} if action else {}),
            **(observation.get("fixed_arguments", {}) if action else {}),
        }
        route["required_parameters"] = list(
            observation.get("required_parameters", guidance.required)
        )
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


_NO_MATCH_EXPLANATIONS: Final[dict[NoMatchReason, str]] = {
    "explicitly_unavailable": "Visible declarations explicitly exclude requested evidence. No eligible route declares it.",
    "undeclared_evidence": "No eligible declaration establishes the requested evidence in this scope. Support is unknown.",
    "wrong_source": "A visible route declares all required values only from a different source.",
    "incompatible_combination": "The values are individually declared, but no eligible route supplies this combination in one observation.",
    "lexical_miss": "No catalog text matched. Evidence support is unknown.",
    "no_visible_route": "No matching route is available with the selected intent, invocation surfaces and visibility in this scope.",
}


def _diagnose_miss(
    request: SearchRequest,
    structured: bool,
    matched_before_filters: bool,
    observations: list[RankedRoute],
    equivalents: dict[RouteKey, SearchRoute],
    unavailable_parents: set[str],
) -> NoMatch:
    # Group only declarations surviving the same intent, surface, registration and parent caps
    # as the normal results. Never introspect other clients or the full local registry here.
    host_evidence = request.host_tool_evidence
    groups = _group_routes(
        request.route_surfaces,
        observations,
        equivalents,
        unavailable_parents,
        host_evidence.present if host_evidence is not None else (),
    )
    needed = set(request.required_values or ())
    offered: set[ObservationValue] = set()
    excluded: set[ObservationValue] = set()
    candidates = []
    different_source = False
    for group in groups:
        route = group["representative"]
        observation = route["observation"]
        values = needed.intersection(observation["values"])
        unavailable = needed.intersection(observation["unavailable"])
        offered.update(values)
        excluded.update(unavailable)
        if not values and not unavailable:
            continue
        compatible = not any(
            len(needed.intersection(pair)) > 1
            for pair in observation.get("mutually_exclusive_values", ())
        )
        complete = bool(needed and values == needed and compatible and not unavailable)
        different_source |= complete and observation["source"] != request.observation_source
        # Prefer routes supplying actual requested fields, then a declared related observation.
        related = any(value in observation.get("related_values", {}) for value in unavailable)
        candidates.append(
            (not complete, -len(values), not related, -len(unavailable), _route_key(route), route)
        )

    undeclared = needed - offered - excluded
    reason: NoMatchReason
    if matched_before_filters:
        reason = "no_visible_route"
    elif not structured:
        reason = "lexical_miss"
    elif different_source:
        reason = "wrong_source"
    elif undeclared or not needed:
        reason = "undeclared_evidence"
    elif needed - offered:
        reason = "explicitly_unavailable"
    else:
        reason = "incompatible_combination"
    candidates.sort(key=lambda item: item[:-1])
    return {
        "reason": reason,
        "explanation": _NO_MATCH_EXPLANATIONS[reason],
        "undeclared_values": sorted(undeclared),
        "excluded_values": sorted(excluded - offered),
        "alternatives": [item[-1] for item in candidates[: request.limit]],
        "alternatives_truncated": len(candidates) > request.limit,
    }


def search_catalog(
    request: SearchRequest,
    entries: list[SearchEntry],
    *,
    visible_names: set[str] | None = None,
    resources: list[CatalogResource] | None = None,
) -> SearchResponse:
    """Rank actions and readable resources while retaining the tool-level result contract."""
    matches: list[tuple[float, str, SearchResult]] = []
    routes: list[RankedRoute] = []
    additional_routes: list[RankedRoute] = []
    observations: list[RankedRoute] = []
    available_equivalents: dict[RouteKey, SearchRoute] = {}
    structured = request.observation_source is not None or request.required_values is not None
    host_evidence = request.host_tool_evidence
    confirmed_tools = set(host_evidence.present) if host_evidence is not None else set()

    def route_order(item: RankedRoute) -> tuple[float, bool, str, str]:
        score, name, action, route = item
        return score, route.get("tool") not in confirmed_tools, name, action

    unavailable_parents = (
        {entry["name"] for entry in entries} - visible_names if visible_names is not None else set()
    )
    if request.host_tool_names is not None:
        # A host can narrow a live list. Its report never establishes server authority.
        allowed = set(request.host_tool_names).intersection(visible_names or ())
        entries = [entry for entry in entries if entry["name"] in allowed]
    elif host_evidence is not None:
        advertised = visible_names or set()
        entries = [
            entry
            for entry in entries
            if entry["name"] in advertised and host_evidence.received(entry["name"]) is not False
        ]
    elif structured and visible_names is not None:
        entries = [entry for entry in entries if entry["name"] in visible_names]
    matched_query = None if structured else request.catalog_query or request.query
    query = _normalized(matched_query or "")
    query_words = _words(query)
    available_resources = concrete_resources(resources or [])
    available_templates = resource_templates(resources or [])
    capabilities = [
        (entry, tool_capability(entry, visible_names=visible_names)) for entry in entries
    ]
    model = None
    semantic: dict[RouteKey, float] = {}
    model_unavailable = False
    if query and not request.catalog_query:
        try:
            encoded = _semantic_catalog(
                matched_query,
                capabilities,
                [*available_resources, *available_templates],
                unavailable_parents,
                request.intent,
                request.route_surfaces,
            )
            if encoded is not None:
                model, semantic = encoded
        except SemanticModelUnavailable:
            model_unavailable = True
    weights = {key: 160 * cosine for key, cosine in semantic.items()}

    def add_semantic_evidence(route: SearchRoute, key: RouteKey) -> None:
        if key in semantic:
            route["semantic_similarity"] = round(semantic[key], 4)

    occurrences: Counter[str] = Counter()
    for entry, result in capabilities:
        name = entry["name"]
        if result["advertised"] is not False:
            for action in result["actions"] or [None]:
                key: RouteKey = ("tool", name, action or "")
                read = _READ_BY_ROUTE.get(key)
                if read is None:
                    continue
                route = _tool_route(result, action, action_guidance(entry, action))
                binding = _equivalent_binding(route, read)
                if binding and read.allowed(unavailable_parents):
                    add_semantic_evidence(binding, key)
                    available_equivalents[key] = binding
        if structured:
            selected_actions = []
            for action in result["actions"] or [None]:
                guidance = action_guidance(entry, action)
                if not guidance or not guidance.observation:
                    continue
                route = _tool_route(result, action, guidance)
                if request.intent and route["intent"] != request.intent:
                    continue
                item = (0, name, action or "", route)
                observations.append(item)
                if not _observation_matches(request, guidance.observation):
                    continue
                routes.append(item)
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
        standalone_key = ("tool", name, "")
        lexical = _lexical_score(terms, query_words, bool(semantic))
        score = lexical + weights.get(standalone_key, 0.0)
        score += 1000 if query == _normalized(name) else 0
        if query in _normalized(name):
            score += 100
        elif semantic and len(query_words) > 1 and query_words == _words(name):
            # Corroborate a hybrid match when the request reorders the complete tool name.
            score += lexical
        action_matches = []
        entry_terms = set(terms)
        for action in actions:
            guidance = action_guidance(entry, action)
            action_terms = _term_evidence(
                query_words, (action, 9), (guidance.purpose if guidance else "", 6)
            )
            key = ("tool", name, action)
            if not action_terms and key not in semantic:
                continue
            entry_terms.update(action_terms)
            # A repeated domain word in tool/title/action cannot crowd out the words
            # that distinguish an operation, such as performing or observing an input.
            operation_terms = terms.copy()
            for word, strength in action_terms.items():
                operation_terms[word] = max(strength, terms.get(word, 0))
            weight = _lexical_score(operation_terms, query_words, bool(semantic)) - lexical
            weight += weights.get(key, 0.0)
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
            route = _tool_route(result, action, guidance)
            add_semantic_evidence(route, ("tool", name, action))
            ranked.append((-(weight + score), name, action, route))
        if not action_matches:
            route = _tool_route(result, None, action_guidance(entry, None))
            add_semantic_evidence(route, standalone_key)
            routes.append((-score, name, "", route))
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
            # A declared catalog phrase describes the data this URI provides. Ranking it
            # here also serves its equivalent tool when the client can call only tools.
            alias = (resource.get("kwargs", {}).get("meta") or {}).get(SEARCH_TERMS_META)
            anchor = alias.get("anchor") if isinstance(alias, dict) else None
            phrase = alias.get("phrase") if isinstance(alias, dict) else None
            evidence = _term_evidence(query_words, (resource["name"], 8), (purpose, 6))
            augmented = {}
            if isinstance(anchor, str) and anchor in query_words and isinstance(phrase, str):
                # A domain word anchors the phrase. Generic identifier words such
                # as "items" must not promote the inventory for unrelated tasks.
                augmented = _term_evidence(query_words, (phrase[:256], 9))
            if augmented:
                route["matched_terms"] = sorted(augmented)
                for term, strength in augmented.items():
                    evidence[term] = max(strength, evidence.get(term, 0))
        route["name"] = resource["name"]
        key = _route_key(route)
        if read := _READ_BY_ROUTE.get(key):
            binding = _equivalent_binding(route, read)
            if binding and read.allowed(unavailable_parents):
                add_semantic_evidence(binding, key)
                available_equivalents[key] = binding
        if structured:
            if route.get("observation") and (not request.intent or request.intent == "observe"):
                item = (0, identifier, "", route)
                observations.append(item)
                if _observation_matches(request, route["observation"]):
                    routes.append(item)
            continue
        occurrences.update(evidence.keys())
        weight = _lexical_score(evidence, query_words, bool(semantic))
        weight += weights.get(key, 0.0)
        add_semantic_evidence(route, key)
        if exact_name or query == _normalized(identifier):
            weight += 1000
        if weight:
            routes.append((-weight, identifier, "", route))
    matched_before_filters = bool(matches or routes)
    matches.sort(key=lambda item: (item[0], item[1] not in confirmed_tools, item[1]))
    if request.intent:
        routes = [item for item in routes if item[3].get("intent") == request.intent]
    routes.sort(key=route_order)
    selected_routes = [item[3] for item in routes[: request.limit]]
    groups = _group_routes(
        request.route_surfaces,
        sorted([*routes, *additional_routes], key=route_order),
        available_equivalents,
        unavailable_parents,
        confirmed_tools,
    )
    # Words shared by more than a quarter of this inventory cannot independently
    # corroborate a route. This derives common vocabulary from the catalog itself.
    documents = len(entries) + len(available_resources) + len(available_templates)
    distinctive = {term for term, count in occurrences.items() if count <= max(1, documents // 4)}
    unique_terms = {term for term, count in occurrences.items() if count == 1}
    basis: MatchBasis = "none"
    if structured and groups:
        basis = "declared_observation"
    elif not structured:
        for route in selected_routes:
            _route_evidence(route, query, query_words, distinctive, unique_terms)
        if groups:
            first = groups[0]
            group_bases = {
                _route_evidence(route, query, query_words, distinctive, unique_terms)
                for route in [first["representative"], *first["alternatives"]]
            }
            basis = next(
                b
                for b in (
                    "exact_identifier",
                    "multiple_terms",
                    "semantic_similarity",
                    "single_term",
                    "incidental",
                )
                if b in group_bases
            )
    if structured:
        next_action = "inspect_candidates" if groups else "browse_catalog"
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
        "matching": "observation" if structured else "hybrid" if model else "lexical",
        **({"semantic_model": model} if model else {}),
        **({"semantic_status": "unavailable"} if model_unavailable else {}),
        **(
            {
                "observation_source": request.observation_source,
                "required_values": request.required_values or [],
            }
            if structured
            else {}
        ),
        **(
            {
                "no_match": _diagnose_miss(
                    request,
                    structured,
                    matched_before_filters,
                    observations,
                    available_equivalents,
                    unavailable_parents,
                )
            }
            if not groups
            else {}
        ),
        "catalog_language": "en",
        **(
            {
                "host_inventory": {
                    "source": "caller_report",
                    "complete": host_evidence.complete,
                    "statuses": {
                        name: "host_advertised"
                        if name in confirmed_tools
                        else "host_filtered"
                        if host_evidence.complete
                        else "host_unknown"
                        for name in host_evidence.candidates
                        if name in (visible_names or ())
                    },
                }
            }
            if host_evidence is not None
            else {}
        ),
        "next_action": next_action,
        "recommended_action": "inspect_candidates"
        if groups
        and basis
        in {
            "multiple_terms",
            "exact_identifier",
            "declared_observation",
            "semantic_similarity",
        }
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
            "to browse classified actions without matching any words. catalog_query is optional. "
            "For another lexical search, supply the action and target in English. Keep names, paths "
            "and requested text verbatim in the eventual call."
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
