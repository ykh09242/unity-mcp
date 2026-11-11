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
from dataclasses import dataclass, field
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


def require_deadline_cancellation(
    observations: list[dict[str, JsonValue]], *, marker: str | None = None
) -> None:
    """Require a server-observed cancellation before reconnect or teardown."""
    require(
        any(
            item.get("event") == "slow_cancelled"
            and (marker is None or item.get("marker") == marker)
            for item in observations
        ),
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
        "thread_unload_delay_secs = 0",
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


@dataclass
class _PendingRpc:
    ready: anyio.Event = field(default_factory=anyio.Event)
    response: dict[str, JsonValue] | None = None


class AppServer:
    """One owned stdout reader routes overlapping RPCs and complete notifications."""

    def __init__(self, process: Process):
        self.process = process
        self.buffer = b""
        self.request_id = 0
        self.notifications: list[dict[str, JsonValue]] = []
        self.pending: dict[int, _PendingRpc] = {}
        self.reader_error: InteropFailure | None = None
        self.notification_changed = anyio.Event()
        self.write_lock = anyio.Lock()

    async def send(self, message: dict[str, JsonValue]) -> None:
        require(self.process.stdin is not None, "App-server stdin unavailable")
        async with self.write_lock:
            await self.process.stdin.send(json.dumps(message).encode() + b"\n")

    async def read_messages(self) -> None:
        """The caller owns and joins this task before closing the subprocess streams."""
        try:
            require(self.process.stdout is not None, "App-server stdout unavailable")
            while True:
                while b"\n" not in self.buffer:
                    self.buffer += await self.process.stdout.receive(1024 * 1024)
                line, self.buffer = self.buffer.split(b"\n", 1)
                message = mapping(json.loads(line))
                if "id" in message:
                    request_id = message["id"]
                    require(type(request_id) is int, "Unexpected app-server RPC ID")
                    pending = self.pending.get(request_id)
                    require(
                        pending is not None and pending.response is None,
                        "Unexpected server request or duplicate/unknown RPC ID",
                    )
                    pending.response = message
                    pending.ready.set()
                else:
                    self.notifications.append(message)
                    previous, self.notification_changed = self.notification_changed, anyio.Event()
                    previous.set()
        except (
            anyio.EndOfStream,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
            OSError,
            ValueError,
            InteropFailure,
        ) as error:
            self.reader_error = InteropFailure(
                f"App-server reader stopped: {type(error).__name__}: {error}"
            )
        finally:
            if self.reader_error is None:
                self.reader_error = InteropFailure("App-server reader closed")
            for pending in self.pending.values():
                pending.ready.set()
            self.notification_changed.set()

    async def request(self, method: str, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.request_id += 1
        request_id = self.request_id
        if self.reader_error is not None:
            raise self.reader_error
        pending = self.pending[request_id] = _PendingRpc()
        try:
            with anyio.fail_after(90):
                await self.send({"id": request_id, "method": method, "params": params})
                await pending.ready.wait()
            if pending.response is None:
                raise self.reader_error or InteropFailure("App-server response unavailable")
            return result_or_raise(pending.response)
        finally:
            self.pending.pop(request_id, None)

    async def wait_notification(self, method: str, thread: str) -> dict[str, JsonValue]:
        with anyio.fail_after(10):
            while True:
                changed = self.notification_changed
                for message in self.notifications:
                    params = message.get("params")
                    if (
                        message.get("method") == method
                        and isinstance(params, dict)
                        and params.get("threadId") == thread
                    ):
                        return message
                if self.reader_error is not None:
                    raise self.reader_error
                await changed.wait()

    async def call(
        self,
        thread: str,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        meta: dict[str, JsonValue] | None = None,
    ):
        return await self.request(
            "mcpServer/tool/call",
            {
                "threadId": thread,
                "server": "unity",
                "tool": name,
                "arguments": arguments,
                **({"_meta": meta} if meta is not None else {}),
            },
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


def observations(directory: Path) -> list[dict[str, JsonValue]]:
    result = []
    for path in directory.glob("observations-*.jsonl"):
        # Every process owns its file; an in-progress tail is observed next time.
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        result.extend(mapping(json.loads(line)) for line in lines if line.endswith("\n"))
    return sorted(result, key=lambda item: item["observed_ns"])


async def wait_observation(directory: Path, event: str, marker: str) -> dict[str, JsonValue]:
    with anyio.fail_after(10):
        while True:
            for item in observations(directory):
                if item.get("event") == event and item.get("marker") == marker:
                    return item
            await anyio.sleep(0.01)


async def start_thread(client: AppServer, directory: Path) -> str:
    started = await client.request(
        "thread/start", {"cwd": str(directory), "ephemeral": True, "approvalPolicy": "never"}
    )
    thread = mapping(started["thread"])["id"]
    require(isinstance(thread, str), "Missing isolated test thread ID")
    return thread


async def unload_thread(client: AppServer, thread: str) -> None:
    result = await client.request("thread/unsubscribe", {"threadId": thread})
    require(result.get("status") == "unsubscribed", "Loaded thread was not unsubscribed")
    await client.wait_notification("thread/closed", thread)


async def identity(client: AppServer, thread: str, marker: str) -> dict[str, JsonValue]:
    result = await client.call(
        thread, "interop_identity", {}, meta={"interopRequest": marker, "threadId": "forged"}
    )
    require(not result.get("isError"), "Identity observation failed")
    value = mapping(json.loads(text_payload(result)))
    require(
        type(value.get("pid")) is int and bool(value.get("startup")), "Missing process identity"
    )
    meta = mapping(value.get("meta"))
    require(meta.get("threadId") == thread, "Host thread metadata was not authoritative")
    require(meta.get("interopRequest") == marker, "Request metadata crossed thread boundaries")
    return value


def process_identity(value: dict[str, JsonValue]) -> tuple[JsonValue, JsonValue]:
    return value["pid"], value["startup"]


async def read_retained(client: AppServer, thread: str, handle: dict[str, JsonValue]) -> None:
    digest = hashlib.sha256()
    offset = 0
    for _ in range(257):
        result = await client.call(
            thread, "read_response", {"response_id": handle["response_id"], "offset": offset}
        )
        require(not result.get("isError"), "Retained page failed across owners")
        page = mapping(mapping(result.get("structuredContent"))["data"])
        require(page.get("offset") == offset, "Retained page offset changed")
        data = base64.b64decode(page["data_base64"], validate=True)
        require(0 < len(data) <= 256 * 1024, "Invalid retained page size")
        digest.update(data)
        offset += len(data)
        require(offset <= handle["total_bytes"], "Retained read exceeded original size")
        if page.get("next_offset") is None:
            break
        require(page["next_offset"] == offset, "Retained page skipped bytes")
    else:
        raise InteropFailure("Retained read exceeded bounded page count")
    require(
        offset == handle["total_bytes"] and digest.hexdigest() == handle["sha256"],
        "Retained bytes/hash changed across owners",
    )


async def release_retained(client: AppServer, thread: str, handle: dict[str, JsonValue]) -> None:
    result = await client.call(
        thread, "read_response", {"response_id": handle["response_id"], "release": True}
    )
    require(not result.get("isError"), "Retained release failed")
    require(
        mapping(mapping(result["structuredContent"])["data"]).get("state") == "released",
        "Release did not remove the handle",
    )


async def require_missing_handle(
    client: AppServer, thread: str, handle: dict[str, JsonValue]
) -> None:
    result = await client.call(thread, "read_response", {"response_id": handle["response_id"]})
    body = mapping(result.get("structuredContent"))
    require(body.get("error") == "response_not_found", "Unavailable handle remained readable")


async def exercise_threads(
    client: AppServer,
    directory: Path,
    first: str,
    *,
    modern: bool,
    transport: str,
    sharing: bool,
) -> dict[str, JsonValue]:
    first_identity = await identity(client, first, "owner-A")
    startups_before_second = [
        item for item in observations(directory) if item.get("event") == "process_started"
    ]
    second = await start_thread(client, directory)
    require(second != first, "Expected two actual ephemeral threads")
    inventory = await client.request("mcpServerStatus/list", {"threadId": second})
    require(
        isinstance(inventory.get("data"), list) and len(inventory["data"]) == 1,
        "Second thread did not discover the server",
    )
    second_identity = await identity(client, second, "owner-B")
    startups_with_two_owners = [
        item for item in observations(directory) if item.get("event") == "process_started"
    ]
    shared_process = transport == "http" or sharing
    require(
        (process_identity(first_identity) == process_identity(second_identity)) == shared_process,
        "Observed process reuse disagrees with the pool/private control",
    )
    expected_protocol = "2026-07-28" if modern else "2025-06-18"
    require(
        first_identity.get("protocol") == second_identity.get("protocol") == expected_protocol,
        "Owners did not use the expected protocol",
    )
    marker = "overlap-A"
    slow_done = anyio.Event()
    slow_error: list[str] = []

    async def slow():
        try:
            await client.call(first, "interop_slow", {}, meta={"interopRequest": marker})
        except InteropFailure as error:
            slow_error.append(str(error))
        finally:
            slow_done.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(slow)
        await wait_observation(directory, "slow_started", marker)
        require(not slow_done.is_set(), "Slow request settled before the overlap")
        echo = await client.call(second, "interop_echo", {"value": "while-A-pending"})
        require(
            json.loads(text_payload(echo)) == {"value": "while-A-pending"},
            "Concurrent owner failed",
        )
        require(not slow_done.is_set(), "Second owner did not finish before the deadline request")
        await slow_done.wait()
    require(
        len(slow_error) == 1
        and any(word in slow_error[0].lower() for word in ("timed out", "deadline")),
        "Slow call did not return the configured JSON-RPC deadline error",
    )
    await wait_observation(directory, "slow_cancelled", marker)
    await wait_observation(directory, "slow_finished", marker)
    require_deadline_cancellation(observations(directory), marker=marker)
    for thread, original in ((first, first_identity), (second, second_identity)):
        recovered = await identity(client, thread, "recovered-" + thread)
        require(
            process_identity(recovered) == process_identity(original),
            "Deadline recovery replaced the connection",
        )

    handles = []
    for index, thread in enumerate((first, second)):
        result = await client.call(thread, "interop_payload", {"size": 8 * 1024 * 1024 + index})
        handle = mapping(mapping(result["structuredContent"])["delivery"])
        require(handle.get("state") == "retained", "Missing lifecycle handle")
        handles.append(handle)
    require(
        handles[0]["response_id"] != handles[1]["response_id"],
        "Independent payloads reused one handle",
    )
    common_authority = modern and shared_process
    if common_authority:
        await read_retained(client, second, handles[0])
    else:
        await require_missing_handle(client, second, handles[0])
        await release_retained(client, first, handles[0])
    await unload_thread(client, first)
    surviving = await identity(client, second, "survivor-B")
    require(
        process_identity(surviving) == process_identity(second_identity),
        "Unloading A replaced B's process",
    )
    if common_authority:
        await read_retained(client, second, handles[0])
        await release_retained(client, second, handles[0])
        await require_missing_handle(client, second, handles[0])
    await read_retained(client, second, handles[1])
    await release_retained(client, second, handles[1])
    await require_missing_handle(client, second, handles[1])
    before_last_release = len(observations(directory))
    await unload_thread(client, second)
    third = await start_thread(client, directory)
    replacement = await identity(client, third, "after-last-owner")
    require(
        (process_identity(replacement) == process_identity(second_identity))
        == (transport == "http"),
        "Last-owner release did not produce the expected next process",
    )
    await unload_thread(client, third)
    return {
        "kind": "codex_stdio_pool"
        if sharing
        else "external_http_server"
        if transport == "http"
        else "private_stdio_control",
        "sharing_enabled": sharing,
        "owners": [first, second],
        "identities": [first_identity, second_identity],
        "startups_before_second_owner": startups_before_second,
        "startups_with_two_owners": startups_with_two_owners,
        "replacement": replacement,
        "overlap_before_deadline": True,
        "deadline_error": slow_error[0],
        "cancelled_before_unload": True,
        "survivor_process_unchanged": True,
        "common_domain_handle_continuity": common_authority,
        "independent_release": True,
        "observations_before_last_release": before_last_release,
        "selection_evidence": "Request metadata only; independent Editor routing is covered separately by server-contract tests.",
    }


async def exercise(
    client: AppServer,
    directory: Path,
    *,
    modern: bool,
    transport: str,
    sharing: bool,
) -> dict[str, JsonValue]:
    initialized = await client.request(
        "initialize",
        {
            "clientInfo": {"name": "unity_mcp_interop", "version": "1.0"},
            "capabilities": {"experimentalApi": True},
        },
    )
    await client.send({"method": "initialized"})
    enabled = await client.request(
        "experimentalFeature/enablement/set", {"enablement": {"mcp_connection_sharing": sharing}}
    )
    require(
        mapping(enabled.get("enablement")).get("mcp_connection_sharing") is sharing,
        "Client did not apply the connection sharing feature",
    )
    thread = await start_thread(client, directory)
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
    ownership = await exercise_threads(
        client, directory, thread, modern=modern, transport=transport, sharing=sharing
    )
    before_reload = observations(directory)
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
    await unload_thread(client, reconnect_thread)
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
        "ownership": ownership,
        "notifications": client.notifications,
    }


async def run_case(
    codex: str, directory: Path, transport: str, modern: bool, *, sharing: bool = False
) -> dict[str, JsonValue]:
    directory.mkdir()
    home = directory / "home"
    home.mkdir()
    fixture_env = fixture_environment(directory)
    if modern and transport == "stdio" and not sharing:
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
        write_config(home, server, modern=modern and not sharing)
        with (directory / "app-server.log").open("wb") as log:
            process = await anyio.open_process(
                [codex, "app-server"],
                cwd=directory,
                env=isolated_environment(home),
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            async with anyio.create_task_group() as tasks:
                client = AppServer(process)
                tasks.start_soon(client.read_messages)
                try:
                    with anyio.fail_after(180):
                        result = await exercise(
                            client, directory, modern=modern, transport=transport, sharing=sharing
                        )
                finally:
                    with anyio.CancelScope(shield=True):
                        await close_process(process)
                    tasks.cancel_scope.cancel()
    observed = observations(directory)
    initializations = [item for item in observed if item.get("event") == "initialize"]
    expected_protocol = "2026-07-28" if modern else "2025-06-18"
    requests = [item for item in observed if item.get("event") == "request"]
    calls = [item for item in requests if item.get("method") == "tools/call"]
    require(
        bool(calls) and all(item.get("protocol") == expected_protocol for item in calls),
        f"Actual Codex {transport} requests did not use {expected_protocol}; observed "
        f"{sorted({str(item.get('protocol')) for item in calls})}",
    )
    if modern:
        after_reconnect = observed[result["observations_before_reconnect"] :]
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
    advertisements = [item for item in observed if item.get("event") == "advertisement"]
    require(bool(advertisements), "No server capability advertisement observed")
    require(
        all("codex/connection-lease" not in item["extensions"] for item in advertisements),
        "Fixture advertised an unimplemented optional lease extension",
    )
    result["optional_connection_lease_advertised"] = False
    result["ordinary_modern_feature"] = modern and not sharing
    result["protocol_marker_present"] = "CODEX_MCP_PROTOCOL_VERSION" in fixture_env
    result["process_startups"] = [
        item for item in observed if item.get("event") == "process_started"
    ]
    result["deadline_cancel_observed"] = any(
        item.get("event") == "slow_cancelled" for item in observed
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
                print(f"Checking actual Codex case: {label}", flush=True)
                cases.append(await run_case(codex, Path(temporary) / label, transport, modern))
        print("Checking actual Codex case: pooled-stdio", flush=True)
        cases.append(
            await run_case(codex, Path(temporary) / "pooled-stdio", "stdio", True, sharing=True)
        )
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
        ExceptionGroup,
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
