---
id: cli
slug: /guides/cli
title: Use The Local HTTP CLI
sidebar_label: CLI
description: Launch the pinned Editor-control CLI, target requests explicitly, and consume machine-readable output safely.
---

# Use The Local HTTP CLI

`unity-mcp` controls an already-connected Editor through **local HTTP**. It is not the MCP server executable, a stdio client, or the hosted API-key control interface. Start the local HTTP server and Unity bridge first; see [Install And Connect](../getting-started/install.md).

## Run the matching CLI

This beta checkout uses this pinned source. Published stable releases keep their own pins; use the installed package's `mcpServerSource`:

```bash
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/a9d231694c59599040b4c488e7ac42604683dcd7.zip#subdirectory=Server" unity-mcp --help
```

For another installed Unity revision, use its `mcpServerSource`. If `unity-mcp` is already installed from the matching source, the shorter commands below work directly. Otherwise prepend the same `uvx --python ">=3.11" --from` prefix to each command.

## Inspect and select

```bash
unity-mcp --format json status
unity-mcp --format json instance list
unity-mcp --instance "MyGame@a1b2c3d4" --format json scene hierarchy
unity-mcp --instance "MyGame@a1b2c3d4" --format json gameobject find "Player"
```

Replace the example ID with a discovered one. Global options go **before** subcommands. The CLI is stateless: `instance set` cannot persist a target and returns an explanatory failure. Use `--instance` on every command or set `UNITY_MCP_INSTANCE` in the CLI environment.

| Global option | Environment variable | Meaning |
|---|---|---|
| `--host` | `UNITY_MCP_HOST` | HTTP host; default `127.0.0.1`. |
| `--port` | `UNITY_MCP_HTTP_PORT` | Port, 1-65535; default 8080. |
| `--timeout` | `UNITY_MCP_TIMEOUT` | Positive seconds, default 300. |
| `--format` | `UNITY_MCP_FORMAT` | `text`, `json`, or `table`. |
| `--instance` | `UNITY_MCP_INSTANCE` | Target Editor ID. |
| `--verbose` | - | Write command requests and raw responses to stderr; use only with data safe to log. |

Explicit options override corresponding environment settings, then project-context values, then defaults. Health and inventory requests respect `--timeout`, capped at 5 and 10 seconds respectively. The CLI connects directly to loopback addresses and bypasses environment proxies for commands, health checks and inventory. It loads the current local token for authenticated requests. Non-loopback connections need explicit credentials and trusted transport. See [authentication](./security.md#local-http-authentication).

## Recover a command after response loss

Execution receipts are optional and require an operator-enabled private server store.
Inspect availability first; this does not require a connected Editor:

```bash
unity-mcp --format json receipt describe
unity-mcp --instance "MyGame@0123456789abcdef" --format json receipt execute --store-id STORE_ID --idempotency-key create-object-request-0001 manage_gameobject '{"action":"create","name":"OwnedObject"}'
unity-mcp --format json receipt get --store-id STORE_ID --project-hash 0123456789abcdef --idempotency-key create-object-request-0001
```

Replace `STORE_ID` with the returned 32-character store ID and use the complete
16-character project hash. `receipt execute` accepts one native command and the
same inline JSON, `@file`, and `-`/`@-` stdin sources as `raw`. It verifies store
availability, then sends one protected command within the original `--timeout`.
The request key is case-sensitive and must contain 16-128 ASCII letters, digits,
underscores, dots, colons, or hyphens. Use a distinct key for a distinct intended
operation. Receipt options are separate from Unity command parameters.

The CLI never automatically resends or silently falls back to an unprotected
request. If delivery is lost or receipt metadata is missing, it exits nonzero
with the store/project/key needed for lookup. `unknown` is not permission to
replay. An exact duplicate may return an existing observation without executing
again; see [the execution receipt contract](https://github.com/ykh09242/unity-mcp/blob/beta/docs/execution-receipts-ledger.md).
This is not an exactly-once guarantee.

Result retention is off unless `receipt execute --retain-result` is supplied.
Use `receipt get --include-result` to request an available retained result, or
`--receipt-id` instead of the request key. A successful lookup means the record
was read; its `status` still determines the observed outcome. Lookup works after
the Editor disconnects and preserves an explicitly selected project route.
Protection applies only to `receipt execute`, never to other CLI commands or
their hidden preparation/polling calls. Protected `run_tests` starts require an
explicit `source_token` obtained during source preparation.

## Project-local defaults and protection

From a Unity project or its subdirectories, the CLI automatically discovers
`.unity-mcp.json` beside `Assets` and `ProjectSettings`. Discovery stops at the
nearest Unity project, even when that project has no context file; it does not
search home or sibling directories. Create this secret-free file yourself:

```json
{
  "schema_version": "unity-mcp/cli-context@1",
  "host": "127.0.0.1",
  "port": 8080,
  "instance": "0123456789abcdef",
  "timeout": 30,
  "format": "json"
}
```

`schema_version` is required. The remaining fields are optional: `host` must be
loopback, `port` is 1–65535, `timeout` is a positive integer in seconds, `format`
is `text`, `json`, or `table`, and `instance` is a complete 16-character native
hash or `Name@Hash`. Copy the full hash from `unity-mcp instance list`; the sample
is a placeholder. Unknown or duplicate fields are rejected. Omitted fields keep
the CLI defaults, including the 300-second command timeout. Never store tokens,
credential paths, secrets, or executable settings here. The existing
[CLI usage guide and context schema](https://github.com/ykh09242/unity-mcp/blob/beta/Server/src/cli/CLI_USAGE_GUIDE.md#project-local-cli-context)
describe the full contract.

Precedence is **explicit flag → environment variable → context file → default**,
including explicit flags equal to a default. The containing Unity root becomes
the expected project. Commands require a complete explicit instance hash and use
the guarded local route, which checks the selected Editor's registered root and
hash. A mismatch or an older server refuses the request; the CLI never falls back
to an unguarded mutation. Changing `--instance` alone retains the expected root.
This cooperative registered-path check does not authenticate project contents or
prove that an earlier timed-out mutation was rolled back.

```bash
unity-mcp --project-context /projects/GameB/.unity-mcp.json scene active
unity-mcp --instance fedcba9876543210 --expect-project /projects/GameB scene active
unity-mcp --no-project-context --instance 0123456789abcdef scene active
unity-mcp --format json doctor --config
```

`--project-context FILE` selects a file explicitly; `--no-project-context`
disables automatic discovery. They cannot be combined. `--expect-project ROOT`
sets an explicit expected root independently of file defaults. Use `doctor
--config` to inspect the effective settings and where each value came from.
It reports configuration without dispatching Editor work; discovery and readiness
checks retain their own read-only scopes and do not attest a future mutation.

## Discover exact commands

```bash
unity-mcp --help
unity-mcp scene --help
unity-mcp editor --help
unity-mcp editor tests --help
```

Use help and the [CLI reference](../reference/cli.md) for flags, rather than assuming MCP tool parameters have identical CLI names. MCP tools, CLI commands, and Unity handlers are distinct layers. Additional task examples live in [CLI Examples](./cli-examples.md).

Search by task when the tool name is unknown:

```bash
unity-mcp tool search "render pipeline"
unity-mcp --format json tool search "texture"
```

Default text output includes a short purpose, actions with experimental markers,
package requirements, skill links and a follow-up inspection command preserving
the selected host, port, instance and project guard. Long lists are explicitly
truncated; JSON retains the full discovery response. Discovery reports readiness
as unobserved: a matching tool does not establish that the selected Editor has its
required package or is ready to execute it.

## Read and edit the same script version

Use `unity-mcp --format json script read Assets/Scripts/Player.cs --start-line 10 --line-count 5` to read a line range. The matching Editor returns the SHA-256 of the full source in `data.sha256`. Pass that value to `script edit` with `--expected-sha`. If another writer changes the script, the edit returns `stale_file` and the caller can read the new version before revising the edits.

## Automate without losing errors

Use `--format json` for one machine-readable stdout document; diagnostics go to stderr. Explicit Unity command failures return nonzero exit status while retaining native diagnostic data. Check both exit status and the returned payload, including failed child commands/jobs.

Save returned job IDs and poll them; do not rerun a mutation when a timeout may have occurred after dispatch. Budget errors call for smaller requests, not disabled safeguards. High-impact commands still need Editor consent. Never log authentication headers or private input files into shared job artifacts.

## Explicit readiness checks

`unity-mcp status` keeps its health-only exit contract: a healthy server can exit
successfully even when instance discovery fails or no Editor is connected. For
automation, use `unity-mcp --format json status --ready`. This exits nonzero if
health, authentication, discovery, or unique target selection fails. The JSON and
table output separate `server_reachable`, `authentication` (`accepted`, `rejected`,
or `unknown`), `instance_discovery`, and `target_ready`.

With multiple Editors, supply `--instance` before `status`. Readiness uses the local
command route's selection rules: the complete inventory `hash` matches first and
must identify exactly one Editor. For `Name@Hash`, the name before the last `@`
and the complete hash must both match that Editor. Supply just the complete hash
when selection should be independent of the project name. If there is no exact
hash match, a selector without `@` can match one exact project name. Duplicate
exact hash matches or duplicate fallback name matches are ambiguous. Hash prefixes
and transport `session_id` values are not selectors, and whitespace is preserved.
Copy the complete `hash`
from `instance list`; project names containing `@` should use that hash. Without a
selector (or with an empty CLI selector), exactly one discovered Editor is
required. This read-only check verifies discovery
availability; it does not dispatch a command or guarantee that compilation or a
subsequent Editor operation will succeed. HTTP errors other than 401/403 leave
authentication unknown. Rejected response bodies and credentials are not printed.
