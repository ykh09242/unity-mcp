# Connection, Routing And Setup

## Capability Discovery

Use the client's tools/resources/template inventory; do not invent prefixed names such as a particular client's `mcp__...` binding. The names below identify server capabilities, not names to assume are already callable.

If a tool is absent, check its group and the selected Editor's enabled tools. When advertised:

```json
{"tool":"manage_tools","params":{"action":"list_groups"}}
```

Check `list_groups.discovery.selection_supported` before changing selection. Activate only the group or tool needed for the request when supported, then refresh the client's actual callable inventory:

```json
{"tool":"manage_tools","params":{"action":"activate","group":"asset_gen"}}
```

Modern selection requires a supported project-bound stdio connection or a verified SDK principal and resolved project. The existing HTTP API-key layer does not supply that principal. Legacy selection follows the connection and is lost on reconnect. Follow the returned discovery reason rather than inferring support from the transport name.

Successful activation changes the server selection; `client_inventory_status: "unconfirmed"` means the host's current-turn call list has not been verified. Do not invent a function binding or repeatedly activate a tool after the server has accepted it. For fixed-inventory clients, choose **Tools > Advanced connection options > Startup tools > Full**, restart the local server (or regenerate stdio configuration), then reconnect after enabling the desired Editor tools. Manual servers support `--tool-profile full` or `UNITY_MCP_TOOL_PROFILE=full`. The setting does not override a remote server, Editor availability, consent or optional-package requirements.

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

Project custom tools may be advertised directly or through `execute_custom_tool`. If available, inspect `mcpforunity://custom-tools` and use the declared custom tool's actual name/parameter schema. Do not dispatch hidden built-in handlers through this tool or a batch: doing so does not reproduce the built-in wrapper's argument mapping or client binding. Hosted mode does not expose arbitrary project custom tools. A returned tool description or an example in this skill does not establish a callable client function.

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

## Concurrent Connections

Keep the intended Editor explicit when parallel agents share a connection. Supported servers and compatible native packages automatically mediate calls against that bound instance; coordination never selects a different Editor to avoid waiting. Older packages retain conservative sequencing with reduced guarantees, rather than inheriting native cross-process protection from the server alone. Inspect advertised coordination support and returned readiness/recovery guidance before relying on it; do not infer support from a version-looking string or a tool name.

`legacy_command_outcome_unknown` means an identical unbound legacy request still has a disconnected owner. Inspect the existing operation and resulting state before choosing recovery; this response does not prove the earlier work failed or authorize replay. A new connection must not be used to take over another client's result.

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

The Editor skill installer mirrors this repository's `.claude/skills/unity-mcp-skill/` subtree at the package's pinned revision into the chosen client's skill directory. The root `unity-mcp-skill/` copy is maintained identically for manual distribution. Sync preserves local edits and files whose baseline ownership is unknown. For a reported conflict, back up and compare the named file with the exact resolved source, merge the intended changes, then preview again; a no-change result does not mean those files were updated. Sync does not authorize other global configuration changes.
