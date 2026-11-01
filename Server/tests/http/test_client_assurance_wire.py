"""Shared-server routing, cancellation and retention remain serial in one test group."""

import json
import os
from pathlib import Path
import sys

import anyio
from mcp import Client
import pytest

from tests.http.client_assurance_wire import (
    MODERN_VERSION,
    ControlledPlugin,
    read_scene,
    wire_client,
)
from tests.http.test_local_control_wire import local_server_process


@pytest.fixture(scope="module")
def assurance_server(tmp_path_factory):
    """One real production server; clients and plugin sockets remain independent."""
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("UNITY_MCP_TOOL_PROFILE", "full")
        with local_server_process(
            tmp_path_factory.mktemp("client-assurance"), module="tests.http.client_assurance_server"
        ) as server:
            yield server


async def state(client: Client) -> dict:
    result = await client.read_resource("assurance://retention", cache_mode="refresh")
    return json.loads(result.contents[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_official_clients_route_two_targets_across_reconnect_and_soak(assurance_server, mode):
    # Given: two real plugin sockets and independent official MCP connections.
    rounds = int(os.environ.get("UNITY_MCP_ASSURANCE_ROUNDS", "64"))
    assert 1 <= rounds <= 10000
    with (
        ControlledPlugin(assurance_server, 1).connected() as first,
        ControlledPlugin(assurance_server, 2).connected() as second,
    ):
        # When: calls overlap across targets, then new clients reconnect to the same server.
        for _ in range(2):
            async with wire_client(assurance_server, mode) as client:
                before = await state(client)
                assert (
                    Path(before["source"]).resolve().parents[2]
                    == Path(__file__).resolve().parents[3]
                )
                assert client.protocol_version == (
                    MODERN_VERSION if mode == "modern" else "2025-11-25"
                )

                async def exercise(current_client: Client, peer: ControlledPlugin) -> None:
                    for _ in range(rounds):
                        assert await read_scene(current_client, peer.instance) == peer.hash

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(exercise, client, first)
                    tasks.start_soon(exercise, client, second)
                after = await state(client)
                # Then: no sessionless per-request state or pending results accumulates.
                assert after["pending"] == after["retained_results"] == 0
                assert after["result_bytes"] == after["raw_results"] == 0
                assert after["entries"] == before["entries"]
                assert after["bytes"] == before["bytes"]
        assert first.commands == second.commands == 2 * rounds


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_wire_cancellation_releases_pending_command_and_keeps_connection_usable(
    assurance_server, mode
):
    # Given: an explicitly cancellation-capable peer with one in-flight native command.
    with ControlledPlugin(assurance_server, 1).connected() as peer:
        async with wire_client(assurance_server, mode) as client:
            peer.hold_next = True
            cancelled = anyio.Event()

            async def hold() -> None:
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await read_scene(client, peer.instance)
                cancelled.set()

            scopes = []
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(hold)
                assert await anyio.to_thread.run_sync(lambda: peer.entered.wait(10))
                # When: cancellation reaches the official client's live tool request.
                scopes[0].cancel()
                with anyio.fail_after(10):
                    await cancelled.wait()
                assert await anyio.to_thread.run_sync(lambda: peer.cancelled.wait(10))
            # Then: the same client and plugin can complete another real command.
            assert await read_scene(client, peer.instance) == peer.hash
            retained = await state(client)
            assert retained["pending"] == retained["retained_results"] == 0
            assert retained["result_bytes"] == retained["raw_results"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_existing_client_routes_reconnected_plugin_generation(assurance_server, mode):
    # Given: a real client remains connected while its target's plugin socket is replaced.
    peer = ControlledPlugin(assurance_server, 1)
    async with wire_client(assurance_server, mode) as client:
        with peer.connected():
            assert await read_scene(client, peer.instance) == peer.hash
        # When: the same project registers on a new server-side plugin generation.
        with peer.connected():
            assert await read_scene(client, peer.instance) == peer.hash
            retained = await state(client)
            # Then: the existing client dispatches to the replacement and releases its result.
            assert retained["pending"] == retained["retained_results"] == 0
        assert peer.commands == 2


@pytest.mark.asyncio
async def test_official_probe_verifies_both_targets_and_owned_socket_cleanup(
    assurance_server, monkeypatch, tmp_path
):
    # Given: independent target identities and the same read-only probe used for native runs.
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", assurance_server[2])
    with (
        ControlledPlugin(assurance_server, 1).connected() as first,
        ControlledPlugin(assurance_server, 2).connected() as second,
    ):
        # When: the standalone official client reconnects over both protocol eras.
        result = await anyio.run_process(
            [
                sys.executable,
                str(root / "tools" / "experiments" / "mcp_client_assurance.py"),
                "--endpoint",
                f"{assurance_server[0]}/mcp",
                "--instance",
                first.instance,
                "--instance",
                second.instance,
                "--expected-scene",
                first.hash,
                "--expected-scene",
                second.hash,
                "--output",
                str(tmp_path / "receipt.json"),
            ],
            env={**os.environ, "PYTHONPATH": str(root / "Server" / "src")},
        )
        receipt = json.loads(result.stdout)
        assert receipt["connections"] == 4
        assert receipt["scene_reads"] == 8
        assert receipt["expected_scene_assertions"] is True
        assert {item["protocol"] for item in receipt["receipts"]} == {MODERN_VERSION, "2025-11-25"}
        assert first.commands == second.commands == 4
    # Then: peer ownership is physically released, including heartbeat and result state.
    async with wire_client(assurance_server, "modern") as client:
        with anyio.fail_after(10):
            while True:
                retained = await state(client)
                if retained["connections"] == retained["admitted"] == retained["ping_tasks"] == 0:
                    break
        assert retained["pending"] == retained["retained_results"] == retained["raw_results"] == 0
