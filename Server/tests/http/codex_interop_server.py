"""Production CLI with test-only pressure tools for the actual Codex MCP client."""

import json
import os
from pathlib import Path

import anyio
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp_types import TextContent

import main
from services.registry import mcp_for_unity_tool
from tests.http.client_assurance_server import build_server as assurance_server


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
async def interop_slow() -> str:
    """Observe whether the actual client's deadline cancels the in-flight tool."""
    path = Path(os.environ["UNITY_MCP_INTEROP_OBSERVATIONS"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "slow_started"}) + "\n")
    try:
        await anyio.sleep(60)
    except anyio.get_cancelled_exc_class():
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "slow_cancelled"}) + "\n")
        raise
    finally:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "slow_finished"}) + "\n")
    return "finished"


class ObserveInitialize(Middleware):
    """Record only negotiated protocol facts in an owned evidence file."""

    async def on_request(self, context, call_next):
        ctx = context.fastmcp_context
        request = ctx.request_context if ctx is not None else None
        if request is not None:
            with Path(os.environ["UNITY_MCP_INTEROP_OBSERVATIONS"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "request",
                            "method": context.method,
                            "protocol": request.protocol_version,
                        }
                    )
                    + "\n"
                )
        return await call_next(context)

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            with Path(os.environ["UNITY_MCP_INTEROP_OBSERVATIONS"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "initialize",
                            "requested": context.message.params.protocol_version,
                            "negotiated": result.protocol_version,
                            "client": context.message.params.client_info.model_dump(),
                        }
                    )
                    + "\n"
                )
        return result


def build_server(*args, **kwargs):
    """Keep the real CLI, registry, authentication and response middleware."""
    server = assurance_server(*args, **kwargs)
    server.add_middleware(ObserveInitialize())
    return server


if __name__ == "__main__":
    main.create_mcp_server = build_server
    main.main()
