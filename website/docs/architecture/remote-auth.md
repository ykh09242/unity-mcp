# Remote Server Auth: Architecture

Remote hosting uses HTTPS/WSS at a reverse proxy and an unpublished HTTP backend. The CLI requires `--http-behind-tls-proxy` (or `UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true`) as an explicit deployment assertion. ASGI embedding requires `config.http_behind_tls_proxy = True` before `http_app()`. This assertion does not configure encryption; see the [deployment guide](../guides/remote-server-auth.md#https-deployment) for the actual proxy and network boundary.

## Request boundary

`UnityMCP.http_app()` wraps the whole ASGI application in `RemoteControlAuthMiddleware` in remote mode. It runs before FastMCP parses bodies, initializes sessions, enumerates catalogs, or dispatches operations. This also protects custom routes and the plugin WebSocket upgrade.

Only `GET /health` and `GET /api/auth/login-url` bypass authentication. All other HTTP and WebSocket requests require exactly one nonempty `X-API-Key` header. The middleware validates it through `ApiKeyService`; acceptance requires a valid result with a nonempty string `user_id`. Duplicate headers, query-string credentials, unavailable validation, and missing user identity cannot authenticate a request.

HTTP failures return `401`. WebSocket failures send an ASGI close with code `1008` before acceptance; the serving ASGI stack may expose this as an HTTP `403` upgrade rejection. The request body is not parsed on failure. Neither session identifiers nor prior successful requests replace authentication on the current request.

On success, the middleware copies the ASGI scope/state and sets `unity_mcp_authenticated_user_id`. Identity is read from this validated request state, never from an unvalidated header or a host-local fallback.

## MCP routing

1. A client sends an HTTPS request with `X-API-Key` to the proxy.
2. The proxy forwards it to the private backend; `RemoteControlAuthMiddleware` validates the key and attaches identity to request state.
3. FastMCP dispatches through `UnityInstanceMiddleware`. Its tool-call, tool-list, resource-read, resource-list, and resource-template-list hooks call `_inject_unity_instance`.
4. `_resolve_user_id_from_request()` uses FastMCP's `get_http_request()` to read the validated identity from request state. Missing identity in remote mode fails closed.
5. The middleware supplies `user_id` and the selected Unity instance to the operation. Active-instance selection is stored through FastMCP's session-scoped state, keyed by its session ID, not a peer-provided client name.
6. `PluginHub` resolves and dispatches only within the authenticated user's sessions. No remote operation may access the host-local legacy connection pool.

Users must explicitly select an instance with `set_active_instance`, or supply `unity_instance` on a tool call. Remote mode disables sole-instance auto-selection. Instance selection and session IDs do not grant access to another user's Unity session: registry lookup always includes the current validated user.

`script_apply_edits` uses the same scoped transport for reads, hash checks, and writes. `manage_tools sync` is rejected remotely because its legacy discovery source is the server host. Plugin-defined tools remain in their authenticated session's catalog and are invoked through `execute_custom_tool`; registration cannot mutate the process-global MCP catalog or other users' group visibility.

## Plugin connection and registry

The outer ASGI boundary authenticates `/hub/plugin` before `PluginHub.on_connect`. The hub retains its own validation guard and stores `user_id` and API-key metadata on WebSocket state. After acceptance, `_handle_register` passes that identity to `PluginRegistry.register()`.

The registry uses separate keys for local and remote sessions:

| Mode | Lookup key |
|------|------------|
| Local | `project_hash` |
| Remote | `(user_id, project_hash)` |

Two users may connect projects with the same hash without sharing sessions. Reconnecting the same user's project replaces its prior connection. Each socket registers once; limits are 32 sessions per user, 256 overall, and 256 tools / 512 KiB of metadata per plugin. Remote session listing without `user_id` raises `ValueError`.

The hub's inner guard can also close with `4401`, `4403`, or `1013` if authentication changes or fails after the outer gate. Normal missing/invalid credentials are rejected by the outer gate first.

## API-key validation and cache

`ApiKeyService` is initialized by `create_mcp_server()` from server configuration. Its validation URL must be absolute HTTPS without userinfo or fragments. It posts `{"api_key": "..."}` and optionally a configured service-token header. Redirects are not followed. Configure only trusted host proxy and certificate settings.

Validation uses a five-second request timeout and one retry with 100 ms backoff. Definitive valid and invalid results are cached; timeouts, connection failures, and 5xx responses are not. The cache is protected by an async lock and capped at 1,024 entries, with expired and negative entries preferred for eviction. A full cache cannot grow on arbitrary invalid keys.

The default cache TTL is 300 seconds, configurable with `--api-key-cache-ttl`. Revocation can therefore take up to the TTL to propagate. `invalidate_cache(api_key)` and `clear_cache()` support explicit invalidation. Keys are represented in diagnostic logs by a one-way fingerprint rather than their original characters.

## Fail-closed boundaries

| Boundary | Failure | Result |
|----------|---------|--------|
| Remote HTTP startup | TLS proxy assertion absent | Exit before listener startup; ASGI construction also rejects |
| Remote HTTP startup | Validation URL absent/unsafe | Initialization fails |
| Outer HTTP authentication | Missing, duplicate, invalid, or unverifiable key | HTTP `401` before dispatch |
| Outer WebSocket authentication | Missing, duplicate, invalid, or unverifiable key | Upgrade rejected |
| MCP identity injection | Validated request identity absent | Authentication error |
| Remote session lookup | User identity absent | Lookup rejected |
| Legacy Unity connection pool | Remote-hosted mode | Access rejected before host discovery or socket use |

The local-only REST command, instance, and custom-tool routes are not registered in remote mode. Local HTTP uses a separate per-launch-token middleware; stdio retains its local transport behavior.

## Key files

| File | Role |
|------|------|
| `Server/src/main.py` | CLI validation, service initialization, outer ASGI middleware |
| `Server/src/core/config.py` | Remote and TLS proxy configuration |
| `Server/src/transport/remote_auth_middleware.py` | Whole-protocol authentication and request identity |
| `Server/src/services/api_key_service.py` | HTTPS key validation, bounded cache, retry |
| `Server/src/transport/plugin_hub.py` | Plugin upgrade guard and scoped dispatch |
| `Server/src/transport/plugin_registry.py` | User/project session ownership |
| `Server/src/transport/unity_instance_middleware.py` | Per-operation identity and instance injection |
| `Server/src/transport/unity_transport.py` | Validated request identity and scoped transport |
| `docker-compose.remote.yml` | HTTPS proxy with no published backend port |
| `Server/deploy/Caddyfile.remote` | HTTPS, WebSocket forwarding, and MCP streaming |
