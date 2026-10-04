# MCP for Unity Server

[![MCP](https://badge.mcpx.dev?status=on 'MCP Enabled')](https://modelcontextprotocol.io/introduction)
[![python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-red.svg 'MIT License')](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/badge/discord-join-red.svg?logo=discord&logoColor=white)](https://discord.gg/y4p8KfzrN4)
[![PyPI version](https://img.shields.io/pypi/v/mcpforunityserver?label=PyPI)](https://pypi.org/project/mcpforunityserver/)
[![Downloads](https://static.pepy.tech/badge/mcpforunityserver)](https://pepy.tech/project/mcpforunityserver)

Model Context Protocol server for Unity Editor integration. Control Unity through natural language using AI assistants like Claude, Cursor, and more.

The server uses FastMCP 4 and MCP SDK 2, supporting Python 3.10 and newer.
Stateful MCP clients can use `set_active_instance` to select a Unity Editor for their session.
Clients using the modern sessionless protocol must pass `unity_instance` on each tool call
and `_meta.unity_instance` on each resource read for remote hosting or when local
auto-selection is ambiguous;
`set_active_instance` returns an explanatory error instead of reporting a selection that cannot persist.
When using FastMCP's Python client and session selection is needed, connect with `Client(..., mode="legacy")`.
The `activate`, `deactivate`, and `reset` actions of `manage_tools` also require a stateful handshake.
Sessionless clients can list groups and, on local servers, use `sync` to refresh server defaults
from Unity's tool toggles.
Remote-hosted clients of either protocol see and can call the built-in tools enabled in their
authenticated Unity plugin's catalog, including optional groups. Without a catalog, only
server-side helper tools are available. Remote `sync` is unavailable because Unity pushes its catalog.

For example, with FastMCP's Python client:

```python
await client.read_resource(
    "mcpforunity://project/info", meta={"unity_instance": "MyGame@abc123"}
)
```

The equivalent MCP `resources/read` request parameters are:

```json
{
  "uri": "mcpforunity://project/info",
  "_meta": { "unity_instance": "MyGame@abc123" }
}
```

These selectors apply only to the current request. Clients that cannot attach resource
metadata can use a stateful protocol and `set_active_instance` for a persistent default.

**Maintained by [Coplay](https://www.coplay.dev/?ref=unity-mcp)** - This project is not affiliated with Unity Technologies.

💬 **Join our community:** [Discord Server](https://discord.gg/y4p8KfzrN4)

**Required:** Install the [Unity MCP Plugin](https://github.com/CoplayDev/unity-mcp?tab=readme-ov-file#-step-1-install-the-unity-package) to connect Unity Editor with this MCP server. You also need `uvx` (requires [uv](https://docs.astral.sh/uv/)) to run the server.

---

## Installation

### Option 1: PyPI

Install and run directly from PyPI using `uvx`.

**Run Server (HTTP):**

```bash
uvx --from mcpforunityserver mcp-for-unity --transport http --http-url http://localhost:8080
```

**MCP Client Configuration (HTTP):**

```json
{
  "mcpServers": {
    "UnityMCP": {
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Unity-MCP-Token": "<current launch token>" }
    }
  }
}
```

**MCP Client Configuration (stdio):**

```json
{
  "mcpServers": {
    "UnityMCP": {
      "command": "uvx",
      "args": [
        "--from",
        "mcpforunityserver",
        "mcp-for-unity",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### Option 2: From GitHub Source

Use this to run the latest released version from the repository. Change the version to `main` to run the latest unreleased changes from the repository.

```json
{
  "mcpServers": {
    "UnityMCP": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/CoplayDev/unity-mcp@v10.3.0#subdirectory=Server",
        "mcp-for-unity",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### Option 3: Docker

**Use Pre-built Image:**

```bash
docker run -p 8080:8080 msanatan/mcp-for-unity-server:latest --transport http --http-url http://0.0.0.0:8080
```

**Build Locally:**

```bash
docker build -t unity-mcp-server .
docker run -p 8080:8080 unity-mcp-server --transport http --http-url http://0.0.0.0:8080
```

Configure your MCP client with `"url": "http://localhost:8080/mcp"`.

### Option 4: Local Development

For contributing or modifying the server code:

```bash
# Clone the repository
git clone https://github.com/CoplayDev/unity-mcp.git
cd unity-mcp/Server

# Run with uv
uv run src/main.py --transport stdio
```

---

## Configuration

The server connects to Unity Editor automatically when both are running. Most users do not need to change any settings.

### Local HTTP authentication

Every local HTTP server launch generates a fresh 256-bit token. REST (`/api/*`),
MCP (`/mcp`, including session requests), and the plugin WebSocket (`/hub/plugin`)
require the `X-Unity-MCP-Token` header. Only `GET /health` is public. Local control
requests with an `Origin` or `Sec-Fetch-Site` header are rejected, even with a valid
token; browser clients are not supported. POST/PUT/PATCH requests must use
`Content-Type: application/json`.

The token is written atomically to `~/.unity-mcp/auth/token-<port>` (on Windows,
`%USERPROFILE%\.unity-mcp\auth\token-<port>`), with owner-only file permissions on
POSIX and the user directory's inherited ACL on Windows. The token is never served
over HTTP or printed in server logs. A clean shutdown removes it, and every restart
replaces it. Do not commit or share this file.

Start the server **before configuring MCP clients** in the Unity window. Unity's
WebSocket and the `unity-mcp` CLI read the current token on each new connection or
request. Generated JSON, Codex TOML, and Claude Code configurations include the
header. After restarting the server, configure your HTTP MCP clients again and
reconnect them so they use the new token. For manual configuration, use the current
file contents as the header value; Codex calls this setting `http_headers`.

Set `UNITY_MCP_LOCAL_AUTH_TOKEN_FILE` to an absolute private file path to change
the server's output location and the native clients' lookup location. The server
always generates its own token; it does not reuse a pre-existing file. For a client
on another machine, explicitly provide the current token through
`UNITY_MCP_LOCAL_AUTH_TOKEN`, a securely transferred token file, or the MCP header.
Automatic file discovery only sends credentials to local hosts. Local HTTP is
unencrypted; use a trusted tunnel across machines. For Docker, share a private token
file/directory with authorized clients, or use remote-hosted authentication.

Stdio and remote-hosted API key authentication keep their existing behavior.

### CLI options

These options apply to the `mcp-for-unity` command (whether run via `uvx`, Docker, or `python src/main.py`).

- `--transport {stdio,http}` - Transport protocol (default: `stdio`)
- `--http-url URL` - Base URL used to derive host/port defaults (default: `http://localhost:8080`)
- `--http-host HOST` - Override HTTP bind host (overrides URL host)
- `--http-port PORT` - Override HTTP bind port (overrides URL port)
- `--http-remote-hosted` - Treat HTTP transport as remotely hosted
  - Requires API key authentication (see below)
  - Disables local/CLI-only HTTP routes (`/api/command`, `/api/instances`, `/api/custom-tools`)
  - Forces explicit Unity instance selection for MCP tool/resource calls
  - Isolates Unity sessions per user
- `--http-behind-tls-proxy` - Required for remote HTTP; confirms the backend is private behind an HTTPS/WSS proxy. This flag does not enable TLS itself.
- `--api-key-validation-url URL` - External endpoint to validate API keys (required when `--http-remote-hosted` is set)
- `--api-key-login-url URL` - URL where users can obtain/manage API keys (served by `/api/auth/login-url`)
- `--api-key-cache-ttl SECONDS` - Cache duration for validated keys (default: `300`)
- `--api-key-service-token-header HEADER` - Header name for server-to-auth-service authentication (e.g. `X-Service-Token`)
- `--api-key-service-token TOKEN` - Token value sent to the auth service for server authentication
- `--default-instance INSTANCE` - Default Unity instance to target (project name, hash, or `Name@hash`)
- `--project-scoped-tools` - Keep custom tools scoped to the active Unity project and enable the custom tools resource
- `--unity-instance-token TOKEN` - Optional per-launch token set by Unity for deterministic lifecycle management
- `--pidfile PATH` - Optional path where the server writes its PID on startup (used by Unity-managed terminal launches)

### Environment variables

- `UNITY_MCP_TRANSPORT` - Transport protocol: `stdio` or `http`
- `UNITY_MCP_HTTP_URL` - HTTP server URL (default: `http://localhost:8080`)
- `UNITY_MCP_HTTP_HOST` - HTTP bind host (overrides URL host)
- `UNITY_MCP_HTTP_PORT` - HTTP bind port (overrides URL port)
- `UNITY_MCP_HTTP_REMOTE_HOSTED` - Enable remote-hosted mode (`true`, `1`, or `yes`)
- `UNITY_MCP_HTTP_BEHIND_TLS_PROXY` - Confirm the remote HTTP backend is private behind an HTTPS/WSS proxy
- `UNITY_MCP_DEFAULT_INSTANCE` - Default Unity instance to target (project name, hash, or `Name@hash`)
- `UNITY_MCP_SKIP_STARTUP_CONNECT=1` - Skip initial Unity connection attempt on startup
- `UNITY_MCP_LOG_DIR` - Override the rotating server log directory. Default: `%LOCALAPPDATA%\UnityMCP\Logs` (Windows), `~/Library/Application Support/UnityMCP/Logs` (macOS), `$XDG_STATE_HOME/UnityMCP/Logs` (Linux/BSD, defaults to `~/.local/state/UnityMCP/Logs`).

API key authentication (remote-hosted mode):

- `UNITY_MCP_API_KEY_VALIDATION_URL` - External endpoint to validate API keys
- `UNITY_MCP_API_KEY_LOGIN_URL` - URL where users can obtain/manage API keys
- `UNITY_MCP_API_KEY_CACHE_TTL` - Cache TTL for validated keys in seconds (default: `300`)
- `UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER` - Header name for server-to-auth-service authentication
- `UNITY_MCP_API_KEY_SERVICE_TOKEN` - Token value sent to the auth service for server authentication

Telemetry:

- `DISABLE_TELEMETRY=1` - Disable anonymous telemetry (opt-out)
- `UNITY_MCP_DISABLE_TELEMETRY=1` - Same as `DISABLE_TELEMETRY`
- `MCP_DISABLE_TELEMETRY=1` - Same as `DISABLE_TELEMETRY`
- `UNITY_MCP_TELEMETRY_ENDPOINT` - Override telemetry endpoint URL
- `UNITY_MCP_TELEMETRY_TIMEOUT` - Override telemetry request timeout (seconds)

### Examples

**Stdio (default):**

```bash
uvx --from mcpforunityserver mcp-for-unity --transport stdio
```

**HTTP (local):**

```bash
uvx --from mcpforunityserver mcp-for-unity --transport http --http-host 127.0.0.1 --http-port 8080
```

**Remote HTTPS (private backend behind a TLS proxy, with API key auth):**

Configure the proxy first using the [HTTPS deployment guide](../website/docs/guides/remote-server-auth.md#https-deployment). Bind the Python backend to loopback when the proxy is on the same host:

```bash
uvx --from mcpforunityserver mcp-for-unity \
  --transport http \
  --http-host 127.0.0.1 \
  --http-port 8080 \
  --http-remote-hosted \
  --http-behind-tls-proxy \
  --api-key-validation-url https://auth.example.com/api/validate-key \
  --api-key-login-url https://app.example.com/api-keys
```

**Disable telemetry:**

```bash
DISABLE_TELEMETRY=1 uvx --from mcpforunityserver mcp-for-unity --transport stdio
```

---

## Remote-Hosted Mode

When deploying the server as a shared remote service (e.g. for a team or Asset Store users), enable `--http-remote-hosted` to activate API key authentication and per-user session isolation.

**Requirements:**

- A trusted HTTPS reverse proxy for MCP requests and WSS plugin connections. Keep the HTTP backend on loopback or an unpublished container network; never expose it directly. `--http-behind-tls-proxy` (or `UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true`) explicitly confirms this boundary and is required at startup. `--http-url https://...` alone does not configure TLS.
- An external HTTPS endpoint that validates API keys. The server POSTs `{"api_key": "..."}` and expects `{"valid": true, "user_id": "..."}` or `{"valid": false}` in response.
- `--api-key-validation-url` must be provided (or `UNITY_MCP_API_KEY_VALIDATION_URL`). The server exits with code 1 if this is missing.
- The validation URL must be absolute HTTPS without embedded credentials or a fragment. Plaintext URLs, including loopback, are rejected before authentication starts. Validation redirects are not followed. HTTPX honors proxy and certificate environment settings, so only use trusted proxies and CA configuration on the server host.

**What changes in remote-hosted mode:**

- Every MCP HTTP request (including initialization and catalogs) and Unity plugin WebSocket upgrade requires exactly one valid `X-API-Key` header. Authentication failures are rejected before dispatch.
- Each user only sees Unity instances that connected with their API key (session isolation).
- Auto-selection of a sole Unity instance is disabled; users must explicitly call `set_active_instance`.
- CLI REST routes (`/api/command`, `/api/instances`, `/api/custom-tools`) are disabled.
- `/health` and `/api/auth/login-url` remain accessible without authentication.

**MCP client config with API key:**

```json
{
  "mcpServers": {
    "UnityMCP": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "X-API-Key": "<your-api-key>"
      }
    }
  }
}
```

For full details, see [Remote Server Auth Guide](../website/docs/guides/remote-server-auth.md) and [Architecture Reference](../website/docs/architecture/remote-auth.md). The repository's `docker-compose.remote.yml` provides a Caddy HTTPS proxy with an unpublished backend.

---

## MCP Resources

The server provides read-only MCP resources for querying Unity Editor state. Resources provide up-to-date information about your Unity project without modifying it.

**Accessing Resources:**

Resources are accessed by their URI (not their name). Always use `ListMcpResources` to get the correct URI format.

**Example URIs:**
- `mcpforunity://editor/state` - Editor readiness snapshot
- `mcpforunity://project/tags` - All project tags
- `mcpforunity://scene/gameobject/{instance_id}` - GameObject details by ID
- `mcpforunity://prefab/{encoded_path}` - Prefab info by asset path

**Important:** Resource names use underscores (e.g., `editor_state`) but URIs use slashes/hyphens (e.g., `mcpforunity://editor/state`). Always use the URI from `ListMcpResources()` when reading resources.

**All resource descriptions now include their URI** for easy reference. List available resources to see the complete catalog with URIs.

---

## Example Prompts

Once connected, try these commands in your AI assistant:

- "Create a 3D player controller with WASD movement"
- "Add a rotating cube to the scene with a red material"
- "Create a simple platformer level with obstacles"
- "Generate a shader that creates a holographic effect"
- "List all GameObjects in the current scene"

---

## Documentation

For complete documentation, troubleshooting, and advanced usage:

📖 **[Full Documentation](https://coplaydev.github.io/unity-mcp/)**

---

## Requirements

- **Python:** 3.10 or newer
- **Unity Editor:** 2021.3 LTS or newer
- **uv:** Python package manager ([Installation Guide](https://docs.astral.sh/uv/getting-started/installation/))

---

## License

MIT License - See [LICENSE](https://github.com/CoplayDev/unity-mcp/blob/main/LICENSE)
