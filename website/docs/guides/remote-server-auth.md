# Remote Server API Key Authentication

When running the MCP for Unity server as a shared remote service, API key authentication ensures that only authorized users can access the server and that each user's Unity sessions are isolated from one another.

This guide covers how to configure, deploy, and use the feature.

This is operator-managed hosting, not a fork-provided service. For same-machine setup use [Install And Connect](../getting-started/install.md) and its local token authentication instead. High-impact Editor consent remains separate from API-key authentication; see [Security And Consent](./security.md).

## Prerequisites

### External Auth Service

You need an external HTTPS endpoint that validates API keys. The server delegates all key validation to this endpoint rather than managing keys itself.

The endpoint must:

- Accept `POST` requests with a JSON body: `{"api_key": "<key>"}`
- Return a JSON response indicating validity and the associated user identity
- Be reachable from the MCP server over the network

See [Validation Contract](#validation-contract) for the full request/response specification.

### Transport Mode

API key authentication is only available when running with HTTP transport (`--transport http`). It has no effect in stdio mode.

## Server Configuration

### CLI Arguments

| Argument | Environment Variable | Default | Description |
| -------- | -------------------- | ------- | ----------- |
| `--http-remote-hosted` | `UNITY_MCP_HTTP_REMOTE_HOSTED` | `false` | Enable remote-hosted mode. Requires API key auth. |
| `--http-behind-tls-proxy` | `UNITY_MCP_HTTP_BEHIND_TLS_PROXY` | `false` | Confirm a private HTTP backend behind an HTTPS/WSS proxy (required for remote hosting). Does not enable TLS itself. |
| `--api-key-validation-url URL` | `UNITY_MCP_API_KEY_VALIDATION_URL` | None | External endpoint to validate API keys (required). |
| `--api-key-login-url URL` | `UNITY_MCP_API_KEY_LOGIN_URL` | None | URL where users can obtain or manage API keys. |
| `--api-key-cache-ttl SECONDS` | `UNITY_MCP_API_KEY_CACHE_TTL` | `300` | How long validated keys are cached (seconds). |
| `--api-key-service-token-header HEADER` | `UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER` | None | Header name for server-to-auth-service authentication. |
| `--api-key-service-token TOKEN` | `UNITY_MCP_API_KEY_SERVICE_TOKEN` | None | Token value sent to the auth service for server authentication. |

Environment variables take effect when the corresponding CLI argument is not provided. For boolean flags, set the env var to `true`, `1`, or `yes`.

### Startup Validation

The server validates its configuration at startup:

- If `--http-remote-hosted` is set but `--api-key-validation-url` is not provided (and the env var is also unset), the server logs an error and exits with code 1.
- Remote HTTP startup also requires `--http-behind-tls-proxy` (or its environment variable). Configure HTTPS first and keep the backend reachable only by the proxy on loopback or an unpublished container network. Setting an `https://` value for `--http-url` alone does not enable TLS on this listener.
- The validation endpoint must use absolute HTTPS, with no embedded credentials or URL fragment. Plaintext endpoints (including loopback) are rejected at initialization, and redirects are not followed. HTTPX honors the host's proxy and certificate environment settings; configure only trusted proxies and certificate authorities.

### HTTPS deployment

Clients must connect over **HTTPS**, and Unity's plugin connection must use **WSS**. The Python listener is a private HTTP backend. Terminate TLS at a reverse proxy with a trusted certificate; never publish the backend port directly to clients.

For a proxy on the same machine, bind the backend to loopback:

```bash
uv run --directory Server mcp-for-unity \
  --transport http \
  --http-host 127.0.0.1 \
  --http-port 8080 \
  --http-remote-hosted \
  --http-behind-tls-proxy \
  --api-key-validation-url https://auth.example.com/api/validate-key \
  --api-key-login-url https://app.example.com/api-keys \
  --api-key-cache-ttl 120
```

Or using environment variables:

```bash
export UNITY_MCP_TRANSPORT=http
export UNITY_MCP_HTTP_HOST=127.0.0.1
export UNITY_MCP_HTTP_PORT=8080
export UNITY_MCP_HTTP_REMOTE_HOSTED=true
export UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true
export UNITY_MCP_API_KEY_VALIDATION_URL=https://auth.example.com/api/validate-key
export UNITY_MCP_API_KEY_LOGIN_URL=https://app.example.com/api-keys

uv run --directory Server mcp-for-unity --transport http
```

For example, run Caddy on the same host with this configuration, substituting a public DNS hostname that resolves to that host:

```caddyfile
https://mcp.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

Caddy obtains and renews certificates automatically and forwards WebSocket upgrades and streaming MCP responses. Permit inbound ports 80 and 443 for the proxy and certificate challenges; keep port 8080 private. See the [Caddy HTTPS documentation](https://caddyserver.com/docs/automatic-https) and [reverse proxy reference](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

The repository also includes `docker-compose.remote.yml` and `Server/deploy/Caddyfile.remote`. From the repository root:

```bash
export MCP_DOMAIN=mcp.example.com
export UNITY_MCP_API_KEY_VALIDATION_URL=https://auth.example.com/api/validate-key
export UNITY_MCP_API_KEY_LOGIN_URL=https://app.example.com/api-keys
docker compose -f docker-compose.remote.yml up -d --build
```

Run this Compose file on its own; combining it with the local `docker-compose.yml` would publish the backend port. Only Caddy publishes ports in the remote example. The backend uses the Compose network for proxy requests and outbound HTTPS for key validation. Do not put the proxy-to-backend hop across an untrusted network; use TLS for that hop if the services are on separate hosts. For ASGI embedding, configure the same private boundary and set `config.http_behind_tls_proxy = True` before calling `http_app()`.

### Service Token (Optional)

If your auth service requires the MCP server to authenticate itself (server-to-server auth), configure a service token:

```bash
--api-key-service-token-header X-Service-Token \
--api-key-service-token "your-server-secret"
```

This adds the specified header to every validation request sent to the auth endpoint.

We strongly recommend using this feature because it ensures that the entity requesting validation is the MCP server itself, not an imposter.

## Unity Plugin Setup

When connecting to a remote-hosted server, Unity users need to provide their API key:

1. Open the Unity MCP (ykh09242) window in the Unity Editor.
2. Select HTTP Remote as the connection mode.
3. Set the server URL to `https://mcp.example.com` and enter the API key in the API Key field. The key is stored in `EditorPrefs` (per-machine, not source-controlled). Keep **Allow Insecure Remote HTTP** disabled. The plugin derives its `wss://` connection from this HTTPS URL.
4. Click **Get API Key** to open the login URL in a browser if you need a new key. This fetches the URL from the server's `/api/auth/login-url` endpoint.

The API key is a one-time entry per machine. It persists across Unity sessions until explicitly cleared.

## MCP Client Configuration

When an API key is configured, the Unity plugin's MCP client configurators automatically include the `X-API-Key` header in generated configuration files.

Example generated config for **Cursor** (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "mcp-for-unity": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "X-API-Key": "<your-api-key>"
      }
    }
  }
}
```

Example for **Claude Code** (CLI):

```bash
claude mcp add --transport http mcp-for-unity https://mcp.example.com/mcp \
  --header "X-API-Key: <your-api-key>"
```

Similar header injection works for VS Code, Windsurf, Cline, and other supported MCP clients.

## Behaviour Changes in Remote-Hosted Mode

Enabling `--http-remote-hosted` changes several server behaviours compared to the default local mode:

### Authentication Enforcement

Every MCP HTTP request requires exactly one valid `X-API-Key` header, including initialization, resource and template catalogs, tool listings, calls, and session operations. Missing, duplicate, invalid, or unverifiable credentials receive HTTP `401` before body parsing or session creation. A session ID or query parameter cannot substitute for the header. Only `GET /health` and `GET /api/auth/login-url` are public.

### WebSocket Auth Gate

Unity plugins connecting via WebSocket (`/hub/plugin`) are validated during the handshake:

| Scenario | WebSocket Close Code | Reason |
| -------- | -------------------- | ------ |
| Missing, duplicate, or invalid API key | `1008` before acceptance | API key authentication required |
| Auth service unavailable | `1008` before acceptance | API key authentication required |
| Valid API key | Connection accepted | user_id stored in connection state |

The ASGI server may report a pre-acceptance rejection as HTTP `403` during the WebSocket upgrade.

### Session Isolation

Each user can only see and interact with their authorized Unity instances. Hosted requests must explicitly select the Editor with `unity_instance` for tools or `_meta.unity_instance` for resources. `set_active_instance` is available only on stateful legacy connections; modern sessionless callers cannot persist selection. Plugin and client keys must resolve to the same user identity. See [Multi-Instance Routing](./multi-instance.md).

### Auto-Select Disabled

In local mode, the server automatically selects the sole connected Unity instance. In remote-hosted mode, this auto-selection is disabled. Users must explicitly call `set_active_instance` with a `Name@hash` from the `mcpforunity://instances` resource.

### CLI Routes Disabled

The following host-local REST endpoints are unavailable in remote-hosted mode, even to authenticated clients:

- `POST /api/command`
- `GET /api/instances`
- `GET /api/custom-tools`

### Endpoints Always Available

These endpoints remain accessible regardless of auth:

| Endpoint | Method | Purpose |
| -------- | ------ | ------- |
| `/health` | GET | Health check for load balancers and monitoring |
| `/api/auth/login-url` | GET | Returns the login URL for API key management |

## Validation Contract

### Request

```http
POST <api-key-validation-url>
Content-Type: application/json

{
  "api_key": "<the-api-key>"
}
```

If a service token is configured, an additional header is sent:

```http
<service-token-header>: <service-token-value>
```

### Response (Valid Key)

```json
{
  "valid": true,
  "user_id": "user-abc-123",
  "metadata": {}
}
```

- `valid` (bool, required): Must be `true`.
- `user_id` (string, required): Stable identifier for the user. Used for session isolation.
- `metadata` (object, optional): Arbitrary metadata stored alongside the validation result.

### Response (Invalid Key)

```json
{
  "valid": false,
  "error": "API key expired"
}
```

- `valid` (bool, required): Must be `false`.
- `error` (string, optional): Human-readable reason.

### Response (HTTP 401)

A `401` status code is also treated as an invalid key (no body parsing required).

### Timeouts and Retries

- Request timeout: 5 seconds
- Retries: 1 (with 100ms backoff)
- Failure mode: deny by default (treated as invalid on any error)

Transient failures (5xx, timeouts, network errors) are **not cached**, so subsequent requests will retry the auth service.

## Error Reference

| Context | Condition | Response |
| ------- | --------- | -------- |
| Any MCP HTTP request | Missing, duplicate, invalid, or unverifiable API key | HTTP `401` before dispatch |
| WebSocket connect | Missing, duplicate, invalid, or unverifiable API key | Upgrade rejected (ASGI close `1008`) |
| `/api/auth/login-url` | Login URL not configured | HTTP `404` with admin guidance message |
| Server startup | Remote-hosted without validation URL | `SystemExit(1)` |
| Server startup | Remote-hosted without explicit TLS proxy configuration | `SystemExit(1)` |

## Troubleshooting

### Custom tools on shared servers

Remote plugin tool definitions are scoped to the authenticated user's selected Unity session. Read `mcpforunity://custom-tools` and call `execute_custom_tool` to use them. Hosted servers do not publish plugin-defined tools as process-global MCP methods or let plugin registration change global tool-group visibility. Re-read the resource after reconnecting or changing the Editor's tool selection; one tenant's changes do not broadcast notifications to other tenants.

Each plugin can register at most 256 tools with 512 KiB of serialized metadata. A socket registers only one session and must register as its first message within 10 seconds. The server permits at most 32 accepted sockets per user and 256 overall, counting sockets that have not registered yet. Reconnecting an existing project replaces its previous session, but needs an available connection slot during the handshake; close an old connection first if the limit has been reached.

Custom-tool polling is limited to 16 active executions per selected project, 32 per user, and 256 overall. Its server-owned deadline is at most 600 seconds, including initial dispatch, subsequent polling commands, and sleeps. Plugin metadata cannot extend that deadline.

Plugin messages and final MCP responses have independent 32 MiB ceilings. Result depth, node count, and retained data are also bounded. Slow HTTP/SSE delivery continues to consume result capacity until delivery or cancellation finishes; exceeding a budget returns a small error. Use pagination and asset paths for large content instead of returning full files inline. Model downloads and archive extraction retain their separate larger limits.

### "API key authentication required" error on every tool call

The server is in remote-hosted mode but no API key is being sent. Ensure the MCP client configuration includes the `X-API-Key` header, or set it in the Unity plugin's connection settings.

### Server exits immediately with code 1

Remote hosting requires both `--api-key-validation-url` and `--http-behind-tls-proxy` (or their environment variables). Set the proxy assertion only after configuring HTTPS/WSS and restricting access to the backend. See [HTTPS deployment](#https-deployment).

### WebSocket upgrade is rejected

Check that the Unity plugin sends a valid API key in the Unity MCP (ykh09242) window's connection settings. If the key is valid, check network connectivity between the MCP server and the validation URL; authentication fails closed when the external service is unavailable.

### User cannot see their Unity instance

Session isolation is active. The Unity editor and the MCP client must use API keys that resolve to the same `user_id`. Verify that the Unity plugin's WebSocket connection and the MCP client's HTTP requests use the same API key.

### Stale auth after key rotation

Validated keys are cached for `--api-key-cache-ttl` seconds (default: 300). After rotating or revoking a key, there is a delay equal to the TTL before the old key stops working. Lower the TTL for faster revocation at the cost of more frequent validation requests.
