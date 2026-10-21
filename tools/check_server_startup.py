"""Validate installed server registration without starting a listener or Unity.

Run in an isolated environment with the server installed, not via its source path:
    uvx --from <server-source> python tools/check_server_startup.py
"""

import asyncio
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
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
    from core.sdk_compatibility import runtime_sdk_report

    # Verify the installed package's optional kernels and owned stores, not just imports.
    # Enter only their lifespans: the full server lifespan would attempt Unity discovery.
    with TemporaryDirectory(prefix="unity-mcp-sdk-") as store_dir:
        root = Path(store_dir)
        server = create_mcp_server(
            project_scoped_tools=False,
            test_wait_tasks_root=root / "waits",
            execution_receipts_root=root / "receipts",
            execution_receipts_private_root=True,
        )
        receipts = server._execution_receipts
        tasks = getattr(server, "_test_job_tasks", None)
        try:
            if tasks is None:
                raise RuntimeError("Reviewed Tasks adapter did not install")
            await receipts.start()
            async with tasks.lifespan():
                report = runtime_sdk_report(server)
                if not report["sdk"]["supported"] or any(
                    feature["state"] != "active" for feature in report["features"].values()
                ):
                    raise RuntimeError("Installed SDK set or optional adapter activation differs")
                tools = await server.list_tools(run_middleware=False)
                resources = await server.list_resources(run_middleware=False)
                templates = await server.list_resource_templates(run_middleware=False)
                required_tools = {
                    "asset_inspect",
                    "gameobject_create",
                    "gameobject_edit",
                    "manage_tools",
                    "prefab_inspect",
                    "read_console",
                    "scene_inspect",
                    "scene_lifecycle",
                }
                missing_tools = required_tools - {tool.name for tool in tools}
                if missing_tools:
                    raise RuntimeError(
                        f"Required MCP tools were not registered: {sorted(missing_tools)}"
                    )
                if not resources or not templates:
                    raise RuntimeError("MCP resources or templates were not registered")
                if "mcpforunity://doctor/runtime" not in {str(item.uri) for item in resources}:
                    raise RuntimeError("Runtime SDK doctor was not registered")
                return {
                    "tools": len(tools),
                    "resources": len(resources),
                    "templates": len(templates),
                    "runtime": report,
                }
        finally:
            receipts.close()
            if tasks is not None:
                await tasks.close()


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
