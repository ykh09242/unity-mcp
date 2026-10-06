# Unity MCP (ykh09242) Server

Python MCP server for the [Unity Editor plugin](../MCPForUnity/README.md). The Git-distributed package is `ykh09242-unity-mcp-server`; executable names remain `mcp-for-unity` (server) and `unity-mcp` (local HTTP CLI). Python **3.10+**, FastMCP 4 and MCP SDK 2 are required.

Stable fork versions begin at **1.0.0**, independently of the upstream `10.3.0` server baseline. Git source selects the implementation, not the version label alone. No fork PyPI distribution or prebuilt Docker image is advertised.

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
uvx --from "git+https://github.com/ykh09242/unity-mcp.git@4ddd83dadca78bd8e4e711f6cb53ad9af2a2d188#subdirectory=Server" mcp-for-unity --transport http --http-host 127.0.0.1 --http-port 8080
```

Use that installed package's value for other revisions. Do not replace the pin with an upstream PyPI package or a moving Git branch. First launch can require dependency downloads; a Git pin is not an offline-install guarantee.

For stdio, use the same `uvx --from` source with `mcp-for-unity --transport stdio`. Each client launches its own process. See [manual configuration](../website/docs/getting-started/clients.md#stdio) for a complete JSON example.

## Local HTTP authentication

Start the HTTP server **before configuring MCP clients**. Every launch creates a fresh `X-Unity-MCP-Token`, stored privately in `~/.unity-mcp/auth/token-<port>` (Windows: `%USERPROFILE%\.unity-mcp\auth\token-<port>`). Generated client configuration includes the current header. After restart, reconfigure and reconnect HTTP MCP clients. Native CLI and Unity connections read the current token automatically.

Only local `GET /health` is public. MCP, REST and plugin WebSocket requests require authentication. Browser-origin requests are rejected; mutating HTTP requests require JSON content type. Automatic token lookup sends credentials only to loopback; use an explicit securely transferred token and trusted tunnel across machines. Never commit/share the token or token-bearing configs.

`UNITY_MCP_LOCAL_AUTH_TOKEN_FILE` changes the server's output/native lookup path; the server still generates a new token. `UNITY_MCP_LOCAL_AUTH_TOKEN` supplies an explicit client token, not a server reuse instruction. See the [full local authentication contract](../website/docs/guides/security.md#local-http-authentication).

## Targeting and tool visibility

Discover `mcpforunity://instances`. Modern sessionless calls use `unity_instance` for tools and `_meta.unity_instance` for resources. Explicit targeting is required for ambiguous local selection and all hosted calls. Legacy stateful clients can persist selection with `set_active_instance`; sessionless requests cannot.

`manage_tools` activation/deactivation/reset also require a stateful handshake. Local clients can use `sync` to refresh selected-Editor toggles. Hosted catalogs are pushed by authenticated Unity plugins; remote `sync` is unavailable. See [Tool Groups](../website/docs/guides/tool-groups.md).

## Scalar input types

Tool arguments preserve their declared types. Use JSON `true`/`false` for boolean flags and whole JSON integers for integer arguments. Numeric `0`/`1` cannot substitute for booleans; booleans cannot substitute for numbers. Integer arguments reject floating values such as `1.0` and `1.5` instead of rounding or truncating them. Floating arguments accept integers and finite floats, but reject NaN and Infinity.

Parameters explicitly declaring a string alternative retain canonical `"true"`/`"false"`, exact base-10 integer strings, or finite numeric strings as appropriate. An invalid supplied value produces a validation error; it does not silently select the default. Valid `false` and `0` remain intact. These checks also apply to typed collection elements, nested script-edit options/coordinates, texture settings and declared custom-tool parameters.

`batch_execute` retains the Editor's native parameter names and payload shapes. Its commands use the same Unity scalar readers as individual native calls. Arbitrary custom handlers should use `PropertyConversion.ConvertTo<T>(parameters)` or `token.ReadScalar<T>()` instead of Json.NET's permissive primitive conversions; see [Custom Tools](../website/docs/guides/custom-tools.md).

Explicit Unity domain conversions remain supported, including boolean shader toggles and stepped animation-curve tangents.

## CLI and environment reference

```bash
uvx --from "git+https://github.com/ykh09242/unity-mcp.git@4ddd83dadca78bd8e4e711f6cb53ad9af2a2d188#subdirectory=Server" mcp-for-unity --help
uvx --from "git+https://github.com/ykh09242/unity-mcp.git@4ddd83dadca78bd8e4e711f6cb53ad9af2a2d188#subdirectory=Server" unity-mcp --help
```

The server and Editor-control CLI have different options. `unity-mcp` requires local HTTP and accepts `--host`, `--port`, `--timeout`, `--format`, and `--instance`. Place global options before subcommands; see [CLI examples](../website/docs/guides/cli.md).

| Server setting | Purpose |
|---|---|
| `UNITY_MCP_TRANSPORT` | `stdio` or `http`. |
| `UNITY_MCP_HTTP_URL`, `UNITY_MCP_HTTP_HOST`, `UNITY_MCP_HTTP_PORT` | HTTP address/bind overrides. |
| `UNITY_MCP_DEFAULT_INSTANCE` | Server default selector; not a substitute for hosted per-request targeting. |
| `UNITY_MCP_SKIP_STARTUP_CONNECT=1` | Skip initial Unity connection attempt. |
| `UNITY_MCP_LOG_DIR` | Override rotating server log directory. |
| `UNITY_MCP_DISABLE_FOCUS_NUDGE=1` | Disable local test-focus nudges. |

Hosted mode requires an HTTPS validator and private HTTPS/WSS proxy plus `--http-behind-tls-proxy`; this assertion does not enable TLS. Its flags, environment variables and deployment examples live in [Remote Server Auth](../website/docs/guides/remote-server-auth.md). Local REST/CLI control is unavailable in hosted mode.

## Privacy, limits and diagnosis

Telemetry has no default endpoint: without explicit configuration, no collector worker or persistence starts. `DISABLE_TELEMETRY=1`, `UNITY_MCP_DISABLE_TELEMETRY=1`, or `MCP_DISABLE_TELEMETRY=1` opt out. See [Telemetry](../website/docs/architecture/telemetry.md) for explicit endpoint/timeout configuration. Provider tools can still contact external services when invoked.

Requests, results, polling, regex work and authoring/import operations have budgets. Reduce work or use supported job polling instead of bypassing checks. A timeout after dispatch does not prove a mutation was canceled; inspect actual state before retrying.

Check Unity compilation and bridge status, then current authentication and selected instance. Server launch logs are under `Library/MCPForUnity/Logs/server-launch-<port>.log`; never share credentials or raw private payloads. See [Troubleshooting](../website/docs/guides/troubleshooting.md).

## Local development and containers

Clone the fork's `beta` branch for development and run from `Server` with `uv run --locked src/main.py --transport stdio`. An Editor **Server Source Override** can point to this local `Server` directory; **Dev Mode** is an explicit fresh-install setting. Neither changes the published release/pin.

Local Docker builds are optional, not the normal onboarding path. Build from this checkout's `Server` directory. If exposing local-token HTTP from a container, authorized clients need access to its private current token; a bare port mapping is not a complete client setup. For hosted deployment use the private-network/proxy configuration in [Remote Server Auth](../website/docs/guides/remote-server-auth.md).

## Attribution and support

Maintained by [ykh09242](https://github.com/ykh09242), based on [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp). Original authorship and full MIT notice are retained in [LICENSE](LICENSE). Report fork issues at [ykh09242/unity-mcp/issues](https://github.com/ykh09242/unity-mcp/issues). Not affiliated with Unity Technologies.
