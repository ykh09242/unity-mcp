# Connection, Routing And Setup

## Capability Discovery

Use the client's tools/resources/template inventory; do not invent prefixed names such as a particular client's `mcp__...` binding. The names below identify server capabilities, not names to assume are already callable.

If a tool is absent, check its group and the selected Editor's enabled tools. When advertised:

```json
{"tool":"manage_tools","params":{"action":"list_groups"}}
```

Activate only the group needed for the request, then refresh the client inventory:

```json
{"tool":"manage_tools","params":{"action":"activate","group":"asset_gen"}}
```

Sessionless clients cannot persist activate/deactivate/reset operations. Use a stateful MCP handshake or the user's Editor configuration rather than attempting to call hidden handlers through a batch. Group visibility is not consent and does not install optional Unity packages.

An activated group can still be filtered by the selected Unity Editor's per-tool toggle. If
the user wants that capability, have them enable the specific tool in the Editor's MCP Tools
settings; do not reset the entire catalog or edit preferences to bypass consent. For a local
server, the exposed sync action can then refresh visibility from that Editor:

```json
{"tool":"manage_tools","params":{"action":"sync"}}
```

Refresh the client's inventory after the catalog change. Hosted sync is unavailable: Unity
pushes its enabled catalog through the authenticated plugin session, and session group
activation cannot grant a tool absent from that catalog. Resolve the Editor toggle/catalog
or authentication problem instead of repeatedly activating a group.

Project custom tools may be advertised directly or through `execute_custom_tool`. If available, inspect `mcpforunity://custom-tools` and use the tool's actual name/parameter schema. Hosted mode does not expose arbitrary project custom tools. Do not infer a capability solely from an example in this skill.

## Select The Project

Read `mcpforunity://instances` when multiple projects or a stale selection make the target unclear. Prefer the returned full `Name@hash` identifier.

For a stateful session, the selection tool stores a session default:

```json
{"tool":"set_active_instance","params":{"instance":"MyProject@abc123"}}
```

For explicit per-request targeting, an advertised tool schema can include `unity_instance`:

```json
{"tool":"manage_scene","params":{"action":"get_active","unity_instance":"MyProject@abc123"}}
```

For resource requests, the server accepts `_meta.unity_instance`; use the client's supported metadata interface if available. Otherwise select a stateful session default before reading. Sessionless clients must supply request targeting and cannot rely on `set_active_instance` persisting. A selector changes that request, not the session default. In a batch, place `unity_instance` on the outer call only; nested commands cannot select different Editors.

Confirm `data.projectRoot` from `mcpforunity://project/info` against the user's intended project. A remote Editor's path is not evidence that the same path exists or is writable on the agent's host.

## Authentication And Failures

Local HTTP/WebSocket launches use `X-Unity-MCP-Token`; hosted mode uses `X-API-Key`. Configure credentials through the client's private settings and the supported Unity setup, never in examples, reports or source control. Automatic local-token discovery is loopback-only. Preserve authentication instead of disabling it to make a request work.

A connection error during domain reload can be transient. Use readiness retry advice with a bounded wait, then reacquire the inventory/target if needed. Repeated auth failures require a configuration correction; retries, changing projects or switching transport are not a substitute.

## Fork Installation And Migration

Only change package/client setup when the user requests setup or migration. The UPM identity is `com.ykh09242.unity-mcp`, displayed as `Unity MCP (ykh09242)`. The initial stable release is `ykh09242-v1.0.0`:

```text
https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.0.0
```

Do not co-install the upstream `com.coplaydev.unity-mcp` package: assembly names and asset GUIDs are retained. Remove the old dependency and add the fork in the same requested project, preserving other manifest entries.

The package's `mcpServerSource` pins the same-fork Server subdirectory to a full Git commit. Defaults do not use PyPI or a floating branch. Explicit development source overrides remain supported; do not silently replace them. Fork update checks use `beta` for tagged/SHA installs; explicit `#main` remains intentional and can reference inherited upstream content.

The Editor skill installer mirrors this repository's `unity-mcp-skill/` subtree into the chosen client's skill directory. The `.claude/` copy in this checkout is a self-contained repository-local variant, not the installer's source. Sync is a requested installation action and may replace files within its managed skill root; it does not authorize other global configuration changes.
