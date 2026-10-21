"""Actual HTTP/SDK observation Tasks preserve owner, generation and native state."""

import asyncio
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from services.tools import wait_for_operation as module
from services.test_wait_journal import WaitJournal, TaskNotFound
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_mcp_test_wait_binding import bound_http, bound_call, completed_wait
from tests.http.test_mcp_test_wait_lifecycle import operation
from transport.models import CommandResultMessage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,state",
    [
        ("build", "failed"),
        ("package", "unknown"),
        ("package", "succeeded"),
        ("scenario", "succeeded"),
    ],
)
async def test_real_sdk_task_matches_ordinary_existing_job_observation(
    command_hub, tmp_path, monkeypatch, kind, state
):
    job = "a" * 32

    async def answer(message):
        command_hub.sent.append(message)
        assert message["name"] == module._COMMANDS[kind]
        assert message["params"] == {"action": "status", "job_id": job}
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"],
                result={
                    "success": True,
                    "data": {"job_id": job, "result" if kind == "build" else "status": state},
                },
            ),
        )

    command_hub.websocket.send_json = answer
    async with bound_http(tmp_path, monkeypatch, operation_wait=True) as (client, runtime):
        params = {
            "name": "wait_for_operation",
            "arguments": {"kind": kind, "job_id": job, "wait_timeout": 1},
        }
        ordinary = await bound_call(client, "tools/call", params)
        task = await completed_wait(client, runtime, params)
        assert ordinary["result"] == task["result"]
        assert task["result"]["isError"] is False
        payload = task["result"]["structuredContent"]["data"]
        assert payload["job_status"] == state
        assert payload["terminal"] == (state != "unknown")
        captured = runtime.journal.get(task["taskId"], runtime.journal.local_owner).operation
        assert captured.tool_name == "wait_for_operation" and captured.job_kind == kind
        assert captured.generation == "generation"
    assert len(command_hub.sent) == 2


@pytest.mark.asyncio
async def test_finalizing_is_polled_until_owner_publishes_terminal(
    command_hub, tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "POLL_INTERVAL", 0)

    async def answer(message):
        command_hub.sent.append(message)
        data = {"job_id": "a" * 32, "status": "succeeded"}
        if len(command_hub.sent) == 1:
            data.update(
                status="running",
                phase="finalizing",
                pending_status="succeeded",
                runner_resources_released=False,
            )
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
        )

    command_hub.websocket.send_json = answer
    async with bound_http(tmp_path, monkeypatch, operation_wait=True) as (client, runtime):
        task = await completed_wait(
            client,
            runtime,
            {
                "name": "wait_for_operation",
                "arguments": {"kind": "scenario", "job_id": "a" * 32, "wait_timeout": 1},
            },
        )
        assert task["result"]["structuredContent"]["data"]["terminal"] is True
        assert len(command_hub.sent) == 2


@pytest.mark.asyncio
async def test_replaced_generation_after_journal_capture_never_dispatches(
    command_hub, tmp_path, monkeypatch
):
    async with bound_http(tmp_path, monkeypatch, operation_wait=True) as (client, runtime):
        previous = runtime.execute

        async def replace(captured):
            command_hub.websocket.state.plugin_generation = "replacement"
            return await previous(captured)

        runtime.execute = replace
        task = await completed_wait(
            client,
            runtime,
            {
                "name": "wait_for_operation",
                "arguments": {"kind": "build", "job_id": "job", "wait_timeout": 1},
            },
        )
        assert task["result"]["isError"] is True
        assert (
            task["result"]["structuredContent"]["data"]["reason"]
            == "observation_connection_unavailable_or_changed"
        )
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_generation_fence_at_http_admission_rejects_race(command_hub):
    target = module.ObservationTarget("Fixture@hash", "session", "old", None)
    result = await module.fetch_status(target, "build", "job")
    assert result["success"] is False
    assert result["data"]["reason"] == "observation_generation_changed"
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_cancel_task_only_cancels_status_observation(command_hub, tmp_path, monkeypatch):
    admitted = asyncio.Event()

    async def answer(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            admitted.set()

    command_hub.websocket.send_json = answer
    async with bound_http(tmp_path, monkeypatch, operation_wait=True) as (client, runtime):
        created = await bound_call(
            client,
            "tools/call",
            {
                "name": "wait_for_operation",
                "arguments": {"kind": "build", "job_id": "job", "wait_timeout": 60},
            },
            tasks=True,
        )
        with anyio.fail_after(3):
            await admitted.wait()
        await bound_call(
            client, "tasks/cancel", {"taskId": created["result"]["taskId"]}, tasks=True
        )
        with anyio.fail_after(3):
            while runtime._workers:
                await anyio.sleep(0)
        task = await bound_call(
            client, "tasks/get", {"taskId": created["result"]["taskId"]}, tasks=True
        )
        assert task["result"]["status"] == "cancelled"
    commands = [m for m in command_hub.sent if m["type"] == "execute"]
    assert len(commands) == 1 and commands[0]["params"]["action"] == "status"


@pytest.mark.parametrize("kind", ["build", "package", "scenario"])
def test_reopen_interrupts_wait_and_preserves_captured_kind_without_resuming(tmp_path, kind):
    captured = operation().model_copy(
        update={"tool_name": "wait_for_operation", "job_kind": kind, "generation": "original"}
    )
    with WaitJournal(tmp_path / "journal", clock=lambda: 1000.0) as journal:
        task = journal.create(captured).task_id
    with WaitJournal(tmp_path / "journal", clock=lambda: 1001.0) as journal:
        record = journal.get(task, "alice")
        assert record.status == "failed" and record.operation == captured
        assert "interrupted" in record.error["message"].lower()
        with pytest.raises(TaskNotFound):
            journal.get(task, "bob")


def test_stdio_generation_fence_does_not_connect_or_send(monkeypatch):
    from transport.legacy import unity_connection as connection
    from core.config import config

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(connection, "read_status_file", lambda _: None)
    conn = connection.UnityConnection(instance_id="Fixture@hash", port=1)
    conn.sock = Mock()
    conn.session_generation = "replacement"
    monkeypatch.setattr(conn, "_ensure_live_connection", lambda: None)
    connect = Mock(side_effect=AssertionError("must not reconnect"))
    monkeypatch.setattr(conn, "connect", connect)
    result = conn.send_command(
        "manage_packages",
        {"action": "status", "job_id": "job"},
        max_attempts=0,
        _expected_generation="original",
    )
    assert result["data"]["reason"] == "observation_generation_changed"
    conn.sock.sendall.assert_not_called()
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_http_generation_is_rechecked_inside_transport_admission(command_hub):
    from transport.plugin_hub import PluginHub

    result = await PluginHub.send_command(
        "session",
        "manage_build",
        {"action": "status", "job_id": "job"},
        _expected_generation="original",
    )
    assert result["data"]["reason"] == "observation_generation_changed"
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_non_task_sdk_clients_use_ordinary_observation(monkeypatch, mode):
    from fastmcp import Client, FastMCP
    from transport.response_limit_middleware import ResponseLimitMiddleware

    monkeypatch.setattr(module, "capture_target", AsyncMock(return_value=object()))
    fetch = AsyncMock(return_value={"success": True, "data": {"job_id": "job", "result": "failed"}})
    monkeypatch.setattr(module, "fetch_status", fetch)
    server = FastMCP("operation-observer")
    server.add_middleware(ResponseLimitMiddleware())
    server.tool(module.wait_for_operation)
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        assert len(tools) == 1
        result = await client.call_tool(
            "wait_for_operation", {"kind": "build", "job_id": "job", "wait_timeout": 0}
        )
        assert not result.is_error
        assert result.data["data"]["job_status"] == "failed"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_scenario_wire_parameters_match_existing_owner_contract(monkeypatch):
    from models.play_scenarios import PlayScenarioCommand
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(
        PluginHub,
        "_read_identity",
        AsyncMock(return_value=type("Identity", (), {"generation": "g", "user_id": None})()),
    )
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginHub, "send_command", send)
    await module.fetch_status(
        module.ObservationTarget("Fixture@hash", "session", "g", None), "scenario", "a" * 32
    )
    assert (
        send.await_args.args[2]
        == PlayScenarioCommand(action="status", job_id="a" * 32).wire_parameters()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_journal_capacity_fallback_preserves_captured_generation(
    command_hub, tmp_path, monkeypatch, replace
):
    from services.test_wait_journal import JournalCapacity

    async def answer(message):
        command_hub.sent.append(message)
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"],
                result={"success": True, "data": {"job_id": "job", "result": "succeeded"}},
            ),
        )

    command_hub.websocket.send_json = answer
    async with bound_http(tmp_path, monkeypatch, operation_wait=True) as (client, runtime):

        def capacity(*args, **kwargs):
            if replace:
                command_hub.websocket.state.plugin_generation = "replacement"
            raise JournalCapacity("full")

        monkeypatch.setattr(runtime.journal, "create", capacity)
        response = await bound_call(
            client,
            "tools/call",
            {
                "name": "wait_for_operation",
                "arguments": {"kind": "build", "job_id": "job", "wait_timeout": 1},
            },
            tasks=True,
        )
        result = response["result"]
        assert result["resultType"] == "complete"
        assert result["isError"] is replace
        if replace:
            assert (
                result["structuredContent"]["data"]["reason"]
                == "observation_connection_unavailable_or_changed"
            )
            assert command_hub.sent == []
        else:
            assert result["structuredContent"]["data"]["terminal"] is True
            assert len(command_hub.sent) == 1
    assert module.operation_wait_target.get() is None
