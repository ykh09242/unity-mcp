# Unity MCP (ykh09242) Server

Python MCP server for the [Unity Editor plugin](../MCPForUnity/README.md). The GitHub-distributed package is `ykh09242-unity-mcp-server`; executable names remain `mcp-for-unity` (server) and `unity-mcp` (local HTTP CLI). Python **3.11+**, FastMCP 4 and MCP SDK 2 are required.

CI runs the server and repository-tool suites on Python **3.11 through 3.15**.
Use uv **0.13.0 or newer** when installing Python 3.15 so the stable interpreter is available.

Python 3.15 checks currently set `PY_KEY_VALUE_DISABLE_BEARTYPE=true`: the upstream
`py-key-value-aio` 0.4.6 protocol annotation triggers Python 3.15's new deprecation
warning during its optional runtime instrumentation. This leaves application
validation and warnings-as-errors enabled. Use the same setting when testing with
warnings-as-errors on 3.15 until an upstream fix is released. The lint runner uses
one worker on 3.15 because dill 0.4.1 still references the removed `co_lnotab` attribute.

The October 2026 dependency refresh pins FastMCP **4.1.4**, MCP SDK and types
**2.3.0**, and requires Pillow **13.x**.
[FastMCP 4.1.2](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.2) fixes
transformed-component authorization, repeated schema traversal and legacy HTTP
logging-state cleanup. [FastMCP 4.1.3](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.3)
preserves empty visibility selections and per-server configuration timeouts.
[FastMCP 4.1.4](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.4)
forwards explicit HTTP SSE event-size limits to the MCP SDK; ordinary clients
retain the SDK's finite 1 MiB default. The bounded diagnostic transport harness
also supports this exact reviewed version.
These SDK fixes do not change Unity catalog synchronization or the server's
existing tool-selection policy. HTTP endpoints use the existing Starlette/MCP
routes. [Pillow 13](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)
provides Python 3.15 support and decoder fixes; offline comparison remains strict,
bounded PNG/RGBA processing with no resizing or changed tolerance defaults.
The scene-input, skills, task-wait and execution-receipt compatibility gates use
the same generated reviewed SDK manifest. They refuse an unreviewed FastMCP, MCP
SDK or MCP types version; dependency updates must include adapter validation.

Stable fork versions begin at **1.0.0**, independently of the upstream `10.3.0` server baseline. The pinned source commit selects the implementation, not the version label alone. No fork PyPI distribution or prebuilt Docker image is advertised.

## Choose your task

| Task | Guide |
|---|---|
| Connect Unity and an MCP client | [Install And Connect](../website/docs/getting-started/install.md) |
| Review manual HTTP/stdio config | [MCP Clients](../website/docs/getting-started/clients.md) |
| Replace upstream | [Migration](../website/docs/getting-started/migrate.md) |
| Understand tokens, consent and budgets | [Security And Consent](../website/docs/guides/security.md) |
| Target the correct Editor | [Multi-Instance Routing](../website/docs/guides/multi-instance.md) |
| Use terminal commands | [CLI](../website/docs/guides/cli.md) |
| Host behind HTTPS/API-key auth | [Remote Server Auth](../website/docs/guides/remote-server-auth.md) |
| Change/test the implementation | [Development](../website/docs/contributing/dev-setup.md) and [Testing](../website/docs/contributing/testing.md) |

## Run the pinned server manually

Normally the Editor manages launch/configuration. This beta checkout uses the exact `mcpServerSource` recorded in [`MCPForUnity/package.json`](../MCPForUnity/package.json). Published stable releases keep their own immutable source pins; use the value from the installed package:

```bash
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/c2e581a0ee7b512b13132f0fb90ba44448d97876.zip#subdirectory=Server" mcp-for-unity --transport http --http-host 127.0.0.1 --http-port 8080
```

Use that installed package's value for other revisions. Do not replace the pin with an upstream PyPI package or a moving branch. First launch can require dependency downloads; a source pin is not an offline-install guarantee. The commit-addressed archive avoids Git checkout and its Windows long-path limit; it still downloads the repository archive and builds only `Server`. Existing full-commit Git and explicit local development overrides remain supported.

For stdio, use the same `uvx --python ">=3.11" --from` source with `mcp-for-unity --transport stdio`. Each client launches its own process. See [manual configuration](../website/docs/getting-started/clients.md#stdio) for a complete JSON example.

Update the Editor package and regenerate stdio client configuration together, then restart both. The internal loopback bridge now authenticates with automatically supplied per-launch credentials; an older pinned server is incompatible with the new bridge. See [transport compatibility](../docs/development/transport-performance.md#authenticated-stdio-bridge) for the explicit legacy exception and connection limits.

## Local HTTP authentication

Start the HTTP server **before configuring MCP clients**. Every launch creates a fresh `X-Unity-MCP-Token`, stored privately in `~/.unity-mcp/auth/token-<port>` (Windows: `%USERPROFILE%\.unity-mcp\auth\token-<port>`). Generated client configuration includes the current header. After restart, reconfigure and reconnect HTTP MCP clients. Native CLI and Unity connections read the current token automatically.

Only local `GET /health` is public. MCP, REST and plugin WebSocket requests require authentication. Browser-origin requests are rejected; mutating HTTP requests require JSON content type. Automatic token lookup sends credentials only to loopback; use an explicit securely transferred token and trusted tunnel across machines. Never commit/share the token or token-bearing configs.

`UNITY_MCP_LOCAL_AUTH_TOKEN_FILE` changes the server's output/native lookup path; the server still generates a new token. `UNITY_MCP_LOCAL_AUTH_TOKEN` supplies an explicit client token, not a server reuse instruction. See the [full local authentication contract](../website/docs/guides/security.md#local-http-authentication).

FastMCP 4.1 uses the MCP SDK's 30-minute idle timeout for stateful HTTP sessions.
Clients must initialize a new session after expiry. `FASTMCP_HTTP_SESSION_IDLE_TIMEOUT`
accepts a positive number of seconds, `auto` for the SDK default, or `none` to disable
idle expiry. This does not expire the Unity plugin's WebSocket connection or affect stdio.

## Connected remote authentication

Remote HTTP and plugin WebSocket connections retain a private, in-memory authorization
lease for their exact credential and authentication service. `UNITY_MCP_API_KEY_MAX_AUTH_AGE`
sets the finite positive maximum age in seconds (default `300`); embedded applications
can set `config.api_key_max_auth_age` before server creation. A positive cache TTL limits
lease age to the smaller of that TTL and maximum age. A cache hit keeps the original
monotonic deadline. A zero cache TTL still validates every new caller and gives existing
connections a finite lease using maximum age.

At the deadline, reads, writes and dispatch park behind one fresh revalidation, with an
independent 11-second limit. A valid response for the same principal and unchanged
operation grants renews the lease.
Invalid or missing identity, a changed principal, transient failure, timeout, renewal
capacity refusal, shutdown or service replacement retires it. Invalidation/clear also
fence in-progress cache writes and validation results; they require fresh authentication,
without claiming permanent provider revocation or a provider-specific expiry field.

Process-wide limits retain at most 1,024 lease generations, 128 actual lease owners,
16 owners per generation, 16 outbound validations and 128 validation waiters. Existing
per-key and peer validation budgets still apply. Retired work keeps its charges until
its actual exit, including a writer or validator that resists cancellation. A new
connection can receive a capacity refusal while old work drains. Different credentials
for the same user have independent lifetimes and shared-read caches.

Authorization loss terminates established streams rather than sending a second HTTP
response or successful subscription completion. Reconnect and authenticate again for
future work. A command whose write may already have started keeps an unknown outcome;
inspect state before retrying a mutation. Detached test waits retain only an in-memory
lease, stop future polling on retirement and report interruption without claiming that
the native test job was cancelled. Credentials and leases never enter the wait journal.
Local HTTP and stdio authentication are unchanged.

Remote validation can provide independent `read`, `test`, `edit` and `run` grants
in the server-owned `metadata.unity_mcp_permissions` array. An absent claim keeps
legacy access by default; `UNITY_MCP_API_KEY_PERMISSIONS_STRICT=true` makes an
absent claim grant nothing. Explicit empty and malformed claims never inherit
legacy access. Grant changes retire the old connected lease generation. See
[Remote Operation Permissions](../docs/remote-operation-permissions.md) for the
closed operation policy, requester/plugin intersection and migration limits.

## Targeting and tool visibility

Discover `mcpforunity://instances`. Modern sessionless calls use `unity_instance` for tools and `_meta.unity_instance` for resources. Explicit targeting is required for ambiguous local selection and all hosted calls. Legacy stateful clients can persist selection with `set_active_instance`; sessionless requests cannot.

`manage_tools` activation/deactivation/reset also require a stateful handshake. Local clients can use `sync` to refresh selected-Editor toggles. Hosted catalogs are pushed by authenticated Unity plugins; remote `sync` is unavailable. See [Tool Groups](../website/docs/guides/tool-groups.md).

## Editor state subscriptions

Clients using MCP **2026-07-28** can discover resource subscription support through
`server/discover`, then call `subscriptions/listen` with these parameters:

```json
{
  "notifications": {
    "resourceSubscriptions": ["mcpforunity://editor/state"]
  },
  "_meta": {"unity_instance": "My Project@project-hash"}
}
```

Use an authorized Editor selector from `mcpforunity://instances`. An explicit
`_meta.unity_instance` is required for this subscription even on a local server.
The acknowledgment honors only the supported editor-state URI; duplicate and
unsupported URI entries are removed. Resource list-change events are not offered.

Treat `notifications/resources/updated` as an invalidation: re-read
`mcpforunity://editor/state` with the same Editor metadata. The event carries the
URI and subscription metadata, not a state snapshot. State changes, domain
invalidation, and Editor registration/replacement/disconnection can notify,
including connection changes for polling-only plugins. Exact duplicate frames and
unchanged heartbeats do not notify; rapid changes may coalesce into one event.

Buffers and pending invalidations are bounded. A stream that falls behind may
close; reconnect and re-read rather than counting notifications as individual
changes. Keep polling when notifications are unavailable. Resource reads retain
fresh pushed-state reuse and their RPC fallback when push state is stale or
unavailable; mutation preflight still uses authoritative state.

This adds modern `subscriptions/listen` delivery only. Legacy
`resources/subscribe` delivery is not added; legacy resource polling remains
available.

## Optional bounded test-wait Tasks

Tasks are disabled by default. Set `UNITY_MCP_TEST_WAIT_TASKS_DIR` in the HTTP server process environment to a dedicated private journal directory, then restart. The normal launcher reads it only for `--transport http`; stdio and the local `unity-mcp` control CLI do not expose this extension. Existing local-token or hosted API-key authentication still applies.

This adapter requires the locked MCP SDK **2.3.0** and FastMCP **4.1.4**. A client must use protocol **2026-07-28**, advertise `io.modelcontextprotocol/tasks` in its per-request `io.modelcontextprotocol/clientCapabilities.extensions`, and support `resultType: "task"` and `tasks/get`, `tasks/update`, and `tasks/cancel`. Confirm the extension in `server/discover`. Unsupported SDK hooks or an unavailable journal leave Tasks unadvertised and ordinary calls available.

Call the existing `get_test_job` with its `job_id`, an integer `wait_timeout` from 1 to 60, and `unity_instance` when routing requires it. Calls requesting details, failed tests, page size, or a cursor remain ordinary. An admitted eligible call returns a Task handle; use a Tasks-capable SDK to poll `tasks/get` with its `taskId` and matching encoded `Mcp-Name` header. The nested result preserves the ordinary tool result. `tasks/update` acknowledges the wait Task without changing native inputs. There is no separate `wait_for_test_job` tool; `run_tests` is unchanged.

The Task observes the same bounded wait and original HTTP budget (normally at most 30 seconds). Its result can still say the native job is running. Clients without Tasks, ineligible calls, and capacity refusals retain ordinary `get_test_job` results and polling. Tasks never start a new native run or grant a fresh wait deadline.

Journal results and job metadata are private and subject to bounded disk/worker admission. Tasks expire for retrieval after one hour. Expired files are cleaned up lazily when the journal reopens or admits a new wait, so an idle or stopped server can retain files longer; this is not timed disk erasure. Use one dedicated journal per server process, outside shared/synced folders and source control. POSIX roots/entries must be current-user owned and private; new owned directories/files use 0700/0600, and insecure existing paths are refused rather than chmodded. On Windows, supply a per-user directory with private inherited ACLs; POSIX modes do not enforce Windows ACL privacy.

Authenticated local clients share the journal installation's nonsecret owner identity. A rotated valid launch token keeps Task access; stale tokens fail authentication. Hosted Tasks belong to the authenticated user. A different journal has a different local identity; missing/corrupt identity metadata with existing records fails closed. Reopening the same journal preserves retained results; interrupted waits can report an interruption fault without restarting the native job. Credentials are not stored.

`tasks/cancel` cancels the **wait Task**, not native test execution. Its acknowledgement records cancellation intent; inspect `tasks/get` for the final Task state. Use existing native-job polling/control separately.

## Optional missing scene-load input

`UNITY_MCP_SCENE_LOAD_INPUTS=1` enables an optional HTTP input round for the built-in
`manage_scene(action="load")` when no `name`, `path`, or `build_index` was supplied.
It is disabled by default. The reviewed MCP 2.3.0/FastMCP 4.1.4 APIs and protocol
2026-07-28 are required; the client must support `resultType: "input_required"`
and form elicitation. Local and hosted HTTP retain their existing authentication
and instance-selection rules. Unsupported clients, stdio, unavailable SDK hooks,
and admission refusal retain the ordinary missing-target response; supply the
target explicitly to use the existing flow.

The form asks for one existing `Assets/.../*.unity` scene path, without searching
for a target or repeating consent for a complete request. No preflight, refresh or
scene command runs before an accepted, validated answer. The accepted continuation
runs the original tool with only the missing path added. A timeout after dispatch
still has the ordinary uncertain-outcome semantics; inspect Unity before retrying.

The SDK seals state against tampering and binds it to the authenticated principal
and original arguments. Each question also binds the selected instance, project,
session and socket generation for 120 seconds; unrelated reads do not invalidate
it. Decline/cancel completes without loading. A consumed question admits at most
one continuation, including concurrent retries, and a replay reports that the
attempt already occurred rather than claiming success. The process holds at most
128 questions, at most 16 per principal. Active continuations retain their charge
until they exit. These limits do not extend ordinary command deadlines.

Keys and pending questions are process-local and ephemeral. Restart or another
worker cannot resume that question; send a fresh request with an explicit target.
There is no shared-key setting: safely sharing consumption across replicas would
also require shared atomic attempt tracking. Client support does not imply that
any particular host renders the form.

## Pinned Unity guidance over MCP

On the reviewed MCP SDK **2.3.0** and FastMCP **4.1.4**, the server automatically offers the final `io.modelcontextprotocol/skills` extension when its packaged snapshot validates. Modern **2026-07-28** clients can discover it with `server/discover`, enumerate `skills/list`, and look up `skills/get` with `uri: "skill://unity-mcp-orchestrator/SKILL.md"`. Each entry includes unchanged frontmatter and a complete file manifest with raw-byte SHA-256 digests and sizes. Read individual files on demand with `resources/read`; directory reads are not offered. Explicit legacy clients can read the same ordinary resources but cannot call the modern Skills methods.

The server serves one immutable copy of the existing Unity skill, built from the package's `mcpSkillRevision` and included in the server distribution. It does not scan user skill directories, fetch guidance, install files, or activate a skill in the host. File bytes stay unchanged; both metadata methods retain zero/private cache hints. Local HTTP token and hosted authentication still apply; the guidance itself contains no tenant or Editor state and needs no running Editor.

A host must implement Skills discovery, manifest verification and its own consent/activation flow to use this delivery path; server advertisement does not prove host support. Unsupported SDK hooks or invalid/missing packaged data leave Skills unadvertised. The Editor's existing pinned skill preview/file-install workflow remains available for hosts that need files. Updating the guidance requires a new verified package build and server restart, rather than changing request paths or a live user directory.

## Scalar input types

Tool arguments preserve their declared types. Use JSON `true`/`false` for boolean flags and whole JSON integers for integer arguments. Numeric `0`/`1` cannot substitute for booleans; booleans cannot substitute for numbers. Integer arguments reject floating values such as `1.0` and `1.5` instead of rounding or truncating them. Floating arguments accept integers and finite floats, but reject NaN and Infinity.

Parameters explicitly declaring a string alternative retain canonical `"true"`/`"false"`, exact base-10 integer strings, or finite numeric strings as appropriate. An invalid supplied value produces a validation error; it does not silently select the default. Valid `false` and `0` remain intact. These checks also apply to typed collection elements, nested script-edit options/coordinates, texture settings and declared custom-tool parameters.

`batch_execute` retains the Editor's native parameter names and payload shapes. Its commands use the same Unity scalar readers as individual native calls. Arbitrary custom handlers should use `PropertyConversion.ConvertTo<T>(parameters)` or `token.ReadScalar<T>()` instead of Json.NET's permissive primitive conversions; see [Custom Tools](../website/docs/guides/custom-tools.md).

Explicit Unity domain conversions remain supported, including boolean shader toggles and stepped animation-curve tangents.

Tools with readiness guards validate request-local requirements before querying Editor readiness or refreshing assets. Script search and edit tools likewise reject malformed patterns and edit coordinates before reading a file. Checks requiring scene contents, component availability, or a custom-tool catalog still use the selected Editor's state.

The routing middleware checks built-in tool signatures before instance discovery. Custom coercing validators and proxy tools retain SDK validation. Authentication remains first, and conditional tool requirements are checked by the handler after instance routing. A definitive script-mutation error returns immediately; successful mutations and reload/disconnect recovery retain readiness waits. Timeouts also retain the wait because the mutation may already have been sent.

## CLI and environment reference

```bash
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/c2e581a0ee7b512b13132f0fb90ba44448d97876.zip#subdirectory=Server" mcp-for-unity --help
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/c2e581a0ee7b512b13132f0fb90ba44448d97876.zip#subdirectory=Server" unity-mcp --help
```

The server and Editor-control CLI have different options. `unity-mcp` requires local HTTP and accepts `--host`, `--port`, `--timeout`, `--format`, and `--instance`. Place global options before subcommands; see [CLI examples](../website/docs/guides/cli.md).

| Server setting | Purpose |
|---|---|
| `UNITY_MCP_TRANSPORT` | `stdio` or `http`. |
| `UNITY_MCP_HTTP_URL`, `UNITY_MCP_HTTP_HOST`, `UNITY_MCP_HTTP_PORT` | HTTP address/bind overrides. |
| `UNITY_MCP_DEFAULT_INSTANCE` | Server default selector; not a substitute for hosted per-request targeting. |
| `UNITY_MCP_SKIP_STARTUP_CONNECT=1` | Skip initial Unity connection attempt. |
| `UNITY_MCP_LOG_DIR` | Override rotating server log directory. |
| `UNITY_MCP_ENABLE_FOCUS_NUDGE=1` | Opt in to legacy local test-focus recovery; disabled by default. |
| `UNITY_MCP_DISABLE_FOCUS_NUDGE=1` | Disable local test-focus recovery, even when explicitly enabled. |
| `UNITY_MCP_TEST_WAIT_TASKS_DIR` | Opt in to bounded test-wait Tasks over HTTP with a private journal directory. |
| `UNITY_MCP_SCENE_LOAD_INPUTS=1` | Opt in to missing-target scene-load form input over HTTP. |

Hosted mode requires an HTTPS validator and private HTTPS/WSS proxy plus `--http-behind-tls-proxy`; this assertion does not enable TLS. Its flags, environment variables and deployment examples live in [Remote Server Auth](../website/docs/guides/remote-server-auth.md). Local REST/CLI control is unavailable in hosted mode.

Test-job polling leaves desktop focus and minimized windows untouched by default.
Normal Editor background dispatch, test execution and deadlines are unchanged;
`progress.focus_nudge_status` reports `disabled` when recovery is not enabled.
If tests repeatedly stop progressing only while Unity is unfocused, a local server
can explicitly enable the legacy fallback with `UNITY_MCP_ENABLE_FOCUS_NUDGE=1`
and restart the server. This temporarily activates Unity and can restore a
minimized window; returning focus to the previous app does not re-minimize Unity.
Use this only when that desktop interaction is acceptable. A long-running test
without progress updates alone does not establish that focus recovery is needed.
Hosted servers never manipulate the server host's desktop, including with opt-in.

## Tripo local image consent and task recovery

`generate_model` supports local PNG/JPEG input for Tripo when the caller explicitly
sets `allow_image_upload=true`; the CLI equivalent is `--allow-image-upload`.
A hosted `image_url` takes precedence. Upload sends the original file, including
embedded metadata, to Tripo and may incur provider charges. Use only an image you
intend to share. Local input must be a regular file under `Assets`, at most
20,000,000 bytes, with a matching PNG/JPEG signature; URI, traversal, links, empty
files and WebP input are refused. Tripo generation returns GLB; this integration
does not submit a separate conversion task.

Poll the returned job with the status action. Retained generation task receipts can
reconnect after a domain reload in the same Editor session within the original
600-second budget. An unknown submission or import outcome is not replayed; inspect
retained output before retrying. A saved local output may resume without a provider
key only when its recorded file/metadata identity still matches. Recovery across
Editor exit is not promised. Cancel stops local continuation; it does not guarantee
remote cancellation, cleanup or a refund. Checkpoints omit provider keys, upload
tokens, image bytes, source paths and prompts.

See [manual verification](../docs/asset-gen-manual-verification.md) and
[download policy](../docs/asset-gen-download-security.md). Automated upload/recovery
coverage uses synthetic files, fake HTTP and fake GLB containers; it does not verify
live providers, real reload/importer success, decoded image or model quality.

## Request-ordered C# edits

For a mixed text/structured `script_apply_edits` request, set
`options.mixed_order="request"` and `options.preview=true`. Each step interprets
its coordinates, anchors and structural target against the current buffer produced
by earlier steps. Preparation returns the complete proposed contents, paths and
hashes without applying the edits. With `mixed_order` omitted or set to
`"text_first"`, legacy text-first behavior remains and mixed preview is unsupported.

Inspect the preview, then apply the same edit list with preview disabled and its
`precondition_bytes_sha256` and `candidate_bytes_sha256`. If supplied, `applyMode`
or `apply_mode` must be `"sequential"` for request-order interpretation. The final
candidate is committed once under raw-byte hash guards; a no-op needs no file
replacement or refresh. An unknown result after dispatch must be inspected before
retrying, rather than replayed automatically.

Method/class replacements adapt indentation and line endings without changing
string values. Text/range/anchor/regex payloads are literal: include every desired
space and newline. Preserve exact candidate bytes when using a native file tool;
CRLF, BOM or a missing final newline may require a byte-preserving tool or direct
Unity edit fallback. Never normalize string bytes or invent file events.

## Bounded Editor workflows

- **Session object handles:** capture the original loaded GameObject or Component for repeated requests. Handles expire after 600 seconds, earlier eviction or Editor lifecycle changes. They never persist an identity, load assets/scenes or rediscover a replacement. See [session object handles](../website/docs/guides/object-handles.md).
- **Client configuration:** preview a redacted change to the Unity MCP entry before applying it. Apply refuses stale file, target or connection settings. One session-local restore changes only that entry when the written entry still matches; it preserves other configuration entries.
- **Asset metadata pages:** opt into `metadata_only=true` (CLI `--metadata-only`) and continue with `search_snapshot` (`--search-snapshot`). Pages use the same bounded, short-lived captured query. Stale or mismatched tokens refuse without a loading-search fallback. The initial FindAssets allocation and Unity internal work are outside retained-data limits. See [asset search](../website/docs/guides/asset-search.md).
- **Current uGUI projection:** `diagnose_projection` reports camera/world-quad geometry without rendering or forcing layout. Select an explicit camera for World Space. Normalized geometry may remain usable when target pixels are unavailable; the result does not establish visibility, occlusion or input delivery. See [projection diagnostics](../docs/UGUI_PROJECTION_DIAGNOSTICS.md).

## Privacy, limits and diagnosis

Telemetry has no default endpoint: without explicit configuration, no collector worker or persistence starts. `DISABLE_TELEMETRY=1`, `UNITY_MCP_DISABLE_TELEMETRY=1`, or `MCP_DISABLE_TELEMETRY=1` opt out. See [Telemetry](../website/docs/architecture/telemetry.md) for explicit endpoint/timeout configuration. Provider tools can still contact external services when invoked.

Requests, results, polling, regex work and authoring/import operations have budgets. Reduce work or use supported job polling instead of bypassing checks. A timeout after dispatch does not prove a mutation was canceled; inspect actual state before retrying.

Check Unity compilation and bridge status, then current authentication and selected instance. Server launch logs are under `Library/MCPForUnity/Logs/server-launch-<port>.log`; never share credentials or raw private payloads. See [Troubleshooting](../website/docs/guides/troubleshooting.md).

## Local development and containers

Clone the fork's `beta` branch for development and run from `Server` with `uv run --locked src/main.py --transport stdio`. An Editor **Server Source Override** can point to this local `Server` directory; **Dev Mode** is an explicit fresh-install setting. Neither changes the published release/pin.

Local Docker builds are optional, not the normal onboarding path. Build from this checkout's `Server` directory. If exposing local-token HTTP from a container, authorized clients need access to its private current token; a bare port mapping is not a complete client setup. For hosted deployment use the private-network/proxy configuration in [Remote Server Auth](../website/docs/guides/remote-server-auth.md).

## Attribution and support

Maintained by [ykh09242](https://github.com/ykh09242), based on [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp). Original authorship and full MIT notice are retained in [LICENSE](LICENSE). Report fork issues at [ykh09242/unity-mcp/issues](https://github.com/ykh09242/unity-mcp/issues). Not affiliated with Unity Technologies.
