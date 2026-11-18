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
`values` lists selected fields relative to a successful result's `data` object.
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
