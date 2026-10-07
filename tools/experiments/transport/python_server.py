#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["fastmcp==4.0.11", "mcp==2.3.0", "pydantic==2.13.5"]
# ///
# How to run: uv run python_server.py mcp FIXTURE_DIR
# Existing repository interpreter: Server/.venv/Scripts/python.exe python_server.py ...
"""Two public tool shapes backed exclusively by owned deterministic fixtures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final, Literal

import anyio
from fastmcp import FastMCP
from pydantic import JsonValue, TypeAdapter

JSON: Final = TypeAdapter(dict[str, JsonValue])


def build_server(fixtures: Path) -> FastMCP:
    """Construct the representative subset without importing product startup."""
    outputs = {
        name: JSON.validate_json((fixtures / f"{name}.json").read_bytes())
        for name in ("small", "state", "large", "job")
    }
    server = FastMCP("owned-python-probe")

    # The wide signatures intentionally preserve the public tool parameter shape.
    @server.tool
    async def read_console(
        action: Literal["get", "clear"] | None = None,
        types: list[Literal["error", "warning", "log", "all"]] | str | None = None,
        count: int | str | None = None,
        filter_text: str | None = None,
        page_size: int | str | None = None,
        cursor: int | str | None = None,
        format: Literal["plain", "detailed", "json"] | None = None,
        include_stacktrace: bool | str | None = None,
    ) -> dict[str, JsonValue]:
        """Return an owned fixture; optional public parameters are shape coverage only."""
        _ = action, types, count, page_size, cursor, format, include_stacktrace
        return outputs[(filter_text or "small:0").split(":", 1)[0]]

    @server.tool
    async def get_test_job(
        job_id: str,
        include_failed_tests: bool = False,
        include_details: bool = False,
        wait_timeout: int | None = None,
    ) -> dict[str, JsonValue]:
        """Return an owned terminal test job fixture without executing tests."""
        _ = job_id, include_failed_tests, include_details, wait_timeout
        return outputs["job"]

    _ = read_console, get_test_job  # Registration is performed by the decorators.
    return server


async def export_tools(server: FastMCP, fixtures: Path) -> None:
    """Share the exact advertised descriptors with the official C# SDK server."""
    tools = [
        tool.to_mcp_tool().model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in await server.list_tools()
    ]
    _ = (fixtures / "tools.json").write_text(json.dumps(tools), encoding="utf-8")


def main() -> None:
    print(f"owned_process_pid={os.getpid()}", file=sys.stderr, flush=True)
    mode, directory = sys.argv[1:]
    fixtures = Path(directory)
    server = build_server(fixtures)
    if mode == "export":
        anyio.run(export_tools, server, fixtures)
    else:
        server.run(transport="stdio", show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
