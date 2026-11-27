from itertools import islice
from typing import Annotated, Any, Literal
import os
import sys

from core.config import config
from core.config_provenance import safe_config_value
from core.server_build import RUNNING_SERVER

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_instance_middleware import get_unity_instance_middleware
from transport.plugin_hub import PluginHub

_SECRET_FLAG_MARKERS = ("token", "secret", "password", "api-key", "api_key", "apikey")


def _redact_argv(argv: list[str]) -> list[str]:
    """Keep deployment flags while hiding credential values in local process diagnostics."""
    # Every secret-bearing flag the server accepts takes a value, so the token after a bare
    # flag is always that value, even when it happens to start with "-".
    out: list[str] = []
    pending: str | None = None
    for arg in argv:
        if pending:
            out.append(str(safe_config_value("http_url", arg)) if pending == "url" else "***")
            pending = None
            continue
        name, sep, value = arg.partition("=")
        secret = name.startswith("-") and any(m in name.lower() for m in _SECRET_FLAG_MARKERS)
        if name.startswith("--u") and "--unity-instance-token".startswith(name):
            secret = True
        # argparse also accepts an unambiguous prefix such as --http-u.
        url = name.startswith("--h") and "--http-url".startswith(name)
        if not (url or secret):
            out.append(arg)
        elif sep:
            safe = safe_config_value("http_url", value) if url else "***"
            out.append(f"{name}={safe}")
        else:
            out.append(name)
            pending = "url" if url else "secret"
    return out


def _client_metadata(meta) -> Any:
    """Bound the client-provided extensions, applying diagnostic redaction before traversal."""
    if not isinstance(meta, dict):
        meta = getattr(meta, "model_extra", None)
    remaining = 128

    def safe(key, value, depth):
        nonlocal remaining
        remaining -= 1
        normalized = key.lower().replace("-", "_")
        if (
            any(marker in normalized for marker in ("cookie", "key", "passphrase", "passwd"))
            or safe_config_value(normalized, "present") == "<redacted>"
        ):
            return "<redacted>"
        if isinstance(value, (dict, list)):
            if depth >= 5:
                return "<truncated>"
            if isinstance(value, list):
                return [safe(key, v, depth + 1) for v in islice(value, 32) if remaining > 0]
            return {
                k[:128]: safe(k, v, depth + 1)
                for k, v in islice(value.items(), 32)
                if isinstance(k, str) and remaining > 0
            }
        if value is None or isinstance(value, (str, bool, float, int)):
            return safe_config_value(normalized, value)
        return "<unavailable>"

    return safe("meta", meta, 0)


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description="Inspect this connection's server build and routed Unity Editor. Use manage_tools prepare for a particular call's server permission evidence.",
    annotations=ToolAnnotations(
        title="Debug Request Context",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def debug_request_context(
    ctx: Context,
    detail: Annotated[
        Literal["routing", "client", "server"],
        "Routing is the default. Request client for bounded, redacted metadata supplied by this caller, "
        "or server for local process paths and arguments.",
    ] = "routing",
) -> dict[str, Any]:
    middleware = get_unity_instance_middleware()
    active_instance = await middleware.get_active_instance(ctx)
    routed_instance = await get_unity_instance_from_context(ctx)
    server = {
        **RUNNING_SERVER.command_metadata(),
        "version_guidance": (
            "Compare source_commit with the Unity package's selected pinned server source. "
            "Unity package and Python server versions are independent. After updating, "
            "restart this client's MCP server connection. Reconfigure only when you intend "
            "to change its source; preserve intentional manual version pins."
        ),
    }
    data = {
        "server": server,
        "connection": {
            "transport": config.transport_mode or "stdio",
            "mode": "remote_hosted" if config.http_remote_hosted else "local",
        },
        "editor_route": {
            "unity_instance": routed_instance,
            "edit_authorization": "not_tested",
            "scope": "The route identifies this request's Editor. Each operation checks its own permissions.",
        },
        "session_state": {
            "active_instance": active_instance,
            "plugin_hub_configured": PluginHub.is_configured(),
        },
    }
    if detail == "client":
        rc = getattr(ctx, "request_context", None)
        data["request_context"] = {
            "meta": _client_metadata(getattr(rc, "meta", None)),
            "meta_source": "Caller-provided request metadata. Editor permissions are evaluated separately.",
        }
    elif detail == "server":
        if config.http_remote_hosted:
            server["process_details"] = "local_only"
        else:
            server.update(
                cwd=os.getcwd(),
                cwd_scope="MCP server process working directory",
                argv=_redact_argv(sys.argv),
            )
    return {"success": True, "data": data}
