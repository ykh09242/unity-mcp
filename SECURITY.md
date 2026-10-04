# Security Policy

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

This fork is maintained by [ykh09242](https://github.com/ykh09242). No private reporting email or response-time guarantee has been established for the fork. If GitHub private vulnerability reporting is enabled on [the fork's Security page](https://github.com/ykh09242/unity-mcp/security), use it with:

- A clear description of the issue
- Steps to reproduce or a proof-of-concept
- The version of MCP for Unity affected (UPM package + Python server)
- Your OS, Unity Editor version, and MCP client
- Optional: a suggested fix

If private reporting is unavailable, contact the maintainer through a contact method they publish on their GitHub profile before sharing sensitive details. Coplay's upstream security contact does not represent this fork.

## Supported Versions

| Version | Supported |
|---------|-----------|
| stable fork `1.0.0` | Published baseline; no support SLA |
| moving development branch (`beta`) | Development focus; not an immutable release |
| inherited upstream tags / older commits | Upstream history; no fork support guarantee |

## Network Defaults (Safe by Default)

MCP for Unity is intentionally fail-closed:

- **HTTP Local** binds to loopback only by default (`127.0.0.1`, `localhost`, `::1`). LAN bind (`0.0.0.0`, `::`) requires explicit opt-in via **Allow LAN Bind (HTTP Local)** in Advanced Settings.
- Local HTTP/WebSocket control requires a fresh per-launch `X-Unity-MCP-Token`; only local `GET /health` is public. Browser-origin requests are rejected. Keep token files and generated client configuration private; reconfigure HTTP MCP clients after restarting the server. See [Security And Consent](website/docs/guides/security.md).
- **HTTP Remote** requires `https://` by default. Plaintext `http://` for remote endpoints requires explicit opt-in via **Allow Insecure Remote HTTP**.
- Remote-hosted mode requires API key authentication on every MCP HTTP request, including initialization and catalogs. Only `GET /health` and `GET /api/auth/login-url` are public. Missing, duplicate, invalid, or unverifiable credentials fail closed before protocol dispatch.
- Remote HTTP startup requires an explicit `--http-behind-tls-proxy` deployment assertion. The backend must stay on loopback or an unpublished container network behind HTTPS/WSS; the flag itself does not enable TLS. The remote Compose example publishes only the HTTPS proxy. See [Remote Server Auth](website/docs/guides/remote-server-auth.md).

If you find a way to bypass any of these guards, that qualifies as a security vulnerability and warrants a private report.

## Resource and Data Safeguards

- Hosted plugin catalogs are scoped to the authenticated user. Accepted WebSockets, including sockets awaiting registration, share limits of 32 per user and 256 overall. Registration must be the first message and complete within 10 seconds; each socket registers once. A reconnect needs an available connection slot and replaces the same user's prior project session. Catalogs allow 256 tools / 512 KiB of metadata per plugin.
- Custom-tool polling has a server-owned 600-second deadline covering the initial command, polling commands, and sleeps. At most 16 polling executions are active per selected project, 32 per user, and 256 overall; capacity is retained until the execution exits, including cancellation cleanup.
- Remote-hosted operations cannot use the host-local legacy Unity connection pool, scan host files for external changes, or focus host Unity windows. Script reads, edit hashes, and writes route through the authenticated user's selected plugin session. Host-local `manage_tools sync` is unavailable remotely. Supported text or structural previews do not write files; mixed text/structural preview is rejected. A remote proposal does not establish local native-file applicability.
- `execute_code`, `execute_menu_item`, `manage_script`, `manage_packages`, `manage_build`, and `batch_execute` require explicit enablement in the Editor's tool settings. Previously implicit enabled preferences do not grant this consent. The `manage_script` permission covers all script aliases, including reads, hashes, validation, and previews, as well as asset operations that can introduce or change compiler inputs or plug-ins. Incomplete folder classification requires consent. `AutoRegister` controls registration separately from this permission.
- HTTP request bodies are limited to 64 MiB before parsing, including chunked bodies and missing or misleading size headers. The remote Caddy example applies the same ceiling. Per-command and tool-specific limits still apply within that envelope.
- Plugin WebSocket messages are limited to 32 MiB before JSON decoding, with depth and node-count guards. Command results and final MCP output are independently limited to 32 MiB, 64 levels, and 100,000 nodes. Final MCP accounting includes both text and structured output. HTTP/SSE responses retain their result reservations through queued and blocked delivery; estimated retained-result budgets are 256 MiB per plugin session, 512 MiB per user, and 1 GiB globally. These bounds do not guarantee total process memory usage.
- API-key validation endpoints require HTTPS and do not follow redirects. Client configuration generation applies the same remote TLS policy as the Unity connection.
- Script edits, UI/shader/texture/prefab mutation destinations, and generated asset files stay inside `Assets/` through shared path containment checks. Rooted remainders, traversal, linked files, linked directories, and dangling links are rejected. Screenshot filenames cannot contain directories; their output folders must stay inside the project and reject symbolic links and junctions. These checks assume local processes do not concurrently replace checked paths; they are not a sandbox against a hostile process with filesystem access.
- Asset reads and object references validate direct paths, GUID-resolved paths, and persistent object IDs through the same containment checks. Where a tool explicitly permits package references, the path must resolve inside a registered Unity package root with no linked components. Built-in references, where permitted, are limited to Unity's known resource paths. Mutation destinations remain under `Assets/`.
- Caller-supplied Python regexes have a 0.1-second matching budget, 2,048-character patterns, 2,000,000-character input, and 10,000 matches. Script regex edits additionally share a cooperative one-second deadline and 16,000,000-character work budget across matching, selection, and replacement; brace context is tokenized once. At most two workers are admitted, and cancellation retains capacity until the worker exits. Script edit requests allow at most 32 edits. Search responses include at most 1,000 results, with 2,000-character match/excerpt fields.
- Asset search, UI asset listing, and physics validation reject page sizes outside 1–1,000. Asset preview searches allow at most 32 items, with previews scaled to 256 pixels per edge, 256 KiB PNG data per image, and 4 MiB of aggregate base64 data. Page offsets use overflow-safe arithmetic, and UI metadata and formatted physics warnings are retained only for the selected page.
- Provider API responses are limited to 32 MiB and artifact downloads to 512 MiB, including responses with missing or misleading size headers. ZIP/ZIP64 metadata is preflighted before `ZipArchive` materializes entries: at most 4,096 entries and 16 MiB of central-directory metadata, with declared and actual counts checked. Archives also enforce 512 MiB per entry, 2 GiB total uncompressed data, and a 200:1 compression ratio. Extraction checks actual streamed bytes and removes newly written files on failure.
- Screenshot and UI capture dimensions are limited to 8,192 per side and 33,554,432 pixels after supersampling (1–4). Orbit captures allow at most 128 shots, 16 elevations, and 268,435,456 rendered pixels per batch. Retained tiles and contact sheets each have a 33,554,432-pixel budget; sheets also respect the side limit. Tool-owned UI render targets retain at most eight panels and 67,108,864 pixels. These are allocation/work budgets, not guarantees about total GPU memory usage.
- ScriptableObject patches preflight array growth before asset creation or serialized mutation: at most 1,048,576 elements per growing array and 2,097,152 added serialized elements/fields per request. Compound-element copies additionally have character, inspection-work, and nesting limits. Existing larger arrays can still be read, edited without growth, or shrunk.
- Skill synchronization bounds branch metadata to 1 MiB, recursive tree responses to 32 MiB / 100,000 entries, and selected files to 4,096. Each downloaded file is limited to 64 MiB, with at most 256 MiB of staged file data per sync. Declared sizes and actual streamed bytes are checked before writing downloaded files, and the download deadline includes response-body reads.
- Skill synchronization rejects links and junctions in managed/adopted roots, ownership markers, and file paths before planning, downloading, and applying changes. Local traversal is bounded to 8,192 entries and 64 directory levels. Final write/delete paths are checked again after downloads; the same local path-replacement assumption above applies.
- Local external-change scanning uses the server-selected instance and registered project root, with at most 32 cached states, a 15-minute idle expiry, 1 MiB manifests, and 256 package roots. Each scan has a 20,000-entry and cooperative 250 ms work budget. Scans do not overlap, including while a cancelled worker is still exiting.
- `unity_docs` accepts at most eight lookup queries, 256 characters per query/field, and 2,048 total input characters. Responses are capped at 2 MiB each, with a 30-second request deadline. At most four requests and eight outbound workers are active globally, with two fetches per request. Cancellation does not release worker capacity until the underlying blocking fetch exits.
- Optional command recording writes only execution metadata to `Library/MCPForUnity/Logs/`. Parameters, action values, and error text are excluded. Older logs under `Assets/UnityMCP/Log/` are not modified automatically.
- Server execution logs record lifecycle metadata, duration, and exception type without complete MCP arguments, results, or exception payloads. Custom-tool responses and malformed plugin message bodies are excluded from operational logs.
- Plugin registration rejects control characters and line separators in project names and hashes; registration logs escape fields. Optional telemetry accepts action labels only from trusted built-in tool schemas or fixed action sets. Unknown, free-form, and custom-tool action values are omitted before queueing and again before sending.
- Batch commands enforce the same enabled-resource and enabled-tool settings as individual calls. Nested batches are rejected during preflight, before any command executes, so the configured command ceiling applies to the whole batch.

## What Counts as a Security Issue

- Remote code execution via crafted MCP messages
- Auth bypass on remote-hosted server
- Filesystem read/write outside the intended Unity project root
- Network requests that escape the configured allow-list
- Credential or API-key leakage in logs, telemetry, or error responses

## What Doesn't Count

- Tool actions that intentionally modify the Unity project (that's the product)
- Issues that require an attacker to already have shell access to the host
- Vulnerabilities in third-party dependencies — please report those upstream first; we'll bump our pins after the upstream fix lands

## Disclosure Timeline

Disclosure and reporter credit will be coordinated with the reporter when a fork fix is available. There is no established fork publication schedule.
