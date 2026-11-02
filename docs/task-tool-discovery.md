# Task-purpose discovery and support status

Use `manage_tools(action="search", query="URP renderer diagnostics", limit=5)` to
find built-in tools by task purpose. Search uses local names, focused-capability
source aliases, descriptions, actions and a small curated English/Korean keyword
map. It has no hosted search or language-model dependency. Queries contain
1..512 characters including a non-whitespace character; limits are integers
1..20. Ordering is deterministic, with exact names ranked first and name-based
tie breaking. An unmatched valid query returns an empty result page.

Search never activates tools, creates selection state or changes authorization.
Its `advertised` field comes from the same final context availability filter as
`tools/list`, including authenticated Unity availability. A false value does not
identify which selection, package or authorization constraint hid the tool.
Results include the group, conservative whole-tool effect hints, support maturity,
known static requirements and optional official skill links. `readiness` remains
`unobserved`: search does not probe Unity, install packages or validate a project.
Use the tool's actual availability or diagnostics action where provided, and
refresh the client's inventory after an independently authorized selection change.

The CLI can search before a server or Unity Editor is running:

```sh
unity-mcp --format json tool search "URP renderer diagnostics" --limit 5
unity-mcp --format json tool search "collision" --limit 3
```

CLI search uses the same matcher and the installed package's local declarations.
Its `scope` is `local_catalog` and `advertised` is null, because an offline catalog
cannot observe an MCP connection's callable inventory. MCP search uses
`scope="mcp_context"`. Neither scope includes project custom-tool declarations.

`maturity` is separate from enabled state, readiness, permissions and effect hints:

- `stable`: the compatibility default for existing declarations.
- `experimental`: a newly introduced contract that may evolve as support develops.
- `deprecated`: a retained contract for which callers should seek a replacement;
  this marker alone does not disable or remove it.

Mixed tools can expose `action_maturity` overrides in search. In particular,
`pipeline_get_diagnostics` is experimental while the existing render-pipeline tool
retains its stable default. Project Auditor is experimental. These markers never
admit a call or imply a package is installed.

Official links point to exact `SKILL.md` files in
[Unity-Technologies/skills at the reviewed revision](https://github.com/Unity-Technologies/skills/tree/089bedebab64e9e75943b19a364c7aec2162a7e7).
The maintained mapping covers physics, URP volumes and renderer-feature review,
UI Toolkit/uGUI, audio routing, localization, package management and Project
Auditor. A linked skill's prerequisites describe that guidance, not a claim about
this tool's implementation or observed project readiness.

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
