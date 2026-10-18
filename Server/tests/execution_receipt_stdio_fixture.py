"""Controlled SDK server used only by the owned stdio receipt regression."""

from contextlib import asynccontextmanager
from pathlib import Path
import sys
from unittest.mock import patch

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp_types import CallToolResult, ImageContent, TextContent

from core.config import config
from transport.execution_receipts import install_execution_receipts
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.stdio_response_delivery import retained_stdio_server
from transport.unity_instance_middleware import UnityInstanceMiddleware

PROJECT = "0123456789abcdef"


class ControlledSelection(UnityInstanceMiddleware):
    async def _inject_unity_instance(self, context, **kwargs):
        context.message.arguments.pop("unity_instance", None)
        await context.fastmcp_context.set_state(
            "unity_instance", "Controlled@" + PROJECT, serializable=False
        )


def create_server(root: Path):
    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    counts = {"body": 0}

    @asynccontextmanager
    async def lifespan(server):
        runtime = server._execution_receipts
        await runtime.start()
        try:
            yield {}
        finally:
            runtime.close()

    server = FastMCP("receipt-stdio-test", lifespan=lifespan)
    install_execution_receipts(server, root, private_root=True)
    server.add_middleware(ResponseLimitMiddleware())
    server.add_middleware(ControlledSelection())

    @server.tool(name="receipt_exercise")
    async def exercise(checked: int, as_error: bool = False) -> ToolResult:
        counts["body"] += 1
        return ToolResult.from_mcp_result(
            CallToolResult(
                content=[
                    TextContent(type="text", text="retained stdio application"),
                    ImageContent(type="image", data="AA==", mime_type="image/png"),
                ],
                structured_content={"checked": checked},
                is_error=as_error,
                meta={"application": {"explicit_sensitive_output": "retain exactly"}},
            )
        )

    @server.tool(name="body_count")
    async def body_count() -> dict[str, int]:
        return counts.copy()

    return server


if __name__ == "__main__":
    # Same narrow retained-stream substitution used by the accepted SDK probe.
    # Parsing, validation, dispatch and serialization remain public FastMCP.run.
    with patch("fastmcp.server.mixins.transport.stdio_server", retained_stdio_server):
        create_server(Path(sys.argv[1])).run(transport="stdio", show_banner=False)
