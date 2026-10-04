---
name: mcp-source
description: Change the Unity MCP fork's UPM source in a user-selected Unity project, including pinned Git revisions, development branches or a local checkout. Use for package-source switches, not general package installation.
---

# Switch Unity MCP Package Source

Change only the requested project's `Packages/manifest.json` entry for `com.ykh09242.unity-mcp`. This is the Git-distributed **Unity MCP (ykh09242)** fork. It is not the upstream `com.coplaydev.unity-mcp` package, even though assemblies and asset GUIDs remain compatible.

## Resolve The Request And Project

Interpret supplied arguments in the client's supported invocation format; `main`, `beta`, `branch`, `local` and an explicit tag/full SHA are source choices, not permission to update every connected project. If the source or target project is unclear, ask only for that missing choice.

For a connected Editor, discover the available resource reader and `mcpforunity://instances`, then read `mcpforunity://project/info` for the selected instance. Its `data.projectRoot` identifies the project. Use per-request targeting or a verified stateful session default; do not assume a prior selection persists in a sessionless client.

For a repository-only request, use the user-named project or a bounded search within the requested workspace. Do not search or edit unrelated projects. Multiple matches require selection, not automatic bulk modification. A remote Editor's path does not establish local access; use its authorized host workflow or report the limitation.

## Choose The Dependency

| Choice | Dependency value | Meaning |
|---|---|---|
| Initial stable release | `https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.0.0` | Published independent fork release |
| `beta` | `https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#beta` | Authoritative development/default branch, floating |
| `main` | `https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#main` | Explicit branch choice; inherited upstream content, not the fork's stable channel |
| Explicit tag/SHA | Same fork URL with the user's verified revision after `#` | Prefer a full immutable commit for reproducibility |
| `branch` | Verified remote URL with `?path=/MCPForUnity#<branch>` | Existing remote development branch; do not publish/create it implicitly |
| `local` | `file:<absolute-checkout>/MCPForUnity` | Local package checkout, not `Server/` |

For `branch` or `local`, read the selected checkout's Git root/ref/origin and `MCPForUnity/package.json`. Read-only commands such as `git rev-parse --show-toplevel`, `git branch --show-current` and `git remote get-url origin` can establish context. A detached HEAD is not a branch; ask for a revision rather than inventing one. Normalize a GitHub SSH origin only when forming an HTTPS Git URL, preserving its owner/repository.

Check that the selected revision/checkout actually declares the expected UPM identity before switching when its metadata is available. In particular, inherited `main` may declare the upstream identity and cannot be assumed compatible with the new dependency key. Stop and explain a known mismatch; if remote metadata cannot be verified, disclose that uncertainty rather than asserting compatibility.

## Apply A Scoped Manifest Edit

Stable fork example, showing only the relevant dependency:

```json
{"dependencies":{"com.ykh09242.unity-mcp":"https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.0.0"}}
```

Read the manifest as JSON and preserve all unrelated dependency/registry settings. If the fork entry already exists, change its value only. If migrating from upstream at the user's request, remove `com.coplaydev.unity-mcp` and add the fork entry together: both cannot be co-installed because assembly names/GUIDs are retained. An unexpected existing package/source needs clarification, not silent adoption.

For that upstream-to-fork migration, also inspect an existing `testables` array. Replace
upstream Unity MCP entries with `com.ykh09242.unity-mcp`, keeping unrelated entries and
their order; collapse duplicate Unity MCP entries. Do not create `testables` when absent.
For example, this migration preserves the unrelated dependency and testable:

```json
{"before":{"dependencies":{"com.coplaydev.unity-mcp":"https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#beta","com.example.game":"file:../Game"},"testables":["com.example.game","com.coplaydev.unity-mcp","com.ykh09242.unity-mcp"]},"after":{"dependencies":{"com.ykh09242.unity-mcp":"https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.0.0","com.example.game":"file:../Game"},"testables":["com.example.game","com.ykh09242.unity-mcp"]}}
```

Use a native structured/file edit that preserves valid JSON and the user's formatting where practical. Do not hand-edit `packages-lock.json`, package cache contents or ProjectSettings as part of this source switch. A local path must point to the package folder and be accessible to the Unity host; use the platform's UPM file-path form.

The Unity package's `mcpServerSource` independently pins Server to a full Git SHA. Switching the UPM entry does not authorize editing that field, replacing an intentional development override or falling back to PyPI.

## Verify And Report

Reread the manifest, confirm valid JSON and only the intended dependency/source and conditional Unity MCP `testables` migration, then report old/new values and exact project path. A manifest edit is not proof of a completed Unity package resolution. If the Editor is available within the request, inspect Package Manager/installed metadata and console after resolution; otherwise report that check as pending. Do not launch Unity, refresh unrelated projects or retry a failed resolution by changing revisions without authorization.
