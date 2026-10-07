"""Validate installed server registration without starting a listener or Unity.

Run in an isolated environment with the server installed, not via its source path:
    uvx --from <server-source> python tools/check_server_startup.py
"""

import asyncio
from importlib.metadata import version
import json
import logging
import os
import sys
from tempfile import TemporaryDirectory


def reject_network(event, _args):
    if event in {"socket.bind", "socket.connect"}:
        raise RuntimeError(f"Startup validation must not use network sockets: {event}")


async def check_registration():
    # Windows creates internal wakeup sockets while initializing the event loop.
    sys.addaudithook(reject_network)
    from core.config import config
    from main import create_mcp_server

    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    server = create_mcp_server(project_scoped_tools=False)
    tools = await server.list_tools(run_middleware=False)
    resources = await server.list_resources(run_middleware=False)
    templates = await server.list_resource_templates(run_middleware=False)
    if not {"manage_scene", "read_console"} <= {tool.name for tool in tools}:
        raise RuntimeError("Required MCP tools were not registered")
    if not resources or not templates:
        raise RuntimeError("MCP resources or templates were not registered")
    return {"tools": len(tools), "resources": len(resources), "templates": len(templates)}


def main():
    os.environ["DISABLE_TELEMETRY"] = "true"
    os.environ["UNITY_MCP_TELEMETRY_ENABLED"] = "false"
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "server": version("ykh09242-unity-mcp-server"),
                "fastmcp": version("fastmcp"),
                "mcp": version("mcp"),
                "griffelib": version("griffelib"),
            }
        ),
        flush=True,
    )
    with TemporaryDirectory(prefix="unity-mcp-startup-") as log_dir:
        os.environ["UNITY_MCP_LOG_DIR"] = log_dir
        try:
            print(json.dumps(asyncio.run(check_registration())), flush=True)
        finally:
            logging.shutdown()


if __name__ == "__main__":
    main()
