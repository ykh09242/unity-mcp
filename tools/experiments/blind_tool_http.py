"""Bridge a local Unity MCP endpoint to a project-scoped, read-only stdio connection."""

from __future__ import annotations

import argparse
import sys
from base64 import b64decode
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.resources.base import ResourceContent, ResourceResult
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient
from fastmcp.tools.base import ToolResult
from mcp_types import TextResourceContents

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_trace import (
    PROJECT_INFO,
    decision,
    json_object,
    mapping,
    route_name,
)


def loopback_port(endpoint: str) -> tuple[str, int]:
    """Reject endpoint URI fields that could divert or disclose local authentication."""
    uri = urlsplit(endpoint)
    host = uri.hostname or ""
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            pass
    if (
        uri.scheme != "http"
        or not loopback
        or uri.username is not None
        or uri.password is not None
        or uri.query
        or uri.fragment
    ):
        raise ValueError("Use an HTTP loopback MCP endpoint without credentials or query")
    return host, uri.port or 80


def local_http_client(**options: Any) -> httpx2.AsyncClient:
    """Keep loopback authentication on the configured endpoint."""
    return httpx2.AsyncClient(**(options | {"trust_env": False, "follow_redirects": False}))


class ScopedObservation(Middleware):
    """Use the authentic server catalog with each Unity read pinned to the operator's target."""

    def __init__(
        self,
        client: StatefulProxyClient,
        instance: str,
        project: Path,
        graph_asset: str,
        fixture=None,
    ) -> None:
        self.client = client
        self.instance = instance
        self.project = project.resolve()
        self.graph_asset = graph_asset
        self.fixture = fixture
        self.verified = False

    async def verify_target(self, backend: StatefulProxyClient) -> None:
        """Require the selected Unity instance to report the requested project root."""
        if self.verified:
            return
        result = await backend.read_resource_mcp(
            PROJECT_INFO, meta={"unity_instance": self.instance}
        )
        content = result.contents[0] if result.contents else None
        if not isinstance(content, TextResourceContents):
            raise ResourceError("The selected Editor could not verify its project root")
        response = json_object(content.text)
        root = mapping(response.get("data")).get("projectRoot")
        if not (response.get("success") is True and isinstance(root, str) and root):
            raise ResourceError("The selected Editor could not verify its project root")
        if Path(root).resolve() != self.project:
            raise ResourceError("The selected Editor reported a different project root")
        self.verified = True

    async def on_call_tool(self, context, call_next) -> ToolResult:
        name = context.message.name
        args = dict(context.message.arguments or {})
        route, _ = route_name("tools/call", {"name": name, "arguments": args})
        channel = decision(route, args, self.graph_asset, endpoint=True, fixture=self.fixture)
        selector = args.pop("unity_instance", self.instance)
        if selector != self.instance or channel == "blocked":
            raise ToolError("Outside this project's permitted observations")
        if name == "find_gameobjects":
            args["refresh_if_dirty"] = False
        if channel == "unity" or name in {"manage_tools", "search_unity_tools"}:
            async with self.client.new_stateful() as backend:
                if channel == "unity" or name == "search_unity_tools":
                    await self.verify_target(backend)
                result = await backend.call_tool_mcp(
                    name, {**args, "unity_instance": self.instance}
                )
            return ToolResult.from_mcp_result(result)
        return await call_next(context)

    async def on_read_resource(self, context, call_next) -> ResourceResult:
        uri = str(context.message.uri)
        route, args = route_name("resources/read", {"uri": uri})
        channel = decision(route, args, self.graph_asset, endpoint=True)
        meta = context.fastmcp_context.request_context.meta or {}
        if meta.get("unity_instance", self.instance) != self.instance or channel == "blocked":
            raise ResourceError("Outside this project's permitted observations")
        if channel != "unity" and uri != "mcpforunity://doctor":
            return await call_next(context)
        async with self.client.new_stateful() as backend:
            await self.verify_target(backend)
            result = await backend.read_resource_mcp(uri, meta={"unity_instance": self.instance})
        return ResourceResult(
            [
                ResourceContent(
                    item.text if isinstance(item, TextResourceContents) else b64decode(item.blob),
                    item.mime_type,
                    item.meta,
                )
                for item in result.contents
            ],
            meta=result.meta,
        )


def create_observation_proxy(
    transport: StreamableHttpTransport, instance: str, project: Path, graph_asset: str, fixture=None
) -> FastMCPProxy:
    """Keep upstream identity and catalog while guarding each forwarded observation."""
    client = StatefulProxyClient(transport, forward_incoming_headers=False, timeout=40.0)
    proxy = FastMCPProxy(client_factory=client.new_stateful, identity="upstream", skills=True)
    proxy.add_middleware(ScopedObservation(client, instance, project, graph_asset, fixture))
    return proxy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint")
    parser.add_argument("instance")
    parser.add_argument("project", type=Path)
    parser.add_argument("graph_asset")
    parser.add_argument("fixture", nargs="?", default="{}")
    args = parser.parse_args()
    host, port = loopback_port(args.endpoint)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))
    from core.local_auth import LOCAL_AUTH_HEADER, read_local_auth_token

    token = read_local_auth_token(host, port)
    if not token:
        raise SystemExit("Local Unity MCP authentication is unavailable")
    transport = StreamableHttpTransport(
        args.endpoint, headers={LOCAL_AUTH_HEADER: token}, httpx_client_factory=local_http_client
    )
    proxy = create_observation_proxy(
        transport, args.instance, args.project, args.graph_asset, json_object(args.fixture)
    )
    proxy.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
