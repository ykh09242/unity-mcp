# Action catalog and task discovery

Use `manage_tools(action="catalog")` to read the group index and the entry for
readable Unity state. Follow `resources` for fixed URIs or `resource_templates`
for parameterized reads. Both branches have their own pagination. Template rows
preserve the original `uri_template` and list required and optional parameters.
For example, after observing a GameObject instance ID in the selected scene,
expand `mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}`
with that ID. Read the expanded URI. A continuation uses the returned cursor
and the same page size. Component or hierarchy changes can invalidate live IDs.
The calling agent interprets the user's request in its original language, selects
relevant groups/tools, compares action purposes and effects, and obtains the full
schema with `manage_tools(action="describe", tool="NAME", operation="ACTION")`.
Actionless tools use the tool name alone. The semantic selection runs in the
calling agent and needs no additional server-side model, embedding service or key.

Use the read-only MCP tool `search_unity_tools` for task search. It accepts the same
search fields as `manage_tools(action="search")` and requires read access. Discovery
leaves tool selection and Unity state unchanged.

`inspect_editor_state` reads the same selected snapshot as
`mcpforunity://editor/state`. Check freshness and the requested scene or
compilation field. `inspect_ugui_hit_order(target, screen_position)` binds the
strict read of `manage_ugui.diagnose_raycast` and requires its parent selection
and read grant. The fixed tool has no callback or input switches. Its native
guard checks the installed official uGUI assembly, required API signatures,
initialized registries, registered participants, existing renderers and safe
filters. An unsupported query establishes no hit
order. `hits_total=0` is an empty order only when the native query was observed.

A host that knows the complete callable tool names for its current connection
can pass them as `host_tool_names` to search. Use exact MCP names after any host
filter, without connection prefixes. The list is limited to 512 names of 1..128
characters and only restricts tools. An empty list permits only resource routes.
Omit it if the host inventory is unknown or incomplete.

To investigate one missing tool, read `mcpforunity://doctor` for the selected
target's observed package and running-server source comparison. On the **same
connection and target**, call `manage_tools(action="inspect_inventory", tool=NAME,
host_tool_names=[...])` with that host's own complete callable list. The result
distinguishes `server_unavailable`, `host_filtered`, `host_advertised`, and
`host_unknown`. Server absence does not reveal whether a declaration is hidden,
denied, or missing. This caller-reported comparison cannot attest another host.
`unity-mcp --instance PROJECT@HASH doctor --inspect-tool NAME` checks source
and the actual SDK tools/list in the same diagnostic connection. An incomplete
list produces `received: null`. A desktop host must inspect its own connection.

## One catalog projection

`Server/src/services/registry/action_catalog.py` projects registered tool signatures,
focused `CAPABILITIES`, existing `ACTION_RULES`, annotations and discovery guidance
into a shared catalog. Tool registration owns action membership and schemas.
Action contracts own their existing required/forbidden inputs. Catalog guidance
adds intent descriptions for ambiguous routes and preserves conservative effects
for operations with callbacks or mixed behavior.

Catalog browsing, exact descriptions, lexical search metadata and activation
readiness lookups consume this projection. New registered actions are included
automatically. Explicit intent guidance must name existing actions and parameters,
which the catalog tests check. An action with `purpose_source="identifier"` uses
its name as a short label and the enclosing tool description for context.
`effects_scope="tool"` indicates conservative tool-wide effects. Known required
parameters on cards supplement conditional requirements in the full live schema.

For reviewed observations, `observation.source` names the origin of the data.
`values` lists stable evidence IDs. Each ID names a field relative to a successful
result's `data` object unless `wire_paths` maps it to a route-specific field.
Read `conditions` before claiming evidence. Tool routes include fixed `arguments`
and input names the caller must supply in `required_parameters`.
`observation.predicates` tests the returned evidence independently of those
invocation prerequisites. Evaluate `all` clauses and the requested value's
`by_value` clauses on one successful reply's `data` object. Without a
value-specific clause, its declared wire path must exist and be non-null.
Python consumers can call
`services.registry.observation_predicates.evaluate_observation(observation, reply, required_values)`
to receive a boolean for each requested value. A fresh boolean `false` is a
valid compilation activity observation. A stale snapshot cannot establish it.
An older Editor can answer a direct readiness check without a native timestamp.
Its reply can establish that the Editor responded. The server returns
`observed_at_unix_ms=0`, `staleness.is_stale=true` and `staleness.age_ms=null`
for the undated scene and compilation fields. A cached copy also leaves readiness
unverified. Starting a test run requires a dated, stable compilation state.

Paths are dot-separated object keys. `equals` uses exact type and value,
`one_of` checks literal strings, and `type` checks the specified JSON type or
format. `hex32` is a 32-character lowercase hexadecimal identifier.
`single_item_equals` requires a one-element integer array equal to the integer
at `other_path`. `timestamp_lte` compares two nonnegative integer timestamps.
`ordered_hits` checks at most 128 rows, consecutive integer ranks starting
at zero, the total, and the truncation flag. A truncated positive result
establishes the returned prefix only. A successful empty query has zero total
and no truncation. A refused or unattempted query supplies no order.
Earlier declarations that specify conditions only in prose require manual
interpretation. The evaluator leaves their conditional facts unconfirmed.

`unavailable` names evidence this operation cannot supply. Catalog cards, exact
descriptions and matching search routes expose the same observation record.
Tools-only clients can also inspect `_meta["io.github.ykh09242.unity-mcp/action-observations"]`
in `tools/list`, keyed by action. Corresponding resource and template listings
carry the record under `_meta["io.github.ykh09242.unity-mcp/observation"]`.
The tool description names the direct action for clients that hide metadata.

Tools-only clients can select the fixed read-only tools `get_game_view_size` and
`inspect_shader_graph` directly. Each advertises the singular observation record
in its tool metadata. These tools require the corresponding parent in the same
Editor and client selection. The graph tool takes a required `path` and optional
`page_size` and `page_number`. Neither tool accepts an action selector.

| Requested observation | Operation | Source and scope |
| --- | --- | --- |
| Pointer hit order | `inspect_ugui_hit_order` or `manage_ugui.diagnose_raycast` | `editor_live`, `pointer_hit_order`. The mixed action binds `allow_custom_filters=false` and `strict_read=true`. Supply a scene RectTransform and bottom-left Game View pixel position. Check the observed policy, ordered hits and truncation. |
| Active scene name and path | `scene_inspect.get_active`, `manage_scene.get_active` or `mcpforunity://editor/state` | `editor_live`, `active_scene.name` and `active_scene.path`. Use the route's wire paths. For a snapshot, check staleness. |
| Current compilation activity | `inspect_editor_state` or `mcpforunity://editor/state` | `editor_live`, `compilation.is_compiling`. Requires a fresh snapshot with a boolean value. |
| Attributed compile outcome | `get_compile_job` | `compile_job`, `compile_job.outcome`. Supply the existing `compile_id`. Check the returned outcome for a finished, attributed result. |
| Current Game View pixels and preset | `manage_editor.get_game_view_size` or `mcpforunity://editor/game-view-size` | `editor_live`. Use `render_size.width` and `render_size.height` for pixels. `current_size` identifies the preset, which may be an aspect ratio. An existing Game View is required. |
| Saved Shader Graph structure | `manage_shader.inspect_graph` or the `shader_graph_structure` template | `asset_file`. The selected Editor reads the named file. Counts cover the whole graph. Node types, edge endpoints and property summaries are paged. |
| Shader source text | `manage_shader.read` | `asset_file`. Reads one source page from a named `.shader` file. |
| Imported shader passes and compiler messages | `manage_shader.diagnostics` | `imported_asset`. Reads imported pass information and Unity's cached compiler observations. |

If the request specifically requires unsaved Shader Graph edits, report that the
graph inspection action and template cannot observe the open window's in-memory
model. A disk read, including one performed by the Editor, supplies only the saved
version. Inspect that version when it satisfies the requested evidence.

Intent guidance is targeted at routes where the action name obscures different
outcomes. Build cards distinguish observing a non-profile `plan`, repeating its
inputs with `expected_plan_sha256` for `build_from_plan`, and an unguarded `build`.
Mixed actions describe their input-dependent effects: build settings and scene
lists can read or write; output cleanup previews unless `dry_run=false`.
Prefab cards distinguish callback-bearing `preview_only` inspection from a
retained root-edit preview and its one-attempt apply token. Scene field and
hierarchy previews identify their supported targets and apply alternatives.
Asset reference previews identify bounded scan coverage and the optional
`reference_revision` guard on move/delete. Shared focused and legacy routes use
the same intent guidance. These cards do not grant permission or replace the
exact schema, and preview wording alone does not imply a read-only effects hint.

## Browsing and execution

- The live catalog includes selectable built-ins and tools advertised for this
  request, including server utilities. The exact description uses the currently
  exposed schema when available. The resource branch lists metadata and never
  reads the resource. Offline discovery uses local declarations.
- `catalog` with `group="GROUP"` returns tool descriptions. With `tool="NAME"` it
  returns action cards and the enclosing tool description. Select one scope per
  call. Pages accept `limit` 1..20 and nonnegative `offset`. Follow `next_offset`
  while more relevant operations remain to be inspected.
- An action card's `invoke` contains the exact tool and action selector. Complete
  it with observed inputs. `describe` with `operation` binds that action in a copy
  of the full schema while retaining conditional constraints. Unknown selections
  fail explicitly and descriptions preserve the registered schema.
- Choose the action that can produce the requested evidence. A direct uGUI event
  tests a target's handler. A pointer hit-order query inspects occlusion. Raw
  mouse or touch simulation follows the supported input path and needs a separate
  observation of the resulting game state. When delegating, pass the selected
  Editor, observations already made and the evidence still needed.
- `advertised` reports visibility in this MCP context. `readiness="unobserved"`
  leaves package availability, project state and permission checks to the existing
  runtime workflow. Catalog requests perform local reads.
- `tool catalog`, `tool catalog --group GROUP` and `tool catalog --tool NAME` expose
  the same offline catalog. CLI pages support `--limit` and `--offset` and always
  emit complete JSON. `tool describe NAME` inspects the actual connection schema.

## First search for an observation

Interpret the user's request in its own language and select `observation_source`
or `required_values` from the advertised `search_unity_tools` schema.
Use `editor_live` for live Editor state, `asset_file` for saved contents and
`imported_asset` for Unity's import results. For example, a request for current
Game View dimensions maps to this first call:

```json
{"observation_source":"editor_live","required_values":["render_size.width","render_size.height"],"route_surfaces":"tools"}
```

Every returned route declares the selected origin and all requested fields.
A saved graph search can require `nodeCount`, `edgeCount` and `nodes` together.
Requiring `unsaved_graph_structure` yields no match in the current catalog.
The optional original `query` is retained as context. Evidence selection is
independent of its wording and of `catalog_query`. Structured responses report
`matching="observation"` and `matched_query=null`. In a live request, tool
matches use the currently advertised inventory. A listed resource or template
still requires its normal selection, permissions and successful read. Values
describe potential evidence. For example, a shader source read supplies either
text or base64 data according to the requested encoding. Requiring both
representations in one call yields no match. Inspect the returned values after a read.

Offline CLI equivalent:

```sh
unity-mcp tool search --observation-source editor_live --required-value render_size.width --required-value render_size.height --route-surfaces tools
```

The calling agent chooses the fields. CLI and SDK tests exercise that boundary
with the same field selections across request languages.

## When no route supplies the evidence

An empty `route_groups` includes `no_match.reason` and a bounded list of
`alternatives`. These alternatives describe what the available route can actually
return. Their source, observed fields, required inputs and unobserved readiness
remain explicit. They do not fulfill the original request.

| Search selectors | Reason | Interpretation |
| --- | --- | --- |
| `editor_live`, `unsaved_graph_structure` with the saved graph reader visible | `explicitly_unavailable` | The saved graph reader explicitly excludes unsaved edits. Its `asset_file` structure is a possible separate observation. |
| `editor_live`, `nodeCount` with only saved-file graph counts declared | `wrong_source` | The available counts describe the saved asset. |
| `contents` and `encodedContents` | `incompatible_combination` | The source reader supports one encoding per invocation. |
| A valid evidence ID with no applicable declaration | `undeclared_evidence` | The current inventory cannot establish support. |
| Unmatched catalog text | `lexical_miss` | Text matching did not establish support or its absence. |
| A match excluded by intent, invocation surface or visibility | `no_visible_route` | The current selection has no eligible route group. |

Alternatives use the same registration, advertisement, intent and surface scope.
`limit` applies after grouping equivalent reads. JSON includes
`undeclared_values`, `excluded_values` and `alternatives_truncated`.
The text CLI prints each alternative's source, actual values and binding.

## Distinct route choices

Read `route_groups` in structured and lexical search. Each group contains a
`representative` and any `alternatives` usable through the requested surfaces.
Choose `route_surfaces="tools"` for clients that can only call tools,
`"resources"` for fixed URIs and resource templates, or `"both"` for both
surfaces. The default is `"both"`. The strongest matching route ranks the
group. Within the group, prefer the advertised dedicated read-only tool,
then the bound operation on its parent tool, then the resource.

Search groups the declared equivalent Game View and saved Shader Graph reads
before applying `limit`. `total_route_groups` and `route_groups_truncated`
describe the bounded group page. A live search omits unadvertised tools from
groups and omits an equivalent resource when its registered parent tool is
known to be unavailable. Searches use catalog metadata and do not test or
change the selected Editor. All routes retain their normal admission checks.

Since MCP 2025-06-18, a search result also includes a `resource_link` content
block for each distinct advertised, fixed URI in the returned groups. A client
can open that URI with `resources/read`. For example, the camera inventory
reads the live scene. With `route_surfaces="tools"`, use the representative
tool binding. Unbound templates stay in the JSON until the client supplies
their arguments. The text and structured JSON retain their existing shape.

Use the binding on the chosen route. For Shader Graph, the tool takes a
full `Assets/*.shadergraph` path as `path`. The resource template uses the
same path encoded as one URI segment in `encoded_path`. Both take
`page_size` and `page_number`, and both return whole-graph counts alongside
paged collections. The mixed tool also takes the bound `operation` as its
`action` argument. Game View size needs an existing Game View. Each
observation is scoped to the selected Editor.

The legacy `routes`, `results`, counts and ranking are independent of the
group surface selector. Existing clients can continue consuming those fields.

## Lexical compatibility

`manage_tools(action="search", query="original task", catalog_query="English task terms")`
and `tool search QUERY --catalog-query "English task terms"` remain available for
lexical shortcuts and existing callers. Queries accept 1..512 characters and
limits accept integers 1..20. Search retains the original `query`, reports the
`matched_query` and declares `matching="lexical"`. Names, source aliases, actions
and published descriptions contribute to deterministic ranking. Each matched word
contributes once per candidate at the weight of its strongest field.
Every search includes the same navigable group purposes and resource entry for
this inventory, including when a partial lexical match is irrelevant. The
agent can follow those selectors directly in any request language. A miss
or an isolated shared term such as `UI` returns `recommended_action="browse_catalog"`.
`match_basis` describes the first route's lexical evidence and each route lists its
`matched_terms`. Words found in more than a quarter of the current inventory
do not corroborate a route. The remaining overlaps appear in `distinctive_terms`.
An exact identifier is sufficient to inspect a candidate. Word
overlap measures spelling and carries no claim about the user's intended outcome.
The older `next_action` field
retains its tool-only lexical retry hints for existing clients. `routes`
reports bounded lexical matches for operations and resources. The optional `catalog_query` supplies
terms for callers using the lexical shortcut. Browse the complete
catalog when candidate meanings fail to satisfy the requested outcome.

Supply `intent="observe"`, `"interact"` or `"edit"` after interpreting the request.
Search filters `routes` and passes the intent to its group selectors. The legacy
`results` list retains the raw tool matches. `catalog` accepts the same intent
with one tool or tool group and pages through classified actions even with no
matching words. For example, browsing the UI group for `observe` includes the
pointer hit-order diagnostic. Browsing the input tool for `interact` includes
direct event dispatch. Read the selected card's evidence and callback limits.
Unclassified actions remain in ordinary browsing. The CLI exposes both filters
as `--intent`.

Tool and action maturity describe support status independently of visibility
and runtime readiness. Official skill links retain their reviewed immutable
revision and provide supplementary guidance. The catalog and its hints are
independent of the existing admission, permission and retry mechanisms.

## Public MCP contract snapshots

`tools/generate_docs_reference.py` also generates
`Server/tests/fixtures/public_mcp_contracts.json`. An isolated subprocess starts
the actual FastMCP tool registry with a fixed full discovery profile, local
in-process transport, no selected Unity project and no hosted availability
catalog. It observes real `tools/list` for modern `2026-07-28` and legacy protocol
variants. It invokes no Unity tool. The snapshot records advertised input/output
schemas and annotations; schema prose annotations are excluded while machine
property names, defaults and enum values are preserved.

The existing `--check` command checks this snapshot alongside reference pages.
Failures list bounded JSON paths and distinguish added fields from potentially
breaking changes, including removed tools, changed schemas, enum choices and
required inputs. The classification is conservative: review the actual diff
before treating an addition as compatible or a reported change as intentional.
After reviewing a deliberate contract change, regenerate with the existing
generator and commit the generated fixture with the source change.
