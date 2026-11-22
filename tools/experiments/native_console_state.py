"""Read an explicitly selected disposable Editor's Console via an independent MCP session.

Run with the Server Python environment. Pass the disposable Unity project as the sole
argument for stdio, or add --endpoint and --instance to use its local HTTP server.
Use this script as blind_tool_selection.py's --state-probe. UNITY_MCP_STATUS_DIR can
select an isolated stdio status directory. The Editor must expose read_console. For
individual occurrences disable Collapse in the fixture's Console before seeding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "Server" / "src" / "main.py"
PAGE_SIZE = 50
TYPES = {"error": {"Error", "Exception", "Assert"}, "warning": {"Warning"}}


def exact_instance(instance):
    if not isinstance(instance, str):
        return False
    name, _, project_hash = instance.rpartition("@")
    return bool(
        name
        and "/" not in name
        and "\\" not in name
        and re.fullmatch(r"[0-9a-fA-F]{8}(?:[0-9a-fA-F]{8})?", project_hash)
    )


def matches_path(value, expected):
    return (
        isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve() == expected
    )


def project_instance(inventory, project, http_instance=None):
    if http_instance is not None:
        if inventory.get("transport") != "http" or not exact_instance(http_instance):
            raise ValueError("Expected local HTTP inventory and an exact Editor instance")
        if sum(item.get("id") == http_instance for item in inventory["instances"]) != 1:
            raise ValueError("HTTP inventory must identify exactly one requested Editor")
        return http_instance
    if inventory.get("transport") != "stdio":
        raise ValueError("Expected a local stdio instance inventory")
    matches = [
        item
        for item in inventory["instances"]
        if item.get("status") == "running" and matches_path(item.get("path"), project / "Assets")
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one running Editor for this project")
    instance = matches[0]["id"]
    project_name = Path(matches[0]["path"]).parent.name
    if not exact_instance(instance) or instance.rpartition("@")[0] != project_name:
        raise ValueError("Editor discovery did not identify an exact instance")
    return instance


def success(blocks):
    if len(blocks) != 1:
        raise ValueError("Expected a single MCP response")
    body = json.loads(blocks[0].text)
    if not isinstance(body, dict) or body.get("success") is not True:
        raise ValueError("Editor observation did not succeed")
    return body


async def collect(call, severity):
    messages = []
    while True:
        cursor = len(messages)
        result = await call(
            "read_console",
            {
                "action": "get",
                "types": [severity],
                "format": "json",
                "fields": ["type", "message"],
                "include_mcp_logs": True,
                "page_size": PAGE_SIZE,
                "cursor": cursor,
            },
        )
        page = result["data"]
        items = page["items"]
        if (
            type(page["cursor"]) is not int
            or page["cursor"] != cursor
            or page["pageSize"] != PAGE_SIZE
            or type(page["truncated"]) is not bool
            or type(page["total"]) is not int
            or not isinstance(items, list)
            or len(items) > PAGE_SIZE
        ):
            raise ValueError("Inconsistent Console page")
        for row in items:
            if row["type"] not in TYPES[severity] or not isinstance(row["message"], str):
                raise ValueError("Unexpected Console row")
            messages.append(row["message"])
        if not page["truncated"]:
            if page.get("nextCursor") is not None or page["total"] != len(messages):
                raise ValueError("Incomplete terminal Console page")
            return sorted(messages)
        # A nonterminal native total may include only one lookahead match.
        if (
            len(items) != PAGE_SIZE
            or page["nextCursor"] != str(len(messages))
            or page["total"] <= len(messages)
        ):
            raise ValueError("Incomplete Console continuation")


async def observe(session, project, http_instance=None):
    inventory_result = await session.read_resource("mcpforunity://instances")
    instance = project_instance(success(inventory_result.contents), project, http_instance)

    async def call(name, args):
        reply = await session.call_tool(name, {**args, "unity_instance": instance})
        if reply.is_error:
            raise ValueError(f"Editor refused {name}")
        return success(reply.content)

    project_result = await session.read_resource(
        "mcpforunity://project/info", meta={"unity_instance": instance}
    )
    info = success(project_result.contents)["data"]
    if not matches_path(info.get("projectRoot"), project) or not matches_path(
        info.get("assetsPath"), project / "Assets"
    ):
        raise ValueError("Native project identity does not match the requested project")

    async def inventory():
        return {"errors": await collect(call, "error"), "warnings": await collect(call, "warning")}

    rows = await inventory()
    if await inventory() != rows:
        raise ValueError("Console changed during observation")
    return {"observation_fixtures": {"console-errors": rows}}


async def run(project, endpoint=None, instance=None):
    if (endpoint is None) != (instance is None):
        raise ValueError("Provide --endpoint and --instance together")
    if endpoint is not None:
        if not exact_instance(instance):
            raise ValueError("HTTP requires the exact Editor instance Name@hash")
        sys.path[:0] = [path for path in (str(ROOT), str(SERVER.parent)) if path not in sys.path]
        from tools.experiments.mcp_client_assurance import official_http_client

        async with official_http_client(endpoint, "modern") as client:
            return await observe(client, project, instance)

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="unity-console-probe-") as logs:
        env = {
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "UNITY_MCP_TELEMETRY_ENABLED": "0",
            "UNITY_MCP_LOG_DIR": logs,
            "PYTHONUTF8": "1",
        }
        for key in ("UNITY_MCP_STATUS_DIR", "UNITY_MCP_TOOL_PROFILE", "UNITY_MCP_TOOL_GROUPS"):
            if key in os.environ:
                env[key] = os.environ[key]
        server = StdioServerParameters(
            command=sys.executable, args=[str(SERVER), "--transport", "stdio"], env=env
        )
        async with (
            stdio_client(server) as (reader, writer),
            ClientSession(reader, writer, read_timeout_seconds=15) as session,
        ):
            await session.initialize()
            return await observe(session, project)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--endpoint", help="Local HTTP MCP endpoint, paired with --instance")
    parser.add_argument("--instance", help="Exact Name@hash registered with the HTTP endpoint")
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    if (args.endpoint is None) != (args.instance is None):
        parser.error("Provide --endpoint and --instance together")
    if not (project / "Assets").is_dir():
        parser.error("Expected a Unity project containing Assets")
    state = asyncio.run(asyncio.wait_for(run(project, args.endpoint, args.instance), timeout=55))
    print(json.dumps(state, ensure_ascii=True))


if __name__ == "__main__":
    main()
