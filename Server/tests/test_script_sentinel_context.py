"""The opt-in reload sentinel owns its command, not the finished script request."""

import asyncio
import gc
import importlib
from types import SimpleNamespace
import threading
from weakref import ref

from fastmcp import Client, FastMCP
import pytest
import pytest_asyncio

from core.config import config
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool
from models.response_limits import response_owner
from transport.response_limit_middleware import ResponseLimitMiddleware
import transport.legacy.unity_connection as legacy

scripts = importlib.import_module("services.tools.manage_script")


@pytest_asyncio.fixture
async def sentinel_wire(monkeypatch):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    conn = legacy.UnityConnection(port=6401, instance_id="EditorA@hash-A")
    original_command = conn.send_command
    contexts, requests, selections, sent, owners, responses = [], [], [], [], [], []
    selected = {"value": "EditorA@hash-A"}
    original_pool = legacy.get_unity_connection_pool
    original_send = legacy.async_send_command_with_retry

    def get_connection(selector):
        selections.append(selector)
        return conn

    def get_pool():
        if config.http_remote_hosted:
            return original_pool()
        return SimpleNamespace(get_connection=get_connection)

    async def instance(ctx):
        contexts.append(ref(ctx))
        requests.append(ref(ctx.request_context))
        return selected["value"]

    async def write(ctx, target, command, params, **kwargs):
        assert target == selected["value"]
        return {"success": True}

    def command(command_type, params, **kwargs):
        sent.append((command_type, params, kwargs))
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), "test failed to release inert Unity IO"
        return {"success": True}

    async def observe_send(*args, **kwargs):
        owner = response_owner.get()
        owners.append(owner.released if owner else None)
        del owner
        result = await original_send(*args, **kwargs)
        responses.append(result)
        return result

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(legacy, "get_unity_connection_pool", get_pool)
    monkeypatch.setattr(conn, "send_command", command)
    monkeypatch.setattr(legacy, "async_send_command_with_retry", observe_send)
    monkeypatch.setattr(legacy, "read_status_file", lambda *args: None)
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(scripts, "send_mutation", write)
    monkeypatch.setattr("glob.glob", lambda *args, **kwargs: [])
    server = FastMCP("sentinel-command-ownership")
    server.add_middleware(ResponseLimitMiddleware())
    wrapped = log_execution("apply_text_edits", "Tool")(scripts.apply_text_edits)
    server.tool(name="apply_text_edits")(telemetry_tool("apply_text_edits")(wrapped))
    wire = SimpleNamespace(
        server=server,
        connection=conn,
        original_command=original_command,
        entered=entered,
        release=release,
        contexts=contexts,
        requests=requests,
        selections=selections,
        sent=sent,
        owners=owners,
        responses=responses,
        selected=selected,
    )
    try:
        yield wire
    finally:
        release.set()
        await asyncio.gather(*scripts._background_tasks, return_exceptions=True)
        await asyncio.sleep(0)
        # Canceled async callers still own running executor admission until IO ends.
        gate = conn._async_admission(loop)
        async with asyncio.timeout(3):
            while gate.locked():
                await asyncio.sleep(0.01)


async def edit(client, force=True):
    return await client.call_tool(
        "apply_text_edits",
        {
            "uri": "Assets/Large.cs",
            "edits": [
                {
                    "startLine": 1,
                    "startCol": 1,
                    "endLine": 1,
                    "endCol": 1,
                    "newText": "x" * (1024 * 1024),
                }
            ],
            "options": {"force_sentinel_reload": force},
        },
        meta={"probe_payload": "y" * (1024 * 1024)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [None, "EditorA@hash-A"])
async def test_finished_request_released_during_real_sentinel_admission(sentinel_wire, selector):
    wire = sentinel_wire
    wire.selected["value"] = selector
    async with Client(wire.server, mode="legacy") as client:
        assert not (await edit(client)).is_error
        await asyncio.wait_for(wire.entered.wait(), 2)
        await client.list_tools()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        assert wire.contexts[0]() is None
        assert wire.requests[0]() is None
        assert wire.owners == [None]
        assert wire.selections == [selector]
        assert len(scripts._background_tasks) == 1
        assert wire.connection._async_admission(asyncio.get_running_loop()).locked()
        assert wire.sent[0][0:2] == ("execute_menu_item", {"menuPath": "MCP/Flip Reload Sentinel"})
        wire.release.set()
        await asyncio.gather(*scripts._background_tasks)
        await asyncio.sleep(0)
        assert wire.responses == [{"success": True}]
        assert not scripts._background_tasks


@pytest.mark.asyncio
async def test_no_sentinel_option_leaves_no_background_request_owner(sentinel_wire):
    wire = sentinel_wire
    async with Client(wire.server, mode="legacy") as client:
        assert not (await edit(client, force=False)).is_error
        await client.list_tools()
        await asyncio.sleep(0)
        gc.collect()
        assert wire.contexts[0]() is None
        assert wire.requests[0]() is None
        assert not scripts._background_tasks
        assert wire.selections == []


@pytest.mark.asyncio
async def test_cancelled_sentinel_keeps_executor_admission_until_command_finishes(sentinel_wire):
    wire = sentinel_wire
    async with Client(wire.server, mode="legacy") as client:
        assert not (await edit(client)).is_error
        await asyncio.wait_for(wire.entered.wait(), 2)
        task = next(iter(scripts._background_tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        gate = wire.connection._async_admission(asyncio.get_running_loop())
        assert gate.locked()
        assert not scripts._background_tasks
        following = asyncio.create_task(legacy.async_send_command_with_retry("get_tool_states", {}))
        try:
            await asyncio.sleep(0.05)
            assert len(wire.sent) == 1
            wire.release.set()
            assert await following == {"success": True}
            assert len(wire.sent) == 2
            assert not gate.locked()
        finally:
            wire.release.set()
            await following


@pytest.mark.asyncio
async def test_sentinel_preserves_legacy_authentication_failure(sentinel_wire, monkeypatch):
    wire = sentinel_wire
    monkeypatch.setattr(
        wire.connection,
        "send_command",
        wire.original_command,
    )

    def rejected_connect(*args, **kwargs):
        wire.connection._authentication_failure = legacy.StdioAuthenticationError(
            "inert rejected proof"
        )
        return False

    monkeypatch.setattr(wire.connection, "connect", rejected_connect)
    async with Client(wire.server, mode="legacy") as client:
        assert not (await edit(client)).is_error
        await asyncio.gather(*scripts._background_tasks)
        assert wire.selections == ["EditorA@hash-A"]
        assert not wire.sent
        assert wire.responses[0].success is False
        assert "inert rejected proof" in wire.responses[0].error


@pytest.mark.asyncio
async def test_sentinel_preserves_remote_hosted_legacy_rejection(sentinel_wire, monkeypatch):
    wire = sentinel_wire
    monkeypatch.setattr(config, "http_remote_hosted", True)
    async with Client(wire.server, mode="legacy") as client:
        assert not (await edit(client)).is_error
        await asyncio.gather(*scripts._background_tasks)
        assert wire.selections == []
        assert not wire.sent
        assert wire.responses[0].success is False
        assert "disabled in remote-hosted mode" in wire.responses[0].error
