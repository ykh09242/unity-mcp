"""Fixed owned native progress SDK probes and their bounded process owner.

NUnit owns every native handler and release gate. The fixed owner starts only
one production HTTP child and four sequential SDK children through the existing
native_python/cleanup_process helpers. It never starts Unity or releases a gate.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import patch

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from fastmcp.server.middleware import Middleware

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from models.models import UnityInstanceInfo
from services.command_progress import claim_command_progress, foreground_progress
from transport.legacy import unity_connection as uc
from transport.unity_transport import send_with_unity_instance


def publish_once(root: Path, name: str, value: dict) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    assert len(payload.encode("utf-8")) <= 4096
    # Never overwrite earlier actual evidence, even after a failed attempt.
    with (root / name).open("x", encoding="utf-8", newline="\n") as output:
        output.write(payload + "\n")


async def probe(settings: dict, control_root: Path, mode: str) -> None:
    assert settings["protocol"] == "core06-owned-stdio-probe-v1"
    assert type(settings["port"]) is int and 0 < settings["port"] < 65536
    assert isinstance(settings["token"], str) and 32 <= len(settings["token"]) <= 256
    assert mode in {"legacy", "2026-07-28"}
    assert settings["mode"] == mode
    identity = "Owned@d0c0f006"
    first, second = settings["first"], settings["second"]
    assert all(isinstance(name, str) and 1 <= len(name) <= 64 for name in (first, second))
    assert first != second
    parameters = {"commands": [{"tool": first, "params": {}}, {"tool": second, "params": {}}]}
    pool = uc.UnityConnectionPool()
    pool._default_instance_id = identity
    pool._last_full_scan = time.monotonic()
    connection = uc.UnityConnection(
        host="127.0.0.1",
        port=settings["port"],
        instance_id=identity,
        auth_token_provider=lambda _: settings["token"],
    )
    pool._known_instances[identity] = UnityInstanceInfo(
        id=identity,
        name="Owned",
        hash="d0c0f006",
        path="/Owned/Assets",
        port=settings["port"],
        status="running",
    )
    pool._connections[identity] = connection
    notifications = []
    callback_errors = []
    handler_calls = []
    call = None
    returned = False
    server = FastMCP("core06-owned-native-stdio-progress")

    class ForegroundScope(Middleware):
        async def on_call_tool(self, context, call_next):
            async with foreground_progress(context.fastmcp_context):
                return await call_next(context)

    server.add_middleware(ForegroundScope())

    @server.tool(name="owned_native_batch")
    async def owned_native_batch(ctx: Context) -> dict:
        assert isinstance(ctx, Context)
        handler_calls.append(1)
        sink = claim_command_progress(total=2)
        assert sink is not None, "The actual SDK call must carry a progress token."
        return await send_with_unity_instance(
            uc.async_send_command_with_retry,
            identity,
            "batch_execute",
            parameters,
            _progress=sink,
            _deadline=asyncio.get_running_loop().time() + 12,
            retry_on_reload=False,
        )

    async def on_progress(completed, total, message):
        # MCP dispatchers may log/swallow callback exceptions. Record every
        # invocation and violation before any optional checkpoint publication;
        # the awaited main path, not this callback, owns the failing assertion.
        previous = notifications[-1][0] if notifications else None
        notifications.append((completed, total, message))
        errors = []
        if returned:
            errors.append("late_after_result")
        numeric = type(completed) in {int, float} and type(total) in {int, float}
        if not numeric or total != 2 or completed not in (1, 2):
            errors.append("invalid_counts")
        if previous is not None and (
            type(previous) not in {int, float} or not numeric or completed <= previous
        ):
            errors.append("non_monotonic_or_duplicate")
        pending = call is not None and not call.done()
        if len(notifications) == 1 and (completed != 1 or not pending):
            errors.append("first_update_not_pending_one_of_two")
        callback_errors.extend(errors)
        try:
            publish_once(
                control_root,
                f"callback-{len(notifications):04d}.json",
                {
                    "protocol": "core06-owned-stdio-probe-v1",
                    "mode": mode,
                    "invocation": len(notifications),
                    "completed_repr": repr(completed),
                    "total_repr": repr(total),
                    "after_result": returned,
                    "call_pending": pending,
                    "errors": errors,
                },
            )
            if len(notifications) == 1 and not callback_errors:
                publish_once(
                    control_root,
                    "progress-observed.json",
                    {
                        "protocol": "core06-owned-stdio-probe-v1",
                        "mode": mode,
                        "completed": 1,
                        "total": 2,
                        "call_pending": True,
                    },
                )
        except (OSError, AssertionError, TypeError, ValueError) as error:
            callback_errors.append("checkpoint_publication:" + repr(error))

    # Endpoint/pool/status discovery substitutions are the same bounded fixture
    # seams as the canonical stdio test. Authentication, routing, admission,
    # framing, ProgressSource guard, SDK Context/session/writer and native batch
    # execution stay real. This is not full hosted public-operation auth proof.
    with ExitStack() as changes:
        changes.enter_context(patch.object(config, "http_remote_hosted", False))
        changes.enter_context(patch.object(config, "transport_mode", "stdio"))
        changes.enter_context(patch.object(uc, "read_status_file", lambda *_: None))
        changes.enter_context(patch.object(uc, "_unity_connection_pool", pool))
        changes.enter_context(patch.object(uc, "get_unity_connection_pool", lambda: pool))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(connection.connect), 3)
            session = connection.authenticated_session
            assert session is not None and "command_progress_v1" in session.capabilities
            assert connection.claim_tool_resync()
            async with Client(
                FastMCPTransport(server), mode=mode, timeout=15, progress_handler=on_progress
            ) as client:
                try:
                    call = asyncio.create_task(
                        client.call_tool_mcp(
                            "owned_native_batch", {}, meta={"progressToken": "owned-core06-" + mode}
                        )
                    )
                    response = await asyncio.wait_for(asyncio.shield(call), 15)
                    returned = True
                    assert not response.is_error
                    value = response.structured_content
                    assert isinstance(value, dict) and value["success"] is True
                    assert response.content and json.loads(response.content[0].text) == value
                    assert value["data"]["attemptedCount"] == 2
                    assert value["data"]["notRunCount"] == 0
                    assert notifications and notifications[0][:2] == (1, 2)
                    assert handler_calls == [1]
                    assert not callback_errors, callback_errors
                    assert 1 <= len(notifications) <= 2
                    count = len(notifications)
                    await asyncio.sleep(0.25)
                    tools = await asyncio.wait_for(client.list_tools(), 2)
                    assert any(tool.name == "owned_native_batch" for tool in tools)
                    assert not callback_errors, callback_errors
                    assert len(notifications) == count
                    publish_once(
                        control_root,
                        "final-result.json",
                        {
                            "protocol": "core06-owned-stdio-probe-v1",
                            "mode": mode,
                            "sdk_is_error": response.is_error,
                            "attempted": 2,
                            "not_run": 0,
                            "handler_calls": 1,
                            "notifications": len(notifications),
                            "same_session_healthy": True,
                            "no_late_progress": True,
                        },
                    )
                finally:
                    if call is not None:
                        if not call.done():
                            call.cancel()
                        await asyncio.gather(call, return_exceptions=True)
        finally:
            for owned in pool._connections.values():
                owned.disconnect()


async def websocket_probe(settings: dict, control_root: Path, mode: str) -> None:
    """Use the production HTTP/MCP/PluginHub and actual native WS registration."""
    assert settings["protocol"] == "core06-owned-ws-probe-v1"
    assert settings["mode"] == mode and mode in {"legacy", "2026-07-28"}
    from urllib.parse import urlsplit

    url = urlsplit(settings["http_url"])
    assert url.scheme == "http" and url.hostname == "127.0.0.1" and url.port
    assert url.path in {"", "/"} and not url.query and not url.fragment and not url.username
    assert isinstance(settings["token"], str) and 32 <= len(settings["token"]) <= 256
    assert settings["unity_instance"] == "d0c0f006"
    assert isinstance(settings["session_id"], str) and settings["session_id"]
    first, second = settings["first"], settings["second"]
    assert all(isinstance(name, str) and 1 <= len(name) <= 64 for name in (first, second))
    assert first != second
    notifications = []
    callback_errors = []
    call = None
    returned = False

    async def on_progress(completed, total, message):
        # MCP dispatchers may log/swallow callback exceptions. Record every
        # invocation and violation before any optional checkpoint publication;
        # the awaited main path, not this callback, owns the failing assertion.
        previous = notifications[-1][0] if notifications else None
        notifications.append((completed, total, message))
        errors = []
        if returned:
            errors.append("late_after_result")
        numeric = type(completed) in {int, float} and type(total) in {int, float}
        if not numeric or total != 2 or completed not in (1, 2):
            errors.append("invalid_counts")
        if previous is not None and (
            type(previous) not in {int, float} or not numeric or completed <= previous
        ):
            errors.append("non_monotonic_or_duplicate")
        pending = call is not None and not call.done()
        if len(notifications) == 1 and (completed != 1 or not pending):
            errors.append("first_update_not_pending_one_of_two")
        callback_errors.extend(errors)
        try:
            publish_once(
                control_root,
                f"callback-{len(notifications):04d}.json",
                {
                    "protocol": settings["protocol"],
                    "mode": mode,
                    "invocation": len(notifications),
                    "completed_repr": repr(completed),
                    "total_repr": repr(total),
                    "after_result": returned,
                    "call_pending": pending,
                    "errors": errors,
                },
            )
            if len(notifications) == 1 and not callback_errors:
                publish_once(
                    control_root,
                    "progress-observed.json",
                    {
                        "protocol": settings["protocol"],
                        "mode": mode,
                        "completed": 1,
                        "total": 2,
                        "call_pending": True,
                    },
                )
        except (OSError, AssertionError, TypeError, ValueError) as error:
            callback_errors.append("checkpoint_publication:" + repr(error))

    async with Client(
        StreamableHttpTransport(
            settings["http_url"] + "/mcp", headers={LOCAL_AUTH_HEADER: settings["token"]}
        ),
        mode=mode,
        timeout=15,
        progress_handler=on_progress,
    ) as client:
        try:
            call = asyncio.create_task(
                client.call_tool_mcp(
                    "batch_execute",
                    {
                        "commands": [{"tool": first, "params": {}}, {"tool": second, "params": {}}],
                        "unity_instance": settings["unity_instance"],
                    },
                    meta={"progressToken": "owned-core06-ws-" + mode},
                )
            )
            response = await asyncio.wait_for(asyncio.shield(call), 15)
            returned = True
            assert not response.is_error
            value = response.structured_content
            assert isinstance(value, dict) and value["success"] is True
            assert response.content and json.loads(response.content[0].text) == value
            assert value["data"]["attemptedCount"] == 2 and value["data"]["notRunCount"] == 0
            assert notifications and notifications[0][:2] == (1, 2)
            assert not callback_errors, callback_errors
            assert 1 <= len(notifications) <= 2
            count = len(notifications)
            await asyncio.sleep(0.25)
            tools = await asyncio.wait_for(client.list_tools(cache_mode="bypass"), 2)
            assert any(tool.name == "batch_execute" for tool in tools)
            assert not callback_errors, callback_errors
            assert len(notifications) == count
            publish_once(
                control_root,
                "final-result.json",
                {
                    "protocol": settings["protocol"],
                    "mode": mode,
                    "sdk_is_error": response.is_error,
                    "attempted": 2,
                    "not_run": 0,
                    "notifications": len(notifications),
                    "same_session_healthy": True,
                    "no_late_progress": True,
                },
            )
        finally:
            if call is not None:
                if not call.done():
                    call.cancel()
                await asyncio.gather(call, return_exceptions=True)


def read_owned_json(path: Path, maximum: int = 4096) -> dict:
    assert path.is_file() and not path.is_symlink()
    with path.open("rb") as source:
        payload = source.read(maximum + 1)
    assert 0 < len(payload) <= maximum
    value = json.loads(payload.decode("utf-8"))
    assert isinstance(value, dict)
    return value


async def await_owned_json(path: Path, deadline: float) -> dict:
    while True:
        assert time.monotonic() < deadline, (
            "Owned JSON publication did not finish within its bound."
        )
        try:
            return read_owned_json(path)
        except PermissionError as error:
            if getattr(error, "winerror", None) != 32:
                raise
            await asyncio.sleep(0.01)


async def owned_native_run(binding_path: Path) -> None:
    """One fixed owner; no retries, descendant discovery, or native gate control."""
    import anyio
    import httpx

    binding = read_owned_json(binding_path)
    assert binding["protocol"] == "core06-native-sdk-owner-v1"
    run_id = binding["runId"]
    assert isinstance(run_id, str) and 1 <= len(run_id) <= 128
    actual = Path(binding["actualRoot"]).resolve(strict=True)
    source = Path(binding["sourceRoot"]).resolve(strict=True)
    assert actual.is_dir() and source.is_dir() and not actual.is_symlink()
    assert binding_path.resolve().parent == actual
    native_start = Path(binding["nativeStartReceipt"])
    native_finished = Path(binding["nativeFinishedReceipt"])
    assert native_start == actual / "native-start.json"
    assert native_finished == actual / "native-finished.json"
    sdk = actual / "sdk"
    sdk.mkdir(exist_ok=False)
    (sdk / "processes").mkdir()
    for name in ("home", "config", "cache", "tmp"):
        (sdk / name).mkdir()
    helper_root = source / "tools"
    assert (helper_root / "bench_transport_process.py").is_file()
    sys.path.insert(0, str(helper_root))
    from bench_transport_process import cleanup_process, native_python

    def argv(arguments):
        value = native_python(arguments, paths=(source / "Server" / "src",))
        value.insert(1, "-B")
        return value

    async def drain(stream, path, is_closing):
        total = 0
        with path.open("xb") as output:
            while True:
                try:
                    chunk = await stream.receive(4096)
                except anyio.EndOfStream:
                    break
                except anyio.ClosedResourceError:
                    if not is_closing():
                        raise
                    break
                total += len(chunk)
                assert total <= 65536, "Owned child output exceeded its fixed bound."
                output.write(chunk)

    @asynccontextmanager
    async def child(role, command, env):
        process = await anyio.open_process(
            command,
            cwd=source / "Server",
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        drains = []
        closing = False
        try:
            receipt = {
                "protocol": "core06-owned-sdk-child-v1",
                "runId": run_id,
                "role": role,
                "pid": process.pid,
                "parentPid": os.getpid(),
                "executable": command[0],
                "argv": command,
                "commandLine": subprocess.list2cmdline(command),
            }
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            assert len(payload.encode()) <= 16384
            with (sdk / "processes" / (role + ".json")).open("x", encoding="utf-8") as output:
                output.write(payload + "\n")
            drains = [
                asyncio.create_task(
                    drain(process.stdout, sdk / (role + ".stdout.log"), lambda: closing)
                ),
                asyncio.create_task(
                    drain(process.stderr, sdk / (role + ".stderr.log"), lambda: closing)
                ),
            ]
            yield process, drains
        finally:
            closing = True
            try:
                await cleanup_process(process)
            finally:
                for task in drains:
                    if not task.done():
                        task.cancel()
                results = await asyncio.gather(*drains, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        raise result

    def check_child(process, drains):
        assert process.returncode is None, "Owned production HTTP child exited early."
        for task in drains:
            if task.done():
                task.result()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    token_path = sdk / "launch-token"
    env = {
        **os.environ,
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_HTTP_REMOTE_HOSTED": "false",
        "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE": str(token_path),
        "UNITY_MCP_LOG_DIR": str(sdk / "logs"),
        "HOME": str(sdk / "home"),
        "USERPROFILE": str(sdk / "home"),
        "APPDATA": str(sdk / "config"),
        "LOCALAPPDATA": str(sdk / "cache"),
        "XDG_CONFIG_HOME": str(sdk / "config"),
        "XDG_CACHE_HOME": str(sdk / "cache"),
        "TMP": str(sdk / "tmp"),
        "TEMP": str(sdk / "tmp"),
        "TMPDIR": str(sdk / "tmp"),
    }
    http_command = argv(
        [
            str(source / "Server" / "src" / "main.py"),
            "--transport",
            "http",
            "--http-host",
            "127.0.0.1",
            "--http-port",
            str(port),
            "--project-scoped-tools",
        ]
    )
    async with child("http", http_command, env) as (http, drains):
        ready_deadline = time.monotonic() + 20
        async with httpx.AsyncClient(timeout=0.5, trust_env=False) as client:
            while True:
                check_child(http, drains)
                assert time.monotonic() < ready_deadline, (
                    "Owned HTTP readiness exceeded 20 seconds."
                )
                try:
                    if (
                        await client.get(url + "/health")
                    ).status_code == 200 and token_path.is_file():
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
        token = token_path.read_text(encoding="utf-8").strip()
        assert 32 <= len(token) <= 256
        publish_once(
            sdk,
            "ws-server.json",
            {"protocol": "core06-owned-ws-server-v1", "http_url": url, "token": token},
        )
        publish_once(
            sdk,
            "owner-ready.json",
            {
                "protocol": binding["protocol"],
                "runId": run_id,
                "server_pid": http.pid,
                "sdk_root": str(sdk),
            },
        )
        start_deadline = time.monotonic() + 20
        while not native_start.is_file():
            check_child(http, drains)
            assert not native_finished.exists(), "Native owner aborted before actual start."
            assert time.monotonic() < start_deadline, (
                "No actual native start receipt within 20 seconds."
            )
            await asyncio.sleep(0.05)
        start = await await_owned_json(native_start, start_deadline)
        assert set(start) == {
            "protocol",
            "runId",
            "pid",
            "dotNetCreationUtcTicks",
            "nativeStartedUnix",
        }
        assert start["protocol"] == "core06-native-start-v1" and start["runId"] == run_id
        assert type(start["pid"]) is int and start["pid"] > 0
        assert type(start["dotNetCreationUtcTicks"]) is int and start["dotNetCreationUtcTicks"] > 0
        epoch = start["nativeStartedUnix"]
        assert type(epoch) in {int, float} and 0 <= time.time() - epoch < 20
        absolute_deadline = time.monotonic() + (epoch + 900 - time.time())
        cohort_deadline = None
        pending = {
            (surface, mode) for surface in ("stdio", "ws") for mode in ("legacy", "2026-07-28")
        }
        while True:
            check_child(http, drains)
            now = time.monotonic()
            assert now < absolute_deadline, (
                "The shared original native 900-second deadline expired."
            )
            if native_finished.is_file():
                finished = await await_owned_json(native_finished, min(absolute_deadline, now + 1))
                assert finished["runId"] == run_id
                assert not pending, "Native finished before all four SDK probes completed."
                break
            if pending and cohort_deadline is not None:
                assert now < cohort_deadline, (
                    "The SDK cohort exceeded 180 seconds from its first endpoint."
                )
            found = next(
                (
                    (surface, mode, sdk / surface / mode / "endpoint.json")
                    for surface, mode in sorted(pending)
                    if (sdk / surface / mode / "endpoint.json").is_file()
                ),
                None,
            )
            if found is None:
                await asyncio.sleep(0.05)
                continue
            if cohort_deadline is None:
                cohort_deadline = now + 180
            surface, mode, manifest = found
            await await_owned_json(
                manifest, min(absolute_deadline, cohort_deadline, time.monotonic() + 1)
            )
            role = surface + ("-legacy" if mode == "legacy" else "-modern")
            command = argv([str(Path(__file__).resolve()), str(manifest), mode])
            async with child(role, command, env) as (adapter, adapter_drains):
                timeout = min(
                    30, absolute_deadline - time.monotonic(), cohort_deadline - time.monotonic()
                )
                assert timeout > 0
                async with asyncio.timeout(timeout):
                    waiting = asyncio.create_task(adapter.wait())
                    try:
                        while not waiting.done():
                            check_child(http, drains)
                            for task in adapter_drains:
                                if task.done():
                                    task.result()
                            await asyncio.sleep(0.05)
                        assert await waiting == 0, "Real SDK probe failed; no retry is permitted."
                    finally:
                        if not waiting.done():
                            waiting.cancel()
                        await asyncio.gather(waiting, return_exceptions=True)
            pending.remove((surface, mode))
        publish_once(
            sdk,
            "owner-result.json",
            {
                "protocol": binding["protocol"],
                "runId": run_id,
                "completed": 4,
                "native_finished": True,
            },
        )


async def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--owned-native-run":
        await owned_native_run(Path(sys.argv[2]).resolve(strict=True))
        return
    assert len(sys.argv) == 3, "Expected owned endpoint JSON path and supported MCP mode."
    manifest = Path(sys.argv[1]).resolve(strict=True)
    assert manifest.stat().st_size <= 4096
    # The existing outer process/source guard must authorize this fresh case
    # directory. No user config/status discovery or credential files are read.
    settings = json.loads(manifest.read_text(encoding="utf-8"))
    async with asyncio.timeout(25):
        if settings["protocol"] == "core06-owned-stdio-probe-v1":
            await probe(settings, manifest.parent, sys.argv[2])
        else:
            await websocket_probe(settings, manifest.parent, sys.argv[2])


if __name__ == "__main__":
    asyncio.run(main())
