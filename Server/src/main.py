from starlette.requests import Request
from transport.unity_instance_middleware import (
    UnityInstanceMiddleware,
    get_unity_instance_middleware,
)
from services.api_key_service import ApiKeyService
from transport.legacy.unity_connection import get_unity_connection_pool, UnityConnectionPool
from services.tools import register_all_tools
from core.telemetry import (
    record_milestone,
    record_telemetry,
    MilestoneType,
    RecordType,
)
from services.resources import register_all_resources
from transport.plugin_registry import PluginRegistry
from transport.plugin_hub import PluginHub
from transport.models import SessionDetails, SessionList
from services.custom_tool_service import (
    CustomToolService,
    resolve_project_id_for_unity_instance,
)
from core.server_build import RUNNING_SERVER
from core.config import config
from core.custom_instructions import (
    CustomInstructionsError,
    append_custom_instructions,
    load_custom_instructions,
)
from core.local_auth import local_auth_token, local_auth_token_path
from transport.local_auth_middleware import LocalControlAuthMiddleware
from transport.local_server_lifecycle import request_local_shutdown
from transport.remote_auth_middleware import RemoteControlAuthMiddleware
from transport.request_body_limit_middleware import (
    MAX_HTTP_REQUEST_BYTES,
    RequestBodyLimitMiddleware,
)
from transport.response_limit_middleware import ResponseLimitMiddleware, ResponseRetentionMiddleware
from models.response_limits import bound_response
from starlette.routing import WebSocketRoute
from starlette.responses import JSONResponse
import argparse
import asyncio
import anyio
from functools import partial
import sys

import logging
from contextlib import asynccontextmanager
import os
import threading
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal
from urllib.parse import urlparse

# Workaround for environments where tool signature evaluation runs with a globals
# dict that does not include common `typing` names (e.g. when annotations are strings
# and evaluated via `eval()` during schema generation).
# Making these names available in builtins avoids `NameError: Annotated/Literal/... is not defined`.
try:  # pragma: no cover - startup safety guard
    import builtins
    import typing as _typing

    _typing_names = (
        "Annotated",
        "Literal",
        "Any",
        "Union",
        "Optional",
        "Dict",
        "List",
        "Tuple",
        "Set",
        "FrozenSet",
    )
    for _name in _typing_names:
        if not hasattr(builtins, _name) and hasattr(_typing, _name):
            # type: ignore[attr-defined]
            setattr(builtins, _name, getattr(_typing, _name))
except Exception:
    pass

from fastmcp import FastMCP
from starlette.middleware import Middleware
from logging.handlers import RotatingFileHandler

if TYPE_CHECKING:
    from fastmcp.server.http import StarletteWithLifespan
    from mcp.server.streamable_http import EventStore


class WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that gracefully handles Windows file locking during rotation."""

    def doRollover(self):
        """Override to catch PermissionError on Windows when log file is locked."""
        try:
            super().doRollover()
        except PermissionError:
            # On Windows, another process may have the log file open.
            # Skip rotation this time - we'll try again on the next rollover.
            pass


# Configure logging using settings from config
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format=config.log_format,
    stream=None,  # None -> defaults to sys.stderr; avoid stdout used by MCP stdio
    force=True,  # Ensure our handler replaces any prior stdout handlers
)
logger = logging.getLogger("mcp-for-unity-server")

# Also write logs to a rotating file so logs are available when launched via stdio.
# Location follows OS conventions; override with UNITY_MCP_LOG_DIR.
try:
    from utils.log_paths import resolve_log_dir

    _log_dir = resolve_log_dir()
    os.makedirs(_log_dir, exist_ok=True)
    _file_path = os.path.join(_log_dir, "unity_mcp_server.log")
    _fh = WindowsSafeRotatingFileHandler(
        _file_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    _fh.setFormatter(logging.Formatter(config.log_format))
    _fh.setLevel(getattr(logging, config.log_level))
    logger.addHandler(_fh)
    logger.propagate = False  # Prevent double logging to root logger
    # Add file handler to root logger so __name__-based loggers (e.g. utils.focus_nudge,
    # services.tools.run_tests) also write to the log file. Named loggers with
    # propagate=False won't double-log.
    logging.getLogger().addHandler(_fh)
    # Also route telemetry logger to the same rotating file and normal level
    try:
        tlog = logging.getLogger("unity-mcp-telemetry")
        tlog.setLevel(getattr(logging, config.log_level))
        tlog.addHandler(_fh)
        tlog.propagate = False  # Prevent double logging for telemetry too
    except Exception as exc:
        # Never let logging setup break startup
        logger.debug("Failed to configure telemetry logger", exc_info=exc)
except Exception as exc:
    # Never let logging setup break startup
    logger.debug("Failed to configure main logger file handler", exc_info=exc)
# Quieten noisy third-party loggers to avoid clutter during stdio handshake
for noisy in ("httpx", "httpx2", "httpcore2", "urllib3", "mcp.server.lowlevel.server"):
    try:
        logging.getLogger(noisy).setLevel(max(logging.WARNING, getattr(logging, config.log_level)))
        logging.getLogger(noisy).propagate = False
    except Exception:
        pass

# Import telemetry only after logging is configured to ensure its logs use stderr and proper levels
# Ensure a slightly higher telemetry timeout unless explicitly overridden by env
try:
    # Ensure generous timeout unless explicitly overridden by env
    if not os.environ.get("UNITY_MCP_TELEMETRY_TIMEOUT"):
        os.environ["UNITY_MCP_TELEMETRY_TIMEOUT"] = "5.0"
except Exception:
    pass

# Global connection pool
_unity_connection_pool: UnityConnectionPool | None = None
_plugin_registry: PluginRegistry | None = None

# Cached server version (set at startup to avoid repeated I/O)
_server_version: str | None = None

# In-memory custom tool service initialized after MCP construction
custom_tool_service: CustomToolService | None = None


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Handle server startup and shutdown."""
    global _unity_connection_pool, _server_version
    _server_version = RUNNING_SERVER.version
    api_key_service = ApiKeyService.get_instance() if ApiKeyService.is_initialized() else None
    logger.info("Unity MCP (ykh09242) v%s starting up", _server_version)

    # Register custom tool management endpoints with FastMCP
    # Routes are declared globally below after FastMCP initialization

    # Note: When using HTTP transport, FastMCP handles the HTTP server
    # Tool registration will be handled through FastMCP endpoints
    enable_http_server = os.environ.get("UNITY_MCP_ENABLE_HTTP_SERVER", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if enable_http_server:
        http_host = os.environ.get("UNITY_MCP_HTTP_HOST", "localhost")
        http_port = int(os.environ.get("UNITY_MCP_HTTP_PORT", "8080"))
        logger.info(f"HTTP tool registry will be available on http://{http_host}:{http_port}")

    global _plugin_registry
    if _plugin_registry is None:
        _plugin_registry = PluginRegistry()
        loop = asyncio.get_running_loop()
        PluginHub.configure(_plugin_registry, loop, mcp=server)

    telemetry_timers: list[threading.Timer] = []

    def defer_telemetry(callback):
        timer = threading.Timer(1.0, callback)
        telemetry_timers.append(timer)
        timer.start()

    try:
        # Record server startup telemetry
        start_time = time.time()
        start_clk = time.perf_counter()
        # Defer initial telemetry by 1s to avoid stdio handshake interference

        def _emit_startup():
            try:
                record_telemetry(
                    RecordType.STARTUP,
                    {
                        "server_version": _server_version,
                        "startup_time": start_time,
                    },
                )
                record_milestone(MilestoneType.FIRST_STARTUP)
            except Exception:
                logger.debug("Deferred startup telemetry failed", exc_info=True)

        defer_telemetry(_emit_startup)

        try:
            skip_connect = os.environ.get("UNITY_MCP_SKIP_STARTUP_CONNECT", "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if skip_connect:
                logger.info(
                    "Skipping Unity connection on startup (UNITY_MCP_SKIP_STARTUP_CONNECT=1)"
                )
            else:
                # Initialize connection pool and discover instances
                _unity_connection_pool = get_unity_connection_pool()
                instances = _unity_connection_pool.discover_all_instances()

                if instances:
                    logger.info(
                        f"Discovered {len(instances)} Unity instance(s): {[i.id for i in instances]}"
                    )

                    # Try to connect to default instance
                    try:
                        _unity_connection_pool.get_connection()
                        logger.info("Connected to default Unity instance on startup")

                        # In stdio mode, query Unity for tool enabled states and sync
                        # server-level visibility. In HTTP mode this is handled by
                        # register_tools via WebSocket in PluginHub.
                        if (config.transport_mode or "stdio").lower() != "http":
                            try:
                                from services.tools import sync_tool_visibility_from_unity

                                sync_result = await sync_tool_visibility_from_unity(notify=False)
                                if sync_result.get("synced"):
                                    logger.info(
                                        "Stdio startup: synced tool visibility from Unity — "
                                        "enabled=[%s], disabled=[%s]",
                                        ", ".join(sync_result.get("enabled_groups", [])),
                                        ", ".join(sync_result.get("disabled_groups", [])),
                                    )
                                else:
                                    # Unsupported command = old Unity package; just debug-log
                                    log_fn = (
                                        logger.debug
                                        if sync_result.get("unsupported")
                                        else logger.warning
                                    )
                                    log_fn(
                                        "Stdio startup: could not sync tool visibility: %s",
                                        sync_result.get("error", "unknown"),
                                    )
                            except Exception as sync_exc:
                                logger.debug(
                                    "Stdio startup: tool visibility sync failed: %s", sync_exc
                                )

                        # Record successful Unity connection (deferred)
                        defer_telemetry(
                            lambda: record_telemetry(
                                RecordType.UNITY_CONNECTION,
                                {
                                    "status": "connected",
                                    "connection_time_ms": (time.perf_counter() - start_clk) * 1000,
                                    "instance_count": len(instances),
                                },
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Could not connect to default Unity instance: {e}")
                else:
                    logger.warning("No Unity instances found on startup")

        except ConnectionError as e:
            logger.warning(f"Could not connect to Unity on startup: {e}")

            # Record connection failure (deferred)
            _err_msg = str(e)[:200]
            defer_telemetry(
                lambda: record_telemetry(
                    RecordType.UNITY_CONNECTION,
                    {
                        "status": "failed",
                        "error": _err_msg,
                        "connection_time_ms": (time.perf_counter() - start_clk) * 1000,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Unexpected error connecting to Unity on startup: {e}")
            _err_msg = str(e)[:200]
            defer_telemetry(
                lambda: record_telemetry(
                    RecordType.UNITY_CONNECTION,
                    {
                        "status": "failed",
                        "error": _err_msg,
                        "connection_time_ms": (time.perf_counter() - start_clk) * 1000,
                    },
                )
            )

        # Yield shared state for lifespan consumers (e.g., middleware)
        yield {
            "pool": _unity_connection_pool,
            "plugin_registry": _plugin_registry,
        }
    finally:
        for timer in telemetry_timers:
            timer.cancel()
        try:
            await PluginHub.shutdown()
        finally:
            try:
                if _unity_connection_pool:
                    _unity_connection_pool.disconnect_all()
            finally:
                try:
                    if api_key_service is not None:
                        await api_key_service.aclose()
                finally:
                    _plugin_registry = None
                    _unity_connection_pool = None
        logger.info("Unity MCP (ykh09242) shut down")


def _build_instructions(project_scoped_tools: bool) -> str:
    if project_scoped_tools:
        custom_tools_note = (
            "I have a dynamic tool system. Always check the mcpforunity://custom-tools resource first "
            "to see what special capabilities are available for the current project."
        )
    else:
        custom_tools_note = (
            "Custom tools are registered as standard tools when Unity connects. "
            "No project-scoped custom tools resource is available."
        )

    return f"""
This server provides tools to interact with the Unity Game Engine Editor.

{custom_tools_note}

Targeting Unity instances:
- Use the resource mcpforunity://instances to list active Unity sessions (Name@hash).
- When multiple instances are connected, specify the target for each request. Pass unity_instance as a tool argument (e.g. unity_instance="MyGame@abc123", unity_instance="abc" for a hash prefix, or unity_instance="6401" for a port number in stdio mode).
- For resource reads, set request metadata _meta.unity_instance to the same identifier (FastMCP Python client: read_resource(uri, meta={{"unity_instance": "MyGame@abc123"}})). Per-request targeting does not change the session default.
- Clients using a stateful legacy MCP protocol can call set_active_instance with an exact Name@hash to pin a session default. Modern sessionless clients must use per-request targeting; set_active_instance and persistent tool-group changes are unavailable.

Important Workflows:

Resources vs Tools:
- Use RESOURCES to read editor state (mcpforunity://editor/state, mcpforunity://project/info, mcpforunity://project/tags, mcpforunity://tests, etc)
- Use TOOLS to perform actions and mutations (manage_editor for play mode control, tag/layer management, etc)
- Always check related resources before modifying the engine state with tools

Reading resources (read this before using ANY resource named below):
- Resources are addressed by URI, never by name. A resource's name and URI are NOT interchangeable: names use underscores (e.g. editor_state) while URIs use slashes (e.g. mcpforunity://editor/state). Do NOT build a URI by swapping separators in the name — you will 404.
- These instructions always spell resources as full mcpforunity:// URIs — read one exactly as written. If you only have a name (from resources/list or another tool's output), look its URI up in resources/list rather than guessing it.
- Resource payloads are wrapped: the content lives under a top-level `data` object, so field paths are `data.<section>.<field>` (e.g. `data.advice.ready_for_tools`), not bare top-level fields.
- The mcpforunity:// URI names the resource, not the server. Some clients take a separate server key on a resource read — in Codex, tools are exposed as mcp__unityMCP__* but resources/read wants server: "unityMCP". If a read fails with an unknown-server error, list resources first and use the key exactly as returned.

Script Management:
- For a Codex-native file edit workflow, first call script_apply_edits with options.preview=true and the selected unity_instance. Preparation returns complete new_contents, authoritative project_root/absolute_path, original_sha256, candidate_sha256 and candidate_bytes_sha256; it applies no edits or refresh. Mixed text/structured preparation is unsupported; split the request or use direct Unity editing.
- Before a native edit, prove the selected Unity session is on the same local host and its project_root is the permitted current workspace. A matching path/hash alone does not prove local provenance; never map a remote Unity path onto a local namesake. Require a complete untruncated candidate and independently hash new_contents encoded as UTF-8 without BOM. A truncated diff is only a summary, never the apply payload.
- Read the actual target bytes, strictly decode UTF-8 (strip only an encoding BOM), and check original_sha256 immediately before writing. Abort and prepare again on drift. Choose an actually exposed native file-edit tool that can preserve all candidate bytes; apply_patch implementations may rewrite CRLF, retain BOM or add final newlines. If that byte shape is unsupported, use a capable native editor or the original direct Unity edit request without preview. Never normalize protected string bytes or add shell repairs/fake patches to manufacture file events.
- After genuine native application, compare exact resulting raw bytes with new_contents encoded as UTF-8 without BOM and candidate_bytes_sha256, then verify get_sha against candidate_sha256 on the same unity_instance. Only after an exact match call validate_script, refresh_unity and read_console for that target. A no_op proposal needs no native write. If local provenance or exact application is unavailable, use the direct Unity edit workflow; this server cannot fabricate Codex Last turn/file-list events or guarantee a particular client UI.
- After creating or modifying scripts (by your own tools or the `manage_script` tool) use `read_console` to check for compilation errors before proceeding
- Only after successful compilation can new components/types be used
- You can poll mcpforunity://editor/state and read `data.compilation.is_compiling` to check if the domain reload is complete, or `data.advice.ready_for_tools` for overall readiness

Scene Setup:
- Always include a Camera and main Light (Directional Light) in new scenes
- Create prefabs with `manage_asset` for reusable GameObjects
- Use `manage_scene` to load, save, and query scene information

Path Conventions:
- Unless specified otherwise, all paths are relative to the project's `Assets/` folder
- Use forward slashes (/) in paths for cross-platform compatibility

Console Monitoring:
- Check `read_console` regularly to catch errors, warnings, and compilation status
- Filter by log type (Error, Warning, Log) to focus on specific issues

Menu Items:
- Use `execute_menu_item` when you have read the mcpforunity://menu-items resource
- This lets you interact with Unity's menu system and third-party tools

Unity API Verification (requires 'docs' tool group):
- When the 'docs' tool group is active, use `unity_reflect` and `unity_docs` to verify Unity API details before answering questions or writing C# code. LLM training data frequently contains incorrect, outdated, or hallucinated Unity APIs.
- BEFORE answering Unity API questions: search the project's assets (`manage_asset`) and reflect the API (`unity_reflect`) to verify. Do NOT rely on training data alone.
- Common hallucination areas: shaders and materials (always search assets for actual shader names), package-specific APIs (Input System, Cinemachine, ProBuilder, NavMesh, URP/HDRP), and APIs that changed between Unity versions.
- Workflow: `unity_reflect search` → `unity_reflect get_type` → `unity_reflect get_member` → `unity_docs get_doc` (if you need examples/caveats).
- For shader/material questions: use `manage_asset(action="search", filter_type="Shader")` to find actual shaders in the project before recommending one.

Payload sizing & paging (important):
- Many Unity queries can return very large JSON. Prefer **paged + summary-first** calls.
- `manage_scene(action="get_hierarchy")`:
  - Use `page_size` + `cursor` and follow `next_cursor` until null.
  - `page_size` is **items per page**; recommended starting point: **50**.
- `manage_gameobject(action="get_components")`:
  - Start with `include_properties=false` (metadata-only) and small `page_size` (e.g. **10-25**).
  - Only request `include_properties=true` when needed; keep `page_size` small (e.g. **3-10**) to bound payloads.
- `manage_asset(action="search")`:
  - Use paging (`page_size`, `page_number`) and keep `page_size` modest (e.g. **25-50**) to avoid token-heavy responses.
  - Keep `generate_preview=false` unless you explicitly need thumbnails (previews may include large base64 payloads).
"""


def _normalize_instance_token(instance_token: str | None) -> tuple[str | None, str | None]:
    if not instance_token:
        return None, None
    if "@" in instance_token:
        name_part, _, hash_part = instance_token.partition("@")
        return (name_part or None), (hash_part or None)
    return None, instance_token


def _select_local_session(
    sessions: SessionList,
    instance_token: str,
) -> tuple[str | None, SessionDetails | None]:
    """Prefer a concrete hash over every project-name match in the catalog."""
    instance_name, instance_hash = _normalize_instance_token(instance_token)
    for session_id, details in sessions.sessions.items():
        if details.hash == instance_hash:
            return session_id, details
    if instance_hash and "@" in instance_token:
        return None, None
    for session_id, details in sessions.sessions.items():
        if details.project in (instance_name, instance_token):
            return session_id, details
    return None, None


class UnityMCP(FastMCP):
    """Protect the control plane for both run() and ASGI embedding."""

    def run(self, transport=None, show_banner=None, **transport_kwargs) -> None:
        if sys.platform != "win32":
            return super().run(transport, show_banner=show_banner, **transport_kwargs)
        # Preserve the IPv4 workaround (#853) without changing global loop policy.
        anyio.run(
            partial(self.run_async, transport, show_banner=show_banner, **transport_kwargs),
            backend_options={"loop_factory": asyncio.SelectorEventLoop},
        )

    async def run_stdio_async(self, show_banner=True, log_level=None, stateless=False) -> None:
        # FastMCP 4.0.11's runner has no public stream injection. This narrow
        # compatibility adapter preserves its lifecycle and initialization.
        from fastmcp.server.context import reset_transport, set_transport
        from fastmcp.utilities.logging import temporary_log_level
        from mcp.server.lowlevel.server import NotificationOptions
        from transport.stdio_response_delivery import retained_stdio_server

        sdk_server = getattr(self, "_mcp_server", None)
        if (
            not callable(getattr(self, "_lifespan_manager", None))
            or not callable(getattr(sdk_server, "run", None))
            or not callable(getattr(sdk_server, "create_initialization_options", None))
        ):
            raise RuntimeError("Installed FastMCP stdio runner API is unsupported")
        if show_banner:
            from fastmcp.utilities.cli import log_server_banner

            log_server_banner(server=self)
        token = set_transport("stdio")
        try:
            with temporary_log_level(log_level):
                async with self._lifespan_manager():
                    async with retained_stdio_server() as (read_stream, write_stream):
                        await sdk_server.run(
                            read_stream,
                            write_stream,
                            sdk_server.create_initialization_options(
                                notification_options=NotificationOptions(tools_changed=True)
                            ),
                        )
        finally:
            reset_transport(token)

    def http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        event_store: "EventStore | None" = None,
        retry_interval: int | None = None,
        host_origin_protection: bool | Literal["auto"] | None = None,
        allowed_hosts: list[str] | None = None,
        allowed_origins: list[str] | None = None,
        session_idle_timeout: float | None = None,
    ) -> "StarletteWithLifespan":
        if config.http_remote_hosted and not config.http_behind_tls_proxy:
            raise ValueError(
                "Remote HTTP requires a private backend behind an HTTPS/WSS proxy; "
                "configure http_behind_tls_proxy only after securing that boundary"
            )
        app = super().http_app(
            path=path,
            middleware=middleware,
            json_response=json_response,
            stateless_http=stateless_http,
            transport=transport,
            event_store=event_store,
            retry_interval=retry_interval,
            host_origin_protection=host_origin_protection,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            session_idle_timeout=session_idle_timeout,
        )
        # add_middleware prepends: authenticate before inspecting or reading bodies.
        app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_HTTP_REQUEST_BYTES)
        app.add_middleware(ResponseRetentionMiddleware)
        if not config.http_remote_hosted:
            app.add_middleware(LocalControlAuthMiddleware, token=config.local_auth_token)
        else:
            app.add_middleware(RemoteControlAuthMiddleware)
        return app


def create_mcp_server(
    project_scoped_tools: bool,
    *,
    managed_instance_token: str | None = None,
    custom_instructions: str | None = None,
) -> FastMCP:
    mcp = UnityMCP(
        name="mcp-for-unity-server",
        version=RUNNING_SERVER.version,
        lifespan=server_lifespan,
        instructions=append_custom_instructions(
            _build_instructions(project_scoped_tools), custom_instructions
        ),
    )
    mcp.add_middleware(ResponseLimitMiddleware())

    global custom_tool_service
    custom_tool_service = CustomToolService(mcp, project_scoped_tools=project_scoped_tools)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_http(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "timestamp": time.time(),
                "version": _server_version or RUNNING_SERVER.version,
                "server_build": RUNNING_SERVER.command_metadata(),
                "message": "Unity MCP (ykh09242) server is running",
            }
        )

    @mcp.custom_route("/api/auth/login-url", methods=["GET"])
    async def auth_login_url(_: Request) -> JSONResponse:
        """Return the login URL for users to obtain/manage API keys."""
        if not config.api_key_login_url:
            return JSONResponse(
                {
                    "success": False,
                    "error": "API key management not configured. Contact your server administrator.",
                },
                status_code=404,
            )
        return JSONResponse(
            {
                "success": True,
                "login_url": config.api_key_login_url,
            }
        )

    # Only expose CLI routes if running locally (not in remote hosted mode)
    if not config.http_remote_hosted:

        @mcp.custom_route("/api/server/shutdown", methods=["POST"])
        async def local_shutdown_route(request: Request) -> JSONResponse:
            return await request_local_shutdown(request, managed_instance_token)

        @mcp.custom_route("/api/command", methods=["POST"])
        async def cli_command_route(request: Request) -> JSONResponse:
            """REST endpoint for CLI commands to Unity."""
            try:
                body = await request.json()

                command_type = body.get("type")
                params = body.get("params", {})
                unity_instance = body.get("unity_instance")

                if not command_type:
                    return JSONResponse(
                        {"success": False, "error": "Missing 'type' field"}, status_code=400
                    )

                # Get available sessions
                sessions = await PluginHub.get_sessions()
                if not sessions.sessions:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "No Unity instances connected. Make sure Unity is running with MCP plugin.",
                        },
                        status_code=503,
                    )

                # Find target session
                session_id = None
                session_details = None
                if unity_instance:
                    session_id, session_details = _select_local_session(sessions, unity_instance)

                # If a specific unity_instance was requested but not found, return an error
                # (Check done here so execute_custom_tool can also validate the instance)
                if unity_instance and not session_id:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": f"Unity instance '{unity_instance}' not found",
                        },
                        status_code=404,
                    )

                # If no specific unity_instance requested, use first available session
                # (Must be done before execute_custom_tool check so all command types benefit)
                if not session_id:
                    try:
                        session_id = next(iter(sessions.sessions.keys()))
                        session_details = sessions.sessions.get(session_id)
                    except StopIteration:
                        # No sessions available - sessions.sessions is empty
                        # This should not happen since we checked at line 378, but handle gracefully
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "No Unity instances connected. Make sure Unity is running with MCP plugin.",
                            },
                            status_code=503,
                        )

                # Custom tool execution - must be checked BEFORE the final PluginHub.send_command call
                # This applies to both cases: with or without explicit unity_instance
                if command_type == "execute_custom_tool":
                    # session_id and session_details are already set above
                    if not session_id or not session_details:
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "No valid Unity session available for custom tool execution",
                            },
                            status_code=503,
                        )
                    tool_name = None
                    tool_params = {}
                    if isinstance(params, dict):
                        tool_name = params.get("tool_name") or params.get("name")
                        tool_params = params.get("parameters") or params.get("params") or {}

                    if not tool_name:
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "Missing 'tool_name' for execute_custom_tool",
                            },
                            status_code=400,
                        )
                    if tool_params is None:
                        tool_params = {}
                    if not isinstance(tool_params, dict):
                        return JSONResponse(
                            {"success": False, "error": "Tool parameters must be an object/dict"},
                            status_code=400,
                        )

                    # Prefer a concrete hash for project-scoped tools.
                    unity_instance_hint = unity_instance
                    if session_details and session_details.hash:
                        unity_instance_hint = session_details.hash

                    project_id = resolve_project_id_for_unity_instance(unity_instance_hint)
                    if not project_id:
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "Could not resolve project id for custom tool",
                            },
                            status_code=400,
                        )

                    service = CustomToolService.get_instance()
                    result = await service.execute_tool(
                        project_id, tool_name, unity_instance_hint, tool_params
                    )
                    return JSONResponse(bound_response(result.model_dump()))

                # Send command to Unity
                result = await PluginHub.send_command(session_id, command_type, params)
                return JSONResponse(bound_response(result))

            except Exception as e:
                logger.error("CLI command failed (%s)", type(e).__name__)
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        @mcp.custom_route("/api/instances", methods=["GET"])
        async def cli_instances_route(_: Request) -> JSONResponse:
            """REST endpoint to list connected Unity instances."""
            try:
                sessions = await PluginHub.get_sessions()
                instances = []
                for session_id, details in sessions.sessions.items():
                    instances.append(
                        {
                            "session_id": session_id,
                            "project": details.project,
                            "hash": details.hash,
                            "unity_version": details.unity_version,
                            "connected_at": details.connected_at,
                        }
                    )
                return JSONResponse({"success": True, "instances": instances})
            except Exception as e:
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        @mcp.custom_route("/api/custom-tools", methods=["GET"])
        async def cli_custom_tools_route(request: Request) -> JSONResponse:
            """REST endpoint to list custom tools for the active Unity project."""
            try:
                unity_instance = request.query_params.get("instance")
                sessions = await PluginHub.get_sessions()
                if not sessions.sessions:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "No Unity instances connected. Make sure Unity is running with MCP plugin.",
                        },
                        status_code=503,
                    )

                session_details = None
                if unity_instance:
                    _, session_details = _select_local_session(sessions, unity_instance)
                    if not session_details:
                        return JSONResponse(
                            {
                                "success": False,
                                "error": f"Unity instance '{unity_instance}' not found",
                            },
                            status_code=404,
                        )
                else:
                    # No specific unity_instance requested: use first available session
                    session_details = next(iter(sessions.sessions.values()))

                unity_instance_hint = unity_instance
                if session_details and session_details.hash:
                    unity_instance_hint = session_details.hash

                project_id = resolve_project_id_for_unity_instance(unity_instance_hint)
                if not project_id:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Could not resolve project id for custom tools",
                        },
                        status_code=400,
                    )

                service = CustomToolService.get_instance()
                tools = await service.list_registered_tools(project_id)
                tools_payload = [
                    tool.model_dump() if hasattr(tool, "model_dump") else tool for tool in tools
                ]

                return JSONResponse(
                    {
                        "success": True,
                        "project_id": project_id,
                        "tool_count": len(tools_payload),
                        "tools": tools_payload,
                    }
                )
            except Exception as e:
                logger.error("CLI custom tools failed (%s)", type(e).__name__)
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # Initialize and register middleware for session-based Unity instance routing
    # Using the singleton getter ensures we use the same instance everywhere
    unity_middleware = get_unity_instance_middleware()
    mcp.add_middleware(unity_middleware)
    logger.info("Registered Unity instance middleware for session-based routing")

    # Initialize API key authentication if in remote-hosted mode
    if config.http_remote_hosted and config.api_key_validation_url:
        ApiKeyService(
            validation_url=config.api_key_validation_url,
            cache_ttl=config.api_key_cache_ttl,
            service_token_header=config.api_key_service_token_header,
            service_token=config.api_key_service_token,
        )
        logger.info(
            "Initialized API key authentication service (validation URL: %s, TTL: %.0fs)",
            config.api_key_validation_url,
            config.api_key_cache_ttl,
        )

    # Mount plugin websocket hub at /hub/plugin when HTTP transport is active.
    # NOTE: Uses FastMCP private API because custom_route() only supports HTTP
    # methods, not WebSocket. _additional_http_routes accepts Starlette Route
    # objects and is still present in FastMCP 3.x.
    existing_routes = [
        route
        for route in mcp._get_additional_http_routes()
        if isinstance(route, WebSocketRoute) and route.path == "/hub/plugin"
    ]
    if not existing_routes:
        mcp._additional_http_routes.append(WebSocketRoute("/hub/plugin", PluginHub))

    # Register all tools
    register_all_tools(mcp, project_scoped_tools=project_scoped_tools)

    # Register all resources
    register_all_resources(mcp, project_scoped_tools=project_scoped_tools)

    return mcp


def main():
    """Entry point for uvx and console scripts."""
    parser = argparse.ArgumentParser(
        description="Unity MCP (ykh09242)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  UNITY_MCP_DEFAULT_INSTANCE   Default Unity instance to target (project name, hash, or 'Name@hash')
  UNITY_MCP_SKIP_STARTUP_CONNECT   Skip initial Unity connection attempt (set to 1/true/yes/on)
  UNITY_MCP_TELEMETRY_ENABLED   Enable telemetry (set to 1/true/yes/on)
  UNITY_MCP_TRANSPORT   Transport protocol: stdio or http (default: stdio)
  UNITY_MCP_HTTP_URL   HTTP server URL (default: http://127.0.0.1:8080)
  UNITY_MCP_HTTP_HOST   HTTP server host (overrides URL host)
  UNITY_MCP_HTTP_PORT   HTTP server port (overrides URL port)
  UNITY_MCP_HTTP_BEHIND_TLS_PROXY   Confirm the remote HTTP backend is private behind HTTPS/WSS

Examples:
  # Use specific Unity project as default
  python -m src.server --default-instance "MyProject"

  # Start with HTTP transport
  python -m src.server --transport http --http-url http://127.0.0.1:8080

  # Start with stdio transport (default)
  python -m src.server --transport stdio

  # Use environment variable for transport
  UNITY_MCP_TRANSPORT=http UNITY_MCP_HTTP_URL=http://localhost:9000 python -m src.server
        """,
    )
    parser.add_argument(
        "--default-instance",
        type=str,
        metavar="INSTANCE",
        help="Default Unity instance to target (project name, hash, or 'Name@hash'). "
        "Overrides UNITY_MCP_DEFAULT_INSTANCE environment variable.",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol to use: stdio or http (default: stdio). "
        "Overrides UNITY_MCP_TRANSPORT environment variable.",
    )
    parser.add_argument(
        "--http-url",
        type=str,
        default="http://127.0.0.1:8080",
        metavar="URL",
        help="HTTP server URL (default: http://127.0.0.1:8080). "
        "Can also set via UNITY_MCP_HTTP_URL environment variable.",
    )
    parser.add_argument(
        "--http-host",
        type=str,
        default=None,
        metavar="HOST",
        help="HTTP server host (overrides URL host). "
        "Overrides UNITY_MCP_HTTP_HOST environment variable.",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=None,
        metavar="PORT",
        help="HTTP server port (overrides URL port). "
        "Overrides UNITY_MCP_HTTP_PORT environment variable.",
    )
    parser.add_argument(
        "--http-remote-hosted",
        action="store_true",
        help="Treat HTTP transport as remotely hosted (forces explicit Unity instance selection). "
        "Can also set via UNITY_MCP_HTTP_REMOTE_HOSTED=true.",
    )
    parser.add_argument(
        "--http-behind-tls-proxy",
        action="store_true",
        help="Confirm this HTTP backend is private behind an HTTPS/WSS reverse proxy. "
        "Required for remote-hosted mode; does not enable TLS on the backend. "
        "Can also set via UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true.",
    )
    parser.add_argument(
        "--api-key-validation-url",
        type=str,
        default=None,
        metavar="URL",
        help="External URL to validate API keys (POST with {'api_key': '...'}). "
        "Required when --http-remote-hosted is set. "
        "Can also set via UNITY_MCP_API_KEY_VALIDATION_URL.",
    )
    parser.add_argument(
        "--api-key-login-url",
        type=str,
        default=None,
        metavar="URL",
        help="URL where users can obtain/manage API keys. "
        "Returned by /api/auth/login-url endpoint. "
        "Can also set via UNITY_MCP_API_KEY_LOGIN_URL.",
    )
    parser.add_argument(
        "--api-key-cache-ttl",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="Cache TTL for validated API keys in seconds (default: 300). "
        "Can also set via UNITY_MCP_API_KEY_CACHE_TTL.",
    )
    parser.add_argument(
        "--api-key-service-token-header",
        type=str,
        default=None,
        metavar="HEADER",
        help="Header name for service token sent to validation endpoint (e.g. X-Service-Token). "
        "Can also set via UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER.",
    )
    parser.add_argument(
        "--api-key-service-token",
        type=str,
        default=None,
        metavar="TOKEN",
        help="Service token value sent to validation endpoint for server authentication. "
        "WARNING: Prefer UNITY_MCP_API_KEY_SERVICE_TOKEN env var in production to avoid process listing exposure.",
    )
    parser.add_argument(
        "--unity-instance-token",
        type=str,
        default=None,
        metavar="TOKEN",
        help="Optional per-launch token set by Unity for deterministic lifecycle management. "
        "Used by Unity to validate it is stopping the correct process.",
    )
    parser.add_argument(
        "--pidfile",
        type=str,
        default=None,
        metavar="PATH",
        help="Optional path where the server will write its PID on startup. "
        "Used by Unity to stop the exact process it launched when running in a terminal.",
    )
    parser.add_argument(
        "--project-scoped-tools",
        action="store_true",
        help="Keep custom tools scoped to the active Unity project and enable the custom tools resource. "
        "Can also set via UNITY_MCP_PROJECT_SCOPED_TOOLS=true.",
    )

    parser.add_argument(
        "--instructions-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Optional project instructions file (strict UTF-8, at most 32768 bytes). "
        "Read once at startup and appended to the built-in instructions.",
    )

    args = parser.parse_args()
    try:
        custom_instructions = load_custom_instructions(args.instructions_file)
    except CustomInstructionsError as exc:
        parser.error(str(exc))

    # Set environment variables from command line args
    if args.default_instance:
        os.environ["UNITY_MCP_DEFAULT_INSTANCE"] = args.default_instance
        logger.info(f"Using default Unity instance from command-line: {args.default_instance}")

    # Set transport mode
    config.transport_mode = args.transport or os.environ.get("UNITY_MCP_TRANSPORT", "stdio")
    logger.info(f"Transport mode: {config.transport_mode}")

    config.http_remote_hosted = bool(args.http_remote_hosted) or os.environ.get(
        "UNITY_MCP_HTTP_REMOTE_HOSTED", ""
    ).lower() in ("true", "1", "yes", "on")
    config.http_behind_tls_proxy = bool(args.http_behind_tls_proxy) or os.environ.get(
        "UNITY_MCP_HTTP_BEHIND_TLS_PROXY", ""
    ).lower() in ("true", "1", "yes", "on")

    # API key authentication configuration
    config.api_key_validation_url = args.api_key_validation_url or os.environ.get(
        "UNITY_MCP_API_KEY_VALIDATION_URL"
    )
    config.api_key_login_url = args.api_key_login_url or os.environ.get(
        "UNITY_MCP_API_KEY_LOGIN_URL"
    )
    try:
        cache_ttl_env = os.environ.get("UNITY_MCP_API_KEY_CACHE_TTL")
        config.api_key_cache_ttl = float(cache_ttl_env) if cache_ttl_env else args.api_key_cache_ttl
    except ValueError:
        logger.warning("Invalid UNITY_MCP_API_KEY_CACHE_TTL value, using default 300.0")
        config.api_key_cache_ttl = 300.0

    # Service token for authenticating to validation endpoint
    config.api_key_service_token_header = args.api_key_service_token_header or os.environ.get(
        "UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER"
    )
    config.api_key_service_token = args.api_key_service_token or os.environ.get(
        "UNITY_MCP_API_KEY_SERVICE_TOKEN"
    )

    # Validate: remote-hosted HTTP mode requires API key validation URL
    if (
        config.http_remote_hosted
        and config.transport_mode == "http"
        and not config.api_key_validation_url
    ):
        logger.error(
            "--http-remote-hosted requires --api-key-validation-url or "
            "UNITY_MCP_API_KEY_VALIDATION_URL environment variable"
        )
        raise SystemExit(1)

    if (
        config.http_remote_hosted
        and config.transport_mode == "http"
        and not config.http_behind_tls_proxy
    ):
        logger.error(
            "Remote HTTP requires an HTTPS/WSS reverse proxy and a private backend. "
            "Bind the backend to loopback or an unpublished container network, then "
            "set --http-behind-tls-proxy or UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true. "
            "This assertion does not enable TLS; do not publish the HTTP backend."
        )
        raise SystemExit(1)

    http_url = os.environ.get("UNITY_MCP_HTTP_URL", args.http_url)
    parsed_url = urlparse(http_url)

    # Allow individual host/port to override URL components
    http_host = (
        args.http_host
        or os.environ.get("UNITY_MCP_HTTP_HOST")
        or parsed_url.hostname
        or "127.0.0.1"
    )

    # Safely parse optional environment port (may be None or non-numeric)
    _env_port_str = os.environ.get("UNITY_MCP_HTTP_PORT")
    try:
        _env_port = int(_env_port_str) if _env_port_str is not None else None
    except ValueError:
        logger.warning("Invalid UNITY_MCP_HTTP_PORT value '%s', ignoring", _env_port_str)
        _env_port = None

    http_port = args.http_port or _env_port or parsed_url.port or 8080

    os.environ["UNITY_MCP_HTTP_HOST"] = http_host
    os.environ["UNITY_MCP_HTTP_PORT"] = str(http_port)

    # Optional lifecycle handshake for Unity-managed terminal launches
    if args.unity_instance_token:
        os.environ["UNITY_MCP_INSTANCE_TOKEN"] = args.unity_instance_token
    if args.pidfile:
        try:
            pid_dir = os.path.dirname(args.pidfile)
            if pid_dir:
                os.makedirs(pid_dir, exist_ok=True)
            with open(args.pidfile, "w", encoding="ascii") as f:
                f.write(str(os.getpid()))
        except Exception as exc:
            logger.warning("Failed to write pidfile '%s': %s", args.pidfile, exc)

    if args.http_url != "http://127.0.0.1:8080":
        logger.info(f"HTTP URL set to: {http_url}")
    if args.http_host:
        logger.info(f"HTTP host override: {http_host}")
    if args.http_port:
        logger.info(f"HTTP port override: {http_port}")

    # Explicit CLI/env overrides always win
    project_scoped_tools_explicit = bool(args.project_scoped_tools) or os.environ.get(
        "UNITY_MCP_PROJECT_SCOPED_TOOLS", ""
    ).lower() in ("true", "1", "yes", "on")

    # If not explicitly set, check Unity status files for the default instance.
    # In stdio mode there is typically only one instance, so "first match wins" is fine.
    project_scoped_tools = project_scoped_tools_explicit
    if not project_scoped_tools_explicit and not config.http_remote_hosted:
        try:
            from transport.legacy.unity_connection import get_unity_connection_pool

            pool = get_unity_connection_pool()
            instances = pool.discover_all_instances()
            # If ANY discovered instance requests project-scoped tools, enable them
            for inst in instances:
                if getattr(inst, "project_scoped_tools", False):
                    project_scoped_tools = True
                    logger.info(
                        "Enabling project-scoped tools because Unity instance %s requested it",
                        inst.id,
                    )
                    break
        except Exception:
            logger.debug(
                "Could not discover Unity instances for project-scoped tool default", exc_info=True
            )

    mcp = create_mcp_server(
        project_scoped_tools,
        custom_instructions=custom_instructions,
        managed_instance_token=(
            args.unity_instance_token
            if args.pidfile and config.transport_mode == "http" and not config.http_remote_hosted
            else None
        ),
    )

    # Determine transport mode
    if config.transport_mode == "http":
        # Use HTTP transport for FastMCP
        transport = "http"
        # Use the parsed host and port from URL/args
        http_url = os.environ.get("UNITY_MCP_HTTP_URL", args.http_url)
        parsed_url = urlparse(http_url)
        host = (
            args.http_host
            or os.environ.get("UNITY_MCP_HTTP_HOST")
            or parsed_url.hostname
            or "127.0.0.1"
        )
        port = args.http_port or _env_port or parsed_url.port or 8080
        logger.info(f"Starting FastMCP with HTTP transport on {host}:{port}")
        if config.http_remote_hosted:
            mcp.run(transport=transport, host=host, port=port)
        else:
            with local_auth_token(port) as token:
                config.local_auth_token = token
                logger.info(
                    "Local HTTP authentication enabled; token file: %s",
                    local_auth_token_path(port),
                )
                try:
                    mcp.run(transport=transport, host=host, port=port)
                finally:
                    config.local_auth_token = None
    else:
        # Use stdio transport for traditional MCP
        logger.info("Starting FastMCP with stdio transport")
        mcp.run(transport="stdio")


# Run the server
if __name__ == "__main__":
    main()
