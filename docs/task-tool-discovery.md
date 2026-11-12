# Action catalog and task discovery

Use `manage_tools(action="catalog")` to read the complete built-in group index.
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

## Browsing and execution

- The root catalog returns every registered built-in tool name in a complete group
  index, including currently hidden tools. Project custom tools use the existing
  project-scoped custom-tool discovery path.
- `catalog` with `group="GROUP"` returns tool descriptions. With `tool="NAME"` it
  returns action cards and the enclosing tool description. Select one scope per
  call. Pages accept `limit` 1..20 and nonnegative `offset`. Follow `next_offset`
  while more relevant operations remain to be inspected.
- An action card's `invoke` contains the exact tool and action selector. Complete
  it with observed inputs. `describe` with `operation` binds that action in a copy
  of the full schema while retaining conditional constraints. Unknown selections
  fail explicitly and descriptions preserve the registered schema.
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
`matched_query` and declares `matching="lexical"`. Names, source aliases, actions,
descriptions and retained keyword aliases contribute to deterministic ranking.
The optional `catalog_query` supplies translated terms. Browse the complete
catalog when candidate meanings fail to satisfy the requested outcome.

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
