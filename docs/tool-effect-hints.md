# Focused tool effect hints

The authoritative capability catalog carries four independently reviewed MCP hints. The Python adapter publishes them through `tools/list`; the generated native catalog and reference pages use the same descriptions, which the Editor already displays.

| Family | Read-only | Destructive | Idempotent | Open world |
| --- | --- | --- | --- | --- |
| Physics queries, bundle inspection, asset reference diagnostics | true | false | false | false |
| Physics diagnostics with optional passive contact observation | false | true | false | false |
| Other focused tools, including mixed read/write tools | false | true | false | true |

The last row is deliberately conservative. `prefab_inspect` can load prefab contents and run component callbacks; scene reference diagnostics can load ScriptableObjects and run `OnEnable`; visual-tree inspection calls virtual getters on custom UI elements. These callbacks are not suppressed, so those complete capabilities retain conservative hints even though their intended operation is inspection. `scene_inspect` can frame the Scene view; `scene_validate` can repair a scene; rendering statistics can change the debug view. Capture tools can write files. Other actions may invoke asset importers or user component callbacks. `asset_inspect` includes preview generation and retains the conservative classification pending a separate review of those effects. A future capability inherits conservative hints until all its actions and options have been reviewed.

Physics diagnostics may add or remove a passive contact watch. It does not advance simulation or change contact-generation settings, but its observer lifecycle is an effect. Query results may change as the project or simulation changes. The idempotent hint is not asserted for any focused capability and does not authorize a retry; it is only meaningful for non-read-only tools under the [MCP annotation specification](https://modelcontextprotocol.io/specification/2026-07-28/schema#toolannotations).

These hints are discovery metadata. Project permissions, explicit consent, session visibility, native enablement and transport routing continue to make their own decisions. The classifier is never used for permission grants, bypasses or automatic replay. External-provider tools retain their existing independent annotations and open-world default.

Run `python tools/generate_tool_capabilities.py` and `python tools/generate_docs_reference.py` after changing catalog descriptions. Real modern and legacy SDK tests check representative query, mixed, mutation and provider annotations alongside the existing permission and visibility contracts.
