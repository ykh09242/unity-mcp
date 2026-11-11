# /// script
# requires-python = ">=3.11"
# ///
"""Exercise the installed Codex Rust MCP client without model turns or credentials.

Run with the locked Server development environment:
    python tools/check_codex_interop.py --codex /path/to/codex --output .work/codex-interop
"""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Final
from unittest.mock import patch

import anyio
from anyio.abc import Process
from pydantic import JsonValue

ROOT: Final = Path(__file__).resolve().parents[1]
PINNED_CODEX_VERSION: Final = "0.167.1"


class InteropFailure(AssertionError):
    """An observed actual-client result violated the regression contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InteropFailure(message)


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    require(isinstance(value, dict), "Expected a JSON object")
    match value:
        case dict():
            return value
        case _:
            raise InteropFailure("Expected a JSON object")


def result_or_raise(message: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Reject JSON-RPC failures instead of mistaking an error for a tool result."""
    if "error" in message:
        raise InteropFailure(f"App-server RPC failed: {json.dumps(message['error'])}")
    return mapping(message.get("result"))


def require_deadline_cancellation(observations: list[dict[str, JsonValue]]) -> None:
    """Require a server-observed cancellation before reconnect or teardown."""
    require(
        any(item.get("event") == "slow_cancelled" for item in observations),
        "Codex tool deadline did not cancel the server operation before reconnect",
    )


def text_payload(result: dict[str, JsonValue]) -> str:
    content = result.get("content")
    require(isinstance(content, list) and len(content) == 1, "Expected one text content block")
    block = mapping(content[0])
    require(block.get("type") == "text", "Expected text content")
    text = block.get("text")
    require(isinstance(text, str), "Expected text string")
    return text


def fixture_environment(directory: Path) -> dict[str, str]:
    return {
        "PYTHONPATH": os.pathsep.join((str(ROOT / "Server/src"), str(ROOT / "Server"))),
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_TOOL_PROFILE": "full",
        "UNITY_MCP_ASSURANCE_CATALOG_SIZE": "512",
        "UNITY_MCP_INTEROP_OBSERVATIONS": str(directory / "observations.jsonl"),
        "UNITY_MCP_STATUS_DIR": str(directory / "status"),
        "UNITY_MCP_LOG_DIR": str(directory / "logs"),
    }


def isolated_environment(directory: Path) -> dict[str, str]:
    """Pass only process necessities; never inherit authentication or user Codex config."""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "PATHEXT", "COMSPEC"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["CODEX_HOME"] = str(directory)
    environment["NO_COLOR"] = "1"
    return environment


def write_config(home: Path, server: dict[str, JsonValue], *, modern: bool = False) -> None:
    """Use an offline provider as an additional guard against accidental model traffic."""
    lines = [
        'model_provider = "interop_offline"',
        'model = "interop-no-model"',
        "check_for_update_on_startup = false",
        'cli_auth_credentials_store = "file"',
        'mcp_oauth_credentials_store = "file"',
        "[features]",
        "plugins = false",
        f"mcp_2026_07_28 = {str(modern).lower()}",
        "[analytics]",
        "enabled = false",
        "[feedback]",
        "enabled = false",
        "[model_providers.interop_offline]",
        'name = "Interop offline guard"',
        'base_url = "http://127.0.0.1:9/v1"',
        'wire_api = "responses"',
        "requires_openai_auth = false",
        "[mcp_servers.unity]",
        "startup_timeout_sec = 45",
        "tool_timeout_sec = 2",
    ]
    for key, value in server.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {json.dumps(value)}")
    for key, value in server.items():
        if isinstance(value, dict):
            lines.append(f"[mcp_servers.unity.{key}]")
            lines.extend(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items())
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class AppServer:
    """One owned app-server process; serialize RPCs and bound all response waits."""

    def __init__(self, process: Process):
        self.process = process
        self.buffer = b""
        self.request_id = 0
        self.notifications: list[str] = []

    async def send(self, message: dict[str, JsonValue]) -> None:
        require(self.process.stdin is not None, "App-server stdin unavailable")
        await self.process.stdin.send(json.dumps(message).encode() + b"\n")

    async def request(self, method: str, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.request_id += 1
        request_id = self.request_id
        await self.send({"id": request_id, "method": method, "params": params})
        require(self.process.stdout is not None, "App-server stdout unavailable")
        with anyio.fail_after(90):
            while True:
                while b"\n" not in self.buffer:
                    self.buffer += await self.process.stdout.receive(1024 * 1024)
                line, self.buffer = self.buffer.split(b"\n", 1)
                message = mapping(json.loads(line))
                if message.get("id") == request_id:
                    return result_or_raise(message)
                require("id" not in message, "Unexpected server request or mismatched RPC ID")
                self.notifications.append(str(message.get("method")))

    async def call(self, thread: str, name: str, arguments: dict[str, JsonValue]):
        return await self.request(
            "mcpServer/tool/call",
            {"threadId": thread, "server": "unity", "tool": name, "arguments": arguments},
        )


async def close_process(process: Process) -> None:
    """EOF first lets Codex release its MCP child; escalation targets only this child."""
    if process.stdin is not None:
        await process.stdin.aclose()
    with anyio.move_on_after(10) as graceful:
        await process.wait()
    if graceful.cancel_called:
        process.terminate()
        with anyio.move_on_after(5) as terminated:
            await process.wait()
        if terminated.cancel_called:
            process.kill()
            with anyio.fail_after(5):
                await process.wait()
    await process.aclose()


async def exercise(client: AppServer, directory: Path) -> dict[str, JsonValue]:
    initialized = await client.request(
        "initialize",
        {
            "clientInfo": {"name": "unity_mcp_interop", "version": "1.0"},
            "capabilities": {"experimentalApi": True},
        },
    )
    await client.send({"method": "initialized"})
    started = await client.request(
        "thread/start",
        {"cwd": str(directory), "ephemeral": True, "approvalPolicy": "never"},
    )
    thread = mapping(started["thread"])["id"]
    require(isinstance(thread, str), "Missing isolated test thread ID")
    inventory = await client.request("mcpServerStatus/list", {"threadId": thread})
    data = inventory.get("data")
    require(isinstance(data, list) and len(data) == 1, "Expected one configured MCP server")
    server = mapping(data[0])
    (directory / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    tools = mapping(server.get("tools"))
    names = {mapping(tool).get("name") for tool in tools.values()}
    require("manage_tools" in names and "read_response" in names, "Production tools missing")
    require(
        sum(str(name).startswith("assurance_catalog_") for name in names) == 512,
        "Codex did not discover the complete 512-tool pressure catalog",
    )
    echoed = await client.call(thread, "interop_echo", {"value": "Codex 한글"})
    require(not echoed.get("isError"), "Normal call returned a tool error")
    require(json.loads(text_payload(echoed)) == {"value": "Codex 한글"}, "Echo was corrupted")
    error = await client.call(thread, "interop_error", {})
    require(error.get("isError") is True, "MCP tool error was lost")
    require("interop deliberate failure" in text_payload(error), "Wrong tool error")
    invalid = await client.call(thread, "interop_echo", {"value": []})
    require(invalid.get("isError") is True, "Production input validation was bypassed")
    described = await client.call(
        thread, "manage_tools", {"action": "describe", "tool": "manage_scene"}
    )
    require(not described.get("isError"), "Production manage_tools call failed")
    require(
        "input_schema" in mapping(mapping(described["structuredContent"])["tool"]),
        "Production describe did not return the tool schema",
    )
    boundary_sizes = [
        (8 * 1024 * 1024 - 32_768 - 512, False),
        (8 * 1024 * 1024, False),
        (3 * 1024 * 1024, True),
    ]
    sizes: list[JsonValue] = []
    for size, unicode_payload in boundary_sizes:
        expected_text = ("한" if unicode_payload else "x") * size
        payload_bytes = len(expected_text.encode("utf-8"))
        response = await client.call(
            thread, "interop_payload", {"size": size, "unicode": unicode_payload}
        )
        require(not response.get("isError"), "Size delivery unexpectedly became a tool error")
        structured = response.get("structuredContent")
        handle = mapping(structured).get("delivery") if structured is not None else None
        require(
            (handle is None) == (payload_bytes < 8 * 1024 * 1024 - 32_768),
            "Response crossed the production inline/retained delivery boundary",
        )
        if handle is None:
            require(text_payload(response) == expected_text, "Inline response was truncated")
            sizes.append({"payload_bytes": payload_bytes, "delivery": "inline"})
            continue
        delivery = mapping(handle)
        require(delivery.get("state") == "retained", "Oversized result was not recoverable")
        received = bytearray()
        offset = 0
        for _ in range(257):
            page_result = await client.call(
                thread,
                "read_response",
                {
                    "response_id": delivery["response_id"],
                    "offset": offset,
                },
            )
            require(not page_result.get("isError"), "Retained page failed")
            page = mapping(mapping(page_result["structuredContent"])["data"])
            require(page.get("offset") == offset, "Page offset changed")
            decoded = base64.b64decode(page["data_base64"], validate=True)
            require(0 < len(decoded) <= 256 * 1024, "Invalid retained page size")
            received.extend(decoded)
            require(len(received) <= delivery["total_bytes"], "Retained pages exceeded total bytes")
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            require(isinstance(next_offset, int) and next_offset > offset, "Page made no progress")
            offset = next_offset
        else:
            raise InteropFailure("Retained response exceeded the bounded page count")
        require(len(received) == delivery["total_bytes"], "Retained byte count differs")
        require(hashlib.sha256(received).hexdigest() == delivery["sha256"], "Retained hash differs")
        restored = mapping(json.loads(received))
        require(text_payload(restored) == expected_text, "Retrieved response was corrupted")
        released = await client.call(
            thread,
            "read_response",
            {
                "response_id": delivery["response_id"],
                "release": True,
            },
        )
        require(not released.get("isError"), "Could not release retained response")
        sizes.append(
            {"payload_bytes": payload_bytes, "delivery": "retained", "sha256_verified": True}
        )
    try:
        await client.call(thread, "interop_slow", {})
    except InteropFailure as error:
        timeout_error = str(error)
        require(
            "timed out" in timeout_error.lower() or "deadline" in timeout_error.lower(),
            "Slow call failed for a reason other than the configured client deadline",
        )
    else:
        raise InteropFailure("Codex did not enforce its configured tool deadline")
    recovered = await client.call(thread, "interop_echo", {"value": "after-timeout"})
    require(
        json.loads(text_payload(recovered)) == {"value": "after-timeout"}, "Timeout recovery failed"
    )
    resource = await client.request(
        "mcpServer/resource/read",
        {
            "threadId": thread,
            "server": "unity",
            "uri": "assurance://retention",
        },
    )
    contents = resource.get("contents")
    require(isinstance(contents, list) and len(contents) == 1, "Production resource read failed")
    retention = mapping(json.loads(mapping(contents[0])["text"]))
    require(
        Path(retention["source"]).resolve() == ROOT / "Server/src/main.py",
        "Client connected to production source outside the selected checkout",
    )
    before_reload = [
        json.loads(line)
        for line in (directory / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    require_deadline_cancellation(before_reload)
    # Reload affects active thread runtimes asynchronously; start a fresh ephemeral
    # runtime after the reload to prove an independent connection can initialize.
    await client.request("config/mcpServer/reload", {})
    restarted = await client.request(
        "thread/start",
        {
            "cwd": str(directory),
            "ephemeral": True,
            "approvalPolicy": "never",
        },
    )
    reconnect_thread = mapping(restarted["thread"])["id"]
    require(isinstance(reconnect_thread, str), "Missing reconnect test thread ID")
    reconnected = await client.call(reconnect_thread, "interop_echo", {"value": "after-reload"})
    require(json.loads(text_payload(reconnected)) == {"value": "after-reload"}, "Reload failed")
    return {
        "user_agent": initialized.get("userAgent"),
        "catalog_tools": len(tools),
        "cases": [
            "discovery",
            "normal_call",
            "tool_error",
            "input_validation",
            "production_describe",
            "resource_read",
            "tool_deadline_recovery",
            "config_reload_reconnect",
        ],
        "size_boundaries": sizes,
        "deadline_error": timeout_error,
        "observations_before_reconnect": len(before_reload),
    }


async def run_case(
    codex: str, directory: Path, transport: str, modern: bool
) -> dict[str, JsonValue]:
    directory.mkdir()
    home = directory / "home"
    home.mkdir()
    fixture_env = fixture_environment(directory)
    if modern and transport == "stdio":
        fixture_env["CODEX_MCP_PROTOCOL_VERSION"] = "2026-07-28"
    with ExitStack() as owned:
        if transport == "http":
            sys.path[:0] = [str(ROOT / "Server"), str(ROOT / "Server/src")]
            fixture_module = import_module("tests.http.test_local_control_wire")
            require(
                Path(fixture_module.__file__).resolve()
                == ROOT / "Server/tests/http/test_local_control_wire.py",
                "HTTP fixture resolved outside the selected checkout",
            )
            local_server_process = fixture_module.local_server_process
            from core.local_auth import LOCAL_AUTH_HEADER

            owned.enter_context(patch.dict(os.environ, fixture_env))
            url, _, token = owned.enter_context(
                local_server_process(directory, module="tests.http.codex_interop_server")
            )
            server = {"url": url + "/mcp", "http_headers": {LOCAL_AUTH_HEADER: token}}
        else:
            server = {
                "command": sys.executable,
                "args": [
                    "-m",
                    "tests.http.codex_interop_server",
                    "--transport",
                    "stdio",
                    "--project-scoped-tools",
                ],
                "cwd": str(ROOT / "Server"),
                "env": fixture_env,
            }
        write_config(home, server, modern=modern)
        with (directory / "app-server.log").open("wb") as log:
            process = await anyio.open_process(
                [codex, "app-server"],
                cwd=directory,
                env=isolated_environment(home),
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with anyio.fail_after(180):
                    result = await exercise(AppServer(process), directory)
            finally:
                with anyio.CancelScope(shield=True):
                    await close_process(process)
    observations = [
        json.loads(line)
        for line in (directory / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    initializations = [item for item in observations if item.get("event") == "initialize"]
    expected_protocol = "2026-07-28" if modern else "2025-06-18"
    requests = [item for item in observations if item.get("event") == "request"]
    calls = [item for item in requests if item.get("method") == "tools/call"]
    require(
        bool(calls) and all(item.get("protocol") == expected_protocol for item in calls),
        f"Actual Codex {transport} requests did not use {expected_protocol}; observed "
        f"{sorted({str(item.get('protocol')) for item in calls})}",
    )
    if modern:
        after_reconnect = observations[result["observations_before_reconnect"] :]
        require(
            any(item.get("method") == "tools/list" for item in after_reconnect),
            "Fresh modern runtime did not rediscover the actual catalog",
        )
    else:
        require(len(initializations) >= 2, "Reconnect did not perform another MCP handshake")
    result["selected_mode"] = "modern" if modern else "legacy"
    result["observed_protocol"] = expected_protocol
    result["request_methods"] = sorted({item["method"] for item in requests})
    result["transport"] = transport
    result["initializations"] = initializations
    result["deadline_cancel_observed"] = any(
        item.get("event") == "slow_cancelled" for item in observations
    )
    result["cancellation"] = (
        "Direct tool-call cancellation is not exposed by the pinned app-server API; deadline cancellation and recovery are reported separately. Python wire tests cover explicit client cancellation."
    )
    return result


async def run(codex: str, output: Path) -> dict[str, JsonValue]:
    version = subprocess.run(
        [codex, "--version"], check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip()
    require(version == f"codex-cli {PINNED_CODEX_VERSION}", f"Expected pinned Codex, got {version}")
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    with tempfile.TemporaryDirectory(prefix="unity-codex-interop-") as temporary:
        for modern in (False, True):
            for transport in ("stdio", "http"):
                label = ("modern" if modern else "legacy") + "-" + transport
                cases.append(await run_case(codex, Path(temporary) / label, transport, modern))
    return {
        "status": "passed",
        "codex_version": version,
        "model_turns": 0,
        "user_credentials_used": False,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=shutil.which("codex"), help="Pinned actual Codex binary")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha", help="Exact receiving commit to verify before execution")
    args = parser.parse_args(argv)
    if not args.codex:
        parser.error("--codex is required when codex is absent from PATH")
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "report.json"
    path.unlink(missing_ok=True)
    receiving_sha = None
    try:
        receiving_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        require(bool(re.fullmatch(r"[0-9a-f]{40}", receiving_sha)), "Invalid checkout SHA")
        if args.expected_sha is not None:
            require(
                bool(re.fullmatch(r"[0-9a-f]{40}", args.expected_sha))
                and receiving_sha == args.expected_sha,
                "Checkout does not match the immutable expected SHA",
            )
        report = anyio.run(run, args.codex, args.output.resolve())
        report["receiving_sha"] = receiving_sha
    except (
        InteropFailure,
        OSError,
        TimeoutError,
        anyio.EndOfStream,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        path.write_text(
            json.dumps(
                {"status": "failed", "receiving_sha": receiving_sha, "error": str(error)[:2000]}
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Codex interoperability passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
