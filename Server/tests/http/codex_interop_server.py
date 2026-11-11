"""Production CLI with test-only pressure tools for the actual Codex MCP client."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import anyio
from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp_types import TextContent

import main
from services.registry import mcp_for_unity_tool
from tests.http.client_assurance_server import build_server as assurance_server

STARTUP = uuid4().hex


def observe(event: str, **values) -> None:
    """Append bounded fixture evidence, never credentials or arbitrary tool arguments."""
    # Each child owns its journal; Windows append handles do not serialize
    # independent processes' writes to one shared JSONL file.
    path = Path(os.environ["UNITY_MCP_INTEROP_OBSERVATIONS"]).with_name(
        f"observations-{STARTUP}.jsonl"
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "event": event,
                    "observed_ns": time.monotonic_ns(),
                    "pid": os.getpid(),
                    "startup": STARTUP,
                    **values,
                }
            )
            + "\n"
        )


@mcp_for_unity_tool(group=None, unity_target=None)
async def interop_identity(ctx: Context) -> dict:
    """Observe this process and request; host metadata grants no project authority."""
    request = ctx.request_context
    metadata = request.meta or {}
    return {
        "pid": os.getpid(),
        "startup": STARTUP,
        "protocol": request.protocol_version,
        "request_id": request.request_id,
        "meta": {key: metadata.get(key) for key in ("threadId", "interopRequest")},
        "unity_instance": await ctx.get_state("unity_instance"),
    }


@mcp_for_unity_tool(group=None, unity_target=None)
async def interop_echo(value: str) -> dict[str, str]:
    """Return a validated argument without a Unity connection."""
    return {"value": value}


@mcp_for_unity_tool(group=None, unity_target=None)
async def interop_error() -> str:
    """Produce a deliberate MCP tool error."""
    raise ToolError("interop deliberate failure")


@mcp_for_unity_tool(group=None, unity_target=None)
async def interop_payload(size: int, unicode: bool = False) -> ToolResult:
    """Return bounded test text through the unmodified response middleware."""
    if not 0 <= size <= 10 * 1024 * 1024:
        raise ToolError("interop size outside fixture bounds")
    text = ("한" if unicode else "x") * size
    return ToolResult(content=[TextContent(type="text", text=text)])


@mcp_for_unity_tool(group=None, unity_target=None)
async def interop_slow(ctx: Context) -> str:
    """Observe whether the actual client's deadline cancels the in-flight tool."""
    metadata = ctx.request_context.meta or {}
    correlation = {"marker": metadata.get("interopRequest"), "thread": metadata.get("threadId")}
    observe("slow_started", **correlation)
    try:
        await anyio.sleep(60)
    except anyio.get_cancelled_exc_class():
        observe("slow_cancelled", **correlation)
        raise
    finally:
        observe("slow_finished", **correlation)
    return "finished"


class ObserveInitialize(Middleware):
    """Record only negotiated protocol facts in an owned evidence file."""

    async def on_request(self, context, call_next):
        ctx = context.fastmcp_context
        request = ctx.request_context if ctx is not None else None
        if request is not None:
            observe("request", method=context.method, protocol=request.protocol_version)
        result = await call_next(context)
        capabilities = getattr(result, "capabilities", None)
        if capabilities is not None:
            values = capabilities.model_dump()
            observe(
                "advertisement",
                method=context.method,
                extensions=sorted(values.get("extensions") or {}),
            )
        return result

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            observe(
                "initialize",
                requested=context.message.params.protocol_version,
                negotiated=result.protocol_version,
                client=context.message.params.client_info.model_dump(),
            )
        return result


def build_server(*args, **kwargs):
    """Keep the real CLI, registry, authentication and response middleware."""
    server = assurance_server(*args, **kwargs)
    server.add_middleware(ObserveInitialize())
    observe("process_started")
    return server


if __name__ == "__main__":
    main.create_mcp_server = build_server
    main.main()
