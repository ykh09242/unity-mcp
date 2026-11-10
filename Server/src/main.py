from core.launch_output import install_managed_launch_output

# Install before dependency imports can capture stderr for their own loggers.
install_managed_launch_output()

from core.server_runtime import ServerRuntime, ServerRuntimeOwner
from transport.http_routes import (
    install_http_routes,
    _normalize_instance_token as _normalize_instance_token,
    _select_local_session as _select_local_session,
    _local_target_refusal as _local_target_refusal,
)
from transport.execution_receipts import (
    execute_receipted_rest,
    install_execution_receipts,
)
from transport.unity_instance_middleware import (
    UnityInstanceMiddleware,
    get_unity_instance_middleware,
)
from services.api_key_service import ApiKeyService
from core.endpoint_logging import endpoint_for_log
from core.windows_event_loop import run_with_wakeup_guard
from transport.legacy.unity_connection import (
    get_unity_connection_pool,
    run_with_connection_deadline,
    UnityConnectionPool,
)
from services.tools import register_all_tools
from core.telemetry import (
    begin_telemetry_shutdown,
    record_milestone,
    record_telemetry,
    start_telemetry,
    MilestoneType,
    RecordType,
)
from services.resources import register_all_resources
from services.test_results_app import install_test_results_app
from transport.plugin_registry import PluginRegistry
from transport.plugin_hub import PluginHub
from services.custom_tool_service import (
    CustomToolService,
    resolve_project_id_for_unity_instance,
)
from core.server_build import RUNNING_SERVER
from core.sdk_compatibility import runtime_sdk_report
from services.execution_receipt_context import owned_to_thread, submit_owned_executor
from services.reference_graph.reconcile import drained_task
from core.config import config, parse_trusted_proxy_cidrs
from core.custom_instructions import (
    CustomInstructionsError,
    append_custom_instructions,
    load_custom_instructions,
)
from core.local_auth import local_auth_token, local_auth_token_path, local_http_sockets
from transport.local_auth_middleware import LocalControlAuthMiddleware
from transport.remote_auth_middleware import (
    RemoteControlAuthMiddleware,
    RemoteLegacySseGuard,
    RemoteSessionTerminationGuard,
)
from transport.catalog_subscriptions import (
    CatalogSubscriptionLifetimeMiddleware,
    install_catalog_subscriptions,
)
from transport.mcp_version_middleware import McpVersionMiddleware, mcp_request_body_limit
from transport.inventory_ordering import InventoryOrderingMiddleware
from transport.session_state_store import SessionStateStore
from transport.skills_extension import install_skills
from transport.scene_load_inputs import prepare_scene_load_inputs
from transport.test_job_tasks import WaitTaskDeliveryMiddleware
from transport.test_wait_binding import install_test_job_tasks
from transport.request_body_limit_middleware import (
    MAX_HTTP_REQUEST_BYTES,
    RequestBodyLimitMiddleware,
)
from transport.response_limit_middleware import ResponseLimitMiddleware, ResponseRetentionMiddleware
from starlette.routing import WebSocketRoute
import argparse
import asyncio
import contextvars
import anyio
from functools import partial
import sys

import logging
import json
import math
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Literal, TypeVar
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

import fastmcp
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
    from core.phase_diagnostics import configure_file

    configure_file(_log_dir)
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

# Stop admitting startup work after this budget. An already running worker must
# still physically settle before cancellation/deadline cleanup can release it.
STARTUP_CONNECT_TIMEOUT = 10.0
_StartupResult = TypeVar("_StartupResult")


async def _startup_worker(
    function: Callable[[], _StartupResult], deadline: float
) -> _StartupResult:
    """Drain actual executor work while preserving its waiter's cancellation scope."""
    loop = asyncio.get_running_loop()
    if loop.time() >= deadline:
        raise TimeoutError("Startup Unity connection deadline reached")
    # Selection and socket helpers use monotonic time; convert the loop's clock
    # once at admission, preserving the remaining original startup budget.
    io_deadline = time.monotonic() + (deadline - loop.time())
    future = submit_owned_executor(loop, None, run_with_connection_deadline, function, io_deadline)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError as cancellation:
        # Repeated cancellation cannot release physical ownership. Preserve the
        # original exception so AnyIO can recognize its own cancel-scope exit.
        with anyio.CancelScope(shield=True):
            try:
                await drained_task(asyncio.gather(future, return_exceptions=True))
            finally:
                raise cancellation


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

    telemetry_owner = start_telemetry()
    telemetry_timers: list[threading.Timer] = []

    def defer_telemetry(callback):
        timer = threading.Timer(1.0, callback)
        telemetry_timers.append(timer)
        timer.start()

    try:
        receipts = getattr(server, "_execution_receipts", None)
        if receipts is not None:
            await receipts.start()
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
                    owner=telemetry_owner,
                )
                record_milestone(MilestoneType.FIRST_STARTUP, owner=telemetry_owner)
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
            elif (config.transport_mode or "stdio").lower() == "http":
                # HTTP commands and visibility use PluginHub/WebSocket. Keep
                # legacy connections lazy for callers that explicitly need them.
                logger.debug("HTTP startup uses the WebSocket hub without legacy discovery")
            else:
                # Initialize connection pool and discover instances
                loop = asyncio.get_running_loop()
                original_deadline = getattr(server, "_unity_startup_deadline", None)
                startup_deadline = loop.time() + (
                    original_deadline - time.monotonic()
                    if original_deadline is not None
                    else STARTUP_CONNECT_TIMEOUT
                )
                async with asyncio.timeout_at(startup_deadline):
                    _unity_connection_pool = await _startup_worker(
                        get_unity_connection_pool, startup_deadline
                    )
                    instances = await _startup_worker(
                        _unity_connection_pool.discover_all_instances, startup_deadline
                    )

                if instances:
                    logger.info(
                        f"Discovered {len(instances)} Unity instance(s): {[i.id for i in instances]}"
                    )

                    # Try to connect to default instance
                    try:
                        async with asyncio.timeout_at(startup_deadline):
                            await _startup_worker(
                                _unity_connection_pool.get_connection, startup_deadline
                            )
                        logger.info("Connected to default Unity instance on startup")

                        # In stdio mode, query Unity for tool enabled states and sync
                        # server-level visibility. In HTTP mode this is handled by
                        # register_tools via WebSocket in PluginHub.
                        if (config.transport_mode or "stdio").lower() != "http":
                            try:
                                from services.tools import sync_tool_visibility_from_unity

                                async with asyncio.timeout_at(startup_deadline):
                                    sync_result = await sync_tool_visibility_from_unity(
                                        notify=False
                                    )
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
                                owner=telemetry_owner,
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
                    owner=telemetry_owner,
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
                    owner=telemetry_owner,
                )
            )

        # Yield shared state for lifespan consumers (e.g., middleware)
        subscriptions = getattr(server, "_catalog_subscriptions", None)
        state_store = getattr(server, "_unity_session_state_store", None)
        async with state_store.lifespan() if state_store is not None else nullcontext():
            async with subscriptions.lifespan() if subscriptions is not None else nullcontext():
                yield {
                    "pool": _unity_connection_pool,
                    "plugin_registry": _plugin_registry,
                }
    finally:
        # The task owns every cleanup callback. Repeated asyncio cancellation and
        # AnyIO level cancellation cannot skip the remaining independent owners.
        with anyio.CancelScope(shield=True):
            await drained_task(
                asyncio.create_task(
                    _shutdown_server_resources(
                        server,
                        api_key_service,
                        telemetry_timers,
                        _plugin_registry,
                        _unity_connection_pool,
                        telemetry_owner,
                    ),
                    context=contextvars.Context(),
                )
            )


async def _shutdown_server_resources(
    server, api_key_service, telemetry_timers, registry, pool, telemetry_owner=None
) -> None:
    from services.state.external_changes_scanner import external_changes_scanner

    # Startup connection can be skipped while later requests create the pool.
    if pool is None:
        from transport.legacy import unity_connection

        pool = unity_connection._unity_connection_pool

    def clear_globals():
        global _plugin_registry, _unity_connection_pool
        if _plugin_registry is registry:
            _plugin_registry = None
        if _unity_connection_pool is pool:
            _unity_connection_pool = None

    async def close_graph(graph):
        if await graph.aclose() is False:
            logger.warning(
                "Reference graph shutdown observation ended; owned work is still draining: %s",
                graph.shutdown_status,
            )

    # Register all owners before invoking any cleanup. ExitStack keeps unwinding
    # if an earlier callback raises, without clearing a live graph worker's lease.
    async with AsyncExitStack() as cleanup:
        cleanup.callback(clear_globals)
        # Fence before the first cleanup await. The callback owns the captured
        # collector even if a later lifespan opens while other resources drain.
        cleanup.push_async_callback(owned_to_thread, begin_telemetry_shutdown(telemetry_owner))
        if api_key_service is not None:
            cleanup.push_async_callback(api_key_service.aclose)
        cleanup.push_async_callback(PluginHub.begin_shutdown())
        for timer in telemetry_timers:
            cleanup.callback(timer.cancel)
        for attribute, method in (
            ("_scene_load_inputs", "close"),
            ("_execution_receipts", "close"),
            ("_unity_session_state_store", "clear"),
        ):
            owner = getattr(server, attribute, None)
            if owner is not None:
                cleanup.callback(getattr(owner, method))
        reference_graph = getattr(server, "_reference_graph_service", None)
        if reference_graph is not None:
            cleanup.push_async_callback(close_graph, reference_graph)
        if pool is not None:
            # Resync can publish to the catalog and own executor work. Retire it
            # before closing the hub, receipts, or any connection resources.
            cleanup.push_async_callback(pool.aclose)
        # Capture before the first cleanup await: a later lifespan may create
        # new scanner owners while this generation's worker is still draining.
        cleanup.push_async_callback(external_changes_scanner.begin_shutdown())
    logger.info("Unity MCP (ykh09242) server resources closed")


def _build_instructions(project_scoped_tools: bool) -> str:
    # Some clients repeat initialize instructions beside each tool. Keep shared
    # rules compact; detailed procedures live in the on-demand skill resources.
    custom_tools_note = (
        "Read mcpforunity://custom-tools only when project-specific capabilities are needed."
        if project_scoped_tools
        else "Custom tools appear as standard tools after Unity connects."
    )
    return f"""Unity Editor tools. Use the live schema and the smallest relevant observation before acting.

Targeting and discovery:
- Resolve the project through mcpforunity://instances. For ambiguous/multiple Editors, pass unity_instance=Name@hash on tools and _meta.unity_instance on resource reads. Modern sessionless clients require per-request targeting; only supported stateful clients can pin a default with set_active_instance.
- Resources use exact listed URIs, never names converted to paths. Unity payloads are normally under data. In Codex the resource server key is unityMCP; use the inventory's actual key in other clients.
- Use the client's exposed tool inventory. For missing optional tools, inspect manage_tools(action="list_groups").discovery before selection; it requires supported client scope/project binding. Static UNITY_MCP_TOOL_PROFILE=full is a configuration option, not permission to change setup. Editor toggles and consent still apply. {custom_tools_note}

State and execution:
- Read mcpforunity://editor/state when lifecycle readiness matters. data.advice.ready_for_tools is readiness, not compilation success. Check the job's compilation outcome/source confirmation and Console diagnostics before using new types. A refresh acknowledgement alone proves neither.
- Preserve job IDs across reconnects. On uncertain results inspect the job/target before repeating a mutation; never automatically replay. Failed batches and native file patches may have saved earlier changes: reread every touched target and retry only unapplied work. Follow retry/recovery guidance; recovery_required is not a transient wait. Keep dependent edits/validation ordered and inspect each batch result (not transactional).
- For tests in the current Play state, preserve the session/scenes/runtime objects: no restart, scene reload, temporary scripts or project refresh. Prefer dedicated tools; proactively use execute_code for authorized runtime work they cannot express when exposed, enabled and consented. Keep safety_checks=true, verify target/API and Play state, return bounded before/after values, and observe async effects without blocking waits. Verify input/UI paths with interaction tools. Never bypass consent or extend scope to persistent/external writes.
- For temporary state/resources, track ownership and lifetime. Arrange exception-safe restoration/cleanup after their last use with lifecycle-appropriate APIs; preserve requested results and unrelated runtime state.

Source edits:
- Native application of script_apply_edits(options.preview=true) requires proven same-host/workspace provenance, a complete candidate, fresh original hash and exact UTF-8 candidate bytes. Read skill://unity-mcp-orchestrator/references/scripts.md for the full handoff if available; otherwise use direct Unity edits. Verify raw bytes and get_sha before validation/refresh; never apply a truncated diff or fabricate file events. After edits, wait for verified compilation and inspect read_console.
- Verify installed Unity/package APIs and actual assets before writing code; use unity_reflect/unity_docs when available. Use exact returned targets, forward-slash asset paths and explicit save scope.

Keep context bounded:
- Read only relevant skill references via the advertised skill://unity-mcp-orchestrator/SKILL.md resource when available. Do not dump every tool schema or whole project catalog.
- Start with summaries and stop paging once the target is found: hierarchy page_size=50; components include_properties=false/page_size=10; asset search page_size=25/generate_preview=false. Fetch detailed properties, stacks, test failures or images only as needed. Prefer bounded Console reads and job summaries over repeatedly fetching full results; retain IDs/cursors for follow-up.
"""


class UnityMCP(ServerRuntimeOwner, FastMCP):
    """Protect the control plane for both run() and ASGI embedding."""

    def __init__(self, *args, runtime: ServerRuntime | None = None, **kwargs):
        self.runtime = runtime if runtime is not None else ServerRuntime()
        super().__init__(*args, **kwargs)

    def run(self, transport=None, show_banner=None, **transport_kwargs) -> None:
        if sys.platform != "win32":
            return super().run(transport, show_banner=show_banner, **transport_kwargs)
        # Preserve the IPv4 workaround (#853) without changing global loop policy.
        anyio.run(
            partial(
                run_with_wakeup_guard,
                partial(self.run_async, transport, show_banner=show_banner, **transport_kwargs),
            ),
            backend_options={"loop_factory": asyncio.SelectorEventLoop},
        )

    async def run_http_async(
        self,
        show_banner=True,
        transport="http",
        host=None,
        port=None,
        log_level=None,
        path=None,
        uvicorn_config=None,
        *args,
        **kwargs,
    ) -> None:
        if config.http_remote_hosted:
            # Auth must inspect the socket peer, including for WebSocket handshakes.
            # Never let Uvicorn or FORWARDED_ALLOW_IPS rewrite it before our boundary.
            uvicorn_config = {**(uvicorn_config or {}), "proxy_headers": False}
        await super().run_http_async(
            show_banner, transport, host, port, log_level, path, uvicorn_config, *args, **kwargs
        )

    async def run_stdio_async(self, show_banner=True, log_level=None, stateless=False) -> None:
        # FastMCP 4.1's runner has no public stream injection. This narrow
        # compatibility adapter preserves its lifecycle and initialization.
        from fastmcp.server.context import reset_transport, set_transport
        from fastmcp.utilities.logging import temporary_log_level
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
                            sdk_server.create_initialization_options(),
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
        session_idle_timeout: float | Literal["auto"] | None = None,
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
        if app.state.transport_type == "streamable-http":
            if getattr(self, "_test_job_tasks", None) is not None:
                app.add_middleware(WaitTaskDeliveryMiddleware)
            app.add_middleware(McpVersionMiddleware, path=app.state.path)
            app.add_middleware(CatalogSubscriptionLifetimeMiddleware)
        app.add_middleware(
            RequestBodyLimitMiddleware,
            max_body_size=MAX_HTTP_REQUEST_BYTES,
            hosted=config.http_remote_hosted,
            route_body_limits=(
                {app.state.path: mcp_request_body_limit(app)}
                if app.state.transport_type == "streamable-http"
                else None
            ),
        )
        app.add_middleware(
            ResponseRetentionMiddleware,
            legacy_sse_message_path=(
                fastmcp.settings.message_path if app.state.transport_type == "sse" else None
            ),
        )
        if not config.http_remote_hosted:
            app.add_middleware(LocalControlAuthMiddleware, token=config.local_auth_token)
        else:
            if app.state.transport_type == "streamable-http":
                RemoteSessionTerminationGuard.install(app)
            elif app.state.transport_type == "sse":
                app.add_middleware(
                    RemoteLegacySseGuard,
                    stream_path=app.state.path,
                    message_path=fastmcp.settings.message_path,
                )
            app.add_middleware(RemoteControlAuthMiddleware)
        return app


def create_mcp_server(
    project_scoped_tools: bool,
    *,
    managed_instance_token: str | None = None,
    custom_instructions: str | None = None,
    test_wait_tasks_root: Path | None = None,
    execution_receipts_root: Path | None = None,
    execution_receipts_private_root: bool = False,
) -> FastMCP:
    scene_inputs = prepare_scene_load_inputs()
    state_store = SessionStateStore()
    mcp = UnityMCP(
        name="mcp-for-unity-server",
        version=RUNNING_SERVER.version,
        lifespan=server_lifespan,
        session_state_store=state_store,
        instructions=append_custom_instructions(
            _build_instructions(project_scoped_tools), custom_instructions
        ),
        **({"request_state_security": scene_inputs.policy} if scene_inputs is not None else {}),
    )
    mcp._unity_session_state_store = state_store
    mcp._scene_load_inputs = scene_inputs
    mcp._scene_load_inputs_requested = os.environ.get("UNITY_MCP_SCENE_LOAD_INPUTS") == "1"
    mcp._scene_load_inputs_transport = config.transport_mode
    from services.reference_graph.service import ReferenceGraphService

    mcp._reference_graph_service = ReferenceGraphService()
    mcp.add_middleware(InventoryOrderingMiddleware())
    mcp.add_middleware(ResponseLimitMiddleware())
    install_execution_receipts(
        mcp, execution_receipts_root, private_root=execution_receipts_private_root
    )
    install_catalog_subscriptions(mcp, remote_hosted=config.http_remote_hosted)

    global custom_tool_service
    custom_tool_service = CustomToolService(mcp, project_scoped_tools=project_scoped_tools)

    mcp.runtime.custom_tools = custom_tool_service
    install_http_routes(
        mcp,
        mcp.runtime,
        managed_instance_token=managed_instance_token,
        resolve_project_id_for_unity_instance=lambda instance: (
            resolve_project_id_for_unity_instance(instance)
        ),
        execute_receipted_rest=lambda *args, **kwargs: execute_receipted_rest(*args, **kwargs),
        server_version=lambda: _server_version or RUNNING_SERVER.version,
    )

    # Initialize and register middleware for session-based Unity instance routing
    # Using the singleton getter ensures we use the same instance everywhere
    unity_middleware = get_unity_instance_middleware()
    mcp.add_middleware(unity_middleware)
    if scene_inputs is not None:
        mcp.add_middleware(scene_inputs)
    logger.info("Registered Unity instance middleware for session-based routing")

    # Initialize API key authentication if in remote-hosted mode
    if config.http_remote_hosted and config.api_key_validation_url:
        ApiKeyService(
            validation_url=config.api_key_validation_url,
            cache_ttl=config.api_key_cache_ttl,
            max_auth_age=config.api_key_max_auth_age,
            permissions_strict=config.api_key_permissions_strict,
            service_token_header=config.api_key_service_token_header,
            service_token=config.api_key_service_token,
        )
        logger.info(
            "Initialized API key authentication service (validation URL: %s, TTL: %.0fs)",
            endpoint_for_log(config.api_key_validation_url),
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
    install_test_results_app(mcp)
    mcp._skills_requested = True
    mcp._packaged_skills = install_skills(mcp)

    if test_wait_tasks_root is not None:
        install_test_job_tasks(mcp, test_wait_tasks_root, remote_hosted=config.http_remote_hosted)

    logger.info("SDK runtime: %s", json.dumps(runtime_sdk_report(mcp), separators=(",", ":")))
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
  UNITY_MCP_TOOL_PROFILE   Startup tool selection: minimal, core or full (local default: core)
  UNITY_MCP_HTTP_URL   HTTP server URL (default: http://127.0.0.1:8080)
  UNITY_MCP_HTTP_HOST   HTTP server host (overrides URL host)
  UNITY_MCP_HTTP_PORT   HTTP server port (overrides URL port)
  UNITY_MCP_HTTP_BEHIND_TLS_PROXY   Confirm the remote HTTP backend is private behind HTTPS/WSS

Examples:
  # Use specific Unity project as default
  mcp-for-unity --default-instance "MyProject"

  # Start with HTTP transport
  mcp-for-unity --transport http --http-url http://127.0.0.1:8080

  # Start with stdio transport (default)
  mcp-for-unity --transport stdio

  # Use environment variable for transport
  UNITY_MCP_TRANSPORT=http UNITY_MCP_HTTP_URL=http://localhost:9000 mcp-for-unity
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
        default=None,
        help="Transport protocol to use: stdio or http (default: stdio). "
        "Overrides UNITY_MCP_TRANSPORT environment variable.",
    )
    parser.add_argument(
        "--tool-profile",
        choices=["minimal", "core", "full"],
        default=None,
        help="Initial tool selection. Use full for clients that capture a fixed tool inventory. "
        "Overrides UNITY_MCP_TOOL_PROFILE; otherwise preserves the local core/hosted full default. "
        "Unity availability and permissions still apply.",
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
    if args.tool_profile is not None:
        os.environ["UNITY_MCP_TOOL_PROFILE"] = args.tool_profile
    if args.default_instance:
        os.environ["UNITY_MCP_DEFAULT_INSTANCE"] = args.default_instance
        logger.info(f"Using default Unity instance from command-line: {args.default_instance}")

    from core.config import configuration_trace
    from core.config_startup import argument_origin, cli_or_environment, environment_or_cli
    from core.config_provenance import safe_config_value
    from models.effective_config import ConfigOrigin

    configuration_trace.declare("http_url", "http://127.0.0.1:8080")
    configuration_trace.declare("http_host", "127.0.0.1")
    configuration_trace.declare("http_port", 8080)

    # Set transport mode
    transport_mode = (
        args.transport
        if args.transport is not None
        else os.environ.get("UNITY_MCP_TRANSPORT", "stdio")
    )
    if transport_mode not in {"stdio", "http"}:
        parser.error("transport must be stdio or http")
    config.transport_mode = transport_mode
    configuration_trace.record(
        "transport_mode",
        config.transport_mode,
        cli_or_environment("--transport", "UNITY_MCP_TRANSPORT", bool(args.transport)),
    )
    logger.info(f"Transport mode: {config.transport_mode}")

    config.http_remote_hosted = bool(args.http_remote_hosted) or os.environ.get(
        "UNITY_MCP_HTTP_REMOTE_HOSTED", ""
    ).lower() in ("true", "1", "yes", "on")
    config.http_behind_tls_proxy = bool(args.http_behind_tls_proxy) or os.environ.get(
        "UNITY_MCP_HTTP_BEHIND_TLS_PROXY", ""
    ).lower() in ("true", "1", "yes", "on")
    configuration_trace.record(
        "http_remote_hosted",
        config.http_remote_hosted,
        cli_or_environment(
            "--http-remote-hosted", "UNITY_MCP_HTTP_REMOTE_HOSTED", bool(args.http_remote_hosted)
        ),
    )
    configuration_trace.record(
        "http_behind_tls_proxy",
        config.http_behind_tls_proxy,
        cli_or_environment(
            "--http-behind-tls-proxy",
            "UNITY_MCP_HTTP_BEHIND_TLS_PROXY",
            bool(args.http_behind_tls_proxy),
        ),
    )
    config.http_trusted_proxy_networks = ()
    if config.http_remote_hosted:
        try:
            config.http_trusted_proxy_networks = parse_trusted_proxy_cidrs(
                os.environ.get("UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS", "")
            )
        except ValueError as error:
            logger.error("%s", error)
            raise SystemExit(1) from None

    configuration_trace.record(
        "http_trusted_proxy_networks",
        [str(network) for network in config.http_trusted_proxy_networks],
        ConfigOrigin(
            source="environment"
            if config.http_remote_hosted and "UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS" in os.environ
            else "default",
            source_detail="UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS"
            if config.http_remote_hosted and "UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS" in os.environ
            else "built_in",
        ),
    )

    # API key authentication configuration
    config.api_key_validation_url = args.api_key_validation_url or os.environ.get(
        "UNITY_MCP_API_KEY_VALIDATION_URL"
    )
    config.api_key_login_url = args.api_key_login_url or os.environ.get(
        "UNITY_MCP_API_KEY_LOGIN_URL"
    )
    for name, option, environment in (
        ("api_key_validation_url", "--api-key-validation-url", "UNITY_MCP_API_KEY_VALIDATION_URL"),
        ("api_key_login_url", "--api-key-login-url", "UNITY_MCP_API_KEY_LOGIN_URL"),
    ):
        configuration_trace.record(
            name,
            getattr(config, name),
            cli_or_environment(option, environment, bool(getattr(args, name))),
        )
    try:
        cache_ttl_env = os.environ.get("UNITY_MCP_API_KEY_CACHE_TTL")
        config.api_key_cache_ttl = float(cache_ttl_env) if cache_ttl_env else args.api_key_cache_ttl
        if not math.isfinite(config.api_key_cache_ttl) or config.api_key_cache_ttl < 0:
            raise ValueError("Cache TTL must be finite and nonnegative")
        cache_origin = (
            environment_or_cli("UNITY_MCP_API_KEY_CACHE_TTL", "--api-key-cache-ttl")
            if cache_ttl_env
            else argument_origin("--api-key-cache-ttl")
        )
        if cache_ttl_env == "":
            cache_origin = cache_origin.model_copy(
                update={"diagnostics": ("empty_environment_ignored",)}
            )
    except ValueError:
        logger.warning("Invalid UNITY_MCP_API_KEY_CACHE_TTL value, using default 300.0")
        config.api_key_cache_ttl = 300.0
        cache_origin = ConfigOrigin(
            diagnostics=("invalid_environment_ignored",)
            if cache_ttl_env
            else ("invalid_cli_ignored",)
        )
    configuration_trace.record("api_key_cache_ttl", config.api_key_cache_ttl, cache_origin)

    try:
        max_age_env = os.environ.get("UNITY_MCP_API_KEY_MAX_AUTH_AGE")
        config.api_key_max_auth_age = (
            float(max_age_env) if max_age_env else config.api_key_max_auth_age
        )
        if not math.isfinite(config.api_key_max_auth_age) or config.api_key_max_auth_age <= 0:
            raise ValueError("Maximum authentication age must be finite and positive")
        age_origin = (
            ConfigOrigin(
                source="environment",
                source_detail="UNITY_MCP_API_KEY_MAX_AUTH_AGE",
                overrides=("built_in",),
            )
            if max_age_env
            else configuration_trace.origin("api_key_max_auth_age", config.api_key_max_auth_age)
        )
        if max_age_env == "":
            age_origin = age_origin.model_copy(
                update={"diagnostics": (*age_origin.diagnostics, "empty_environment_ignored")}
            )
    except ValueError:
        logger.warning("Invalid maximum authentication age, using default 300.0")
        config.api_key_max_auth_age = 300.0
        age_origin = ConfigOrigin(diagnostics=("invalid_environment_ignored",))
    configuration_trace.record("api_key_max_auth_age", config.api_key_max_auth_age, age_origin)

    # Service token for authenticating to validation endpoint
    config.api_key_service_token_header = args.api_key_service_token_header or os.environ.get(
        "UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER"
    )
    config.api_key_service_token = args.api_key_service_token or os.environ.get(
        "UNITY_MCP_API_KEY_SERVICE_TOKEN"
    )

    for name, option, environment in (
        (
            "api_key_service_token_header",
            "--api-key-service-token-header",
            "UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER",
        ),
        ("api_key_service_token", "--api-key-service-token", "UNITY_MCP_API_KEY_SERVICE_TOKEN"),
    ):
        configuration_trace.record(
            name,
            getattr(config, name),
            cli_or_environment(option, environment, bool(getattr(args, name))),
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
        logger.warning("Invalid UNITY_MCP_HTTP_PORT value, ignoring")
        _env_port = None

    http_port = args.http_port or _env_port or parsed_url.port or 8080

    url_origin = (
        environment_or_cli("UNITY_MCP_HTTP_URL", "--http-url")
        if "UNITY_MCP_HTTP_URL" in os.environ
        else argument_origin("--http-url")
    )
    configuration_trace.record("http_url", http_url, url_origin)
    host_origin = cli_or_environment("--http-host", "UNITY_MCP_HTTP_HOST", bool(args.http_host))
    if not args.http_host and not os.environ.get("UNITY_MCP_HTTP_HOST"):
        host_origin = (
            url_origin.model_copy(update={"source_detail": "http_url.hostname"})
            if parsed_url.hostname
            else ConfigOrigin()
        )
    configuration_trace.record("http_host", http_host, host_origin)
    port_origin = (
        argument_origin("--http-port")
        if args.http_port
        else ConfigOrigin(source="environment", source_detail="UNITY_MCP_HTTP_PORT")
        if _env_port
        else url_origin.model_copy(update={"source_detail": "http_url.port"})
        if parsed_url.port
        else ConfigOrigin()
    )
    port_diagnostics = (
        ("invalid_environment_ignored",)
        if _env_port_str is not None and _env_port is None
        else ("zero_environment_ignored",)
        if _env_port == 0
        else ()
    )
    if _env_port_str == "":
        port_diagnostics = ("empty_environment_ignored",)
    if args.http_port and _env_port_str is not None:
        port_origin = port_origin.model_copy(
            update={"overrides": ("UNITY_MCP_HTTP_PORT", "http_url.port")}
        )
    configuration_trace.record(
        "http_port", http_port, port_origin.model_copy(update={"diagnostics": port_diagnostics})
    )

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
        logger.info("HTTP URL set to: %s", safe_config_value("http_url", http_url))
    if args.http_host:
        logger.info("HTTP host override: %s", safe_config_value("http_host", http_host))
    if args.http_port:
        logger.info(f"HTTP port override: {http_port}")

    # Explicit CLI/env overrides always win
    project_scoped_tools_explicit = bool(args.project_scoped_tools) or os.environ.get(
        "UNITY_MCP_PROJECT_SCOPED_TOOLS", ""
    ).lower() in ("true", "1", "yes", "on")

    # If not explicitly set, check Unity status files for the default instance.
    # In stdio mode there is typically only one instance, so "first match wins" is fine.
    project_scoped_tools = project_scoped_tools_explicit
    startup_deadline = time.monotonic() + STARTUP_CONNECT_TIMEOUT
    startup_connect = os.environ.get("UNITY_MCP_SKIP_STARTUP_CONNECT", "").lower() not in (
        "1",
        "true",
        "yes",
        "on",
    )
    if (
        not project_scoped_tools_explicit
        and config.transport_mode == "stdio"
        and startup_connect
        and not config.http_remote_hosted
    ):
        try:
            from transport.legacy.unity_connection import get_unity_connection_pool

            def discover_defaults():
                return get_unity_connection_pool().discover_all_instances()

            instances = run_with_connection_deadline(discover_defaults, startup_deadline)
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
        execution_receipts_root=(
            Path(os.environ["UNITY_MCP_EXECUTION_RECEIPTS_DIR"])
            if os.environ.get("UNITY_MCP_EXECUTION_RECEIPTS_DIR")
            else None
        ),
        execution_receipts_private_root=(
            os.environ.get("UNITY_MCP_EXECUTION_RECEIPTS_PRIVATE_ROOT") == "1"
        ),
        test_wait_tasks_root=(
            Path(os.environ["UNITY_MCP_TEST_WAIT_TASKS_DIR"])
            if config.transport_mode == "http" and os.environ.get("UNITY_MCP_TEST_WAIT_TASKS_DIR")
            else None
        ),
        managed_instance_token=(
            args.unity_instance_token
            if args.pidfile and config.transport_mode == "http" and not config.http_remote_hosted
            else None
        ),
    )

    # CLI default detection and lifespan I/O consume one original startup budget.
    mcp._unity_startup_deadline = startup_deadline

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
            with local_http_sockets(host, port) as sockets, local_auth_token(port) as token:
                config.local_auth_token = token
                configuration_trace.record(
                    "local_auth_token",
                    token,
                    ConfigOrigin(source="runtime", source_detail="local_auth_context"),
                )
                logger.info(
                    "Local HTTP authentication enabled; token file: %s",
                    local_auth_token_path(port),
                )
                try:
                    mcp.run(transport=transport, host=host, port=port, sockets=sockets)
                finally:
                    config.local_auth_token = None
                    configuration_trace.record(
                        "local_auth_token",
                        None,
                        ConfigOrigin(source="runtime", source_detail="local_auth_context_closed"),
                    )
    else:
        # Use stdio transport for traditional MCP
        logger.info("Starting FastMCP with stdio transport")
        mcp.run(transport="stdio")


# Run the server
if __name__ == "__main__":
    main()
