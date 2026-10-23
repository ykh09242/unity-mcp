"""Deterministic scheduling and construction cleanup for the catalog ASGI peer."""

from tests.timing import watchdog_timeout

import gc
import json
import warnings

import anyio
import pytest

from tests.http.test_mcp_catalog_subscription_limits import PluginStream, wait


@pytest.mark.asyncio
async def test_plugin_connect_waits_for_handled_registration_not_receive_readiness():
    consumed, release, finished = anyio.Event(), anyio.Event(), anyio.Event()
    errors = []

    async def app(_scope, receive, send):
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.send", "text": json.dumps({"type": "welcome"})})
        # Force the valid queue-before-next-receive schedule that the real Hub's
        # separate pre-registration receive Task can expose under Python3.11.
        await anyio.wait_all_tasks_blocked()
        frame = await receive()
        assert json.loads(frame["text"])["type"] == "register"
        consumed.set()
        await release.wait()
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps({"type": "registered", "session_id": "actual-registration"}),
            }
        )
        assert (await receive())["type"] == "websocket.disconnect"

    peer = PluginStream(app, "fixture-key", "a", "A")
    async with anyio.create_task_group() as group:

        async def connect():
            try:
                await peer.connect(group)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        group.start_soon(connect)
        await wait(consumed)
        await anyio.wait_all_tasks_blocked()
        try:
            # Receiving/queue readiness is deliberately separated from the
            # production handler's registered response and next receive boundary.
            assert not finished.is_set(), errors
            assert peer.session_id is None
        finally:
            release.set()
            await wait(finished)
            await peer.close()
        assert not errors
        assert peer.session_id == "actual-registration"
        assert peer.done.is_set()


def test_unstarted_plugin_peers_do_not_leak_streams_on_earlier_connect_failure():
    async def unused(_scope, _receive, _send):
        pytest.fail("An unstarted peer must not run")

    # The composed scenario constructs four peers before connecting the first.
    # If that first connection fails, the other three never enter run cleanup.
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", ResourceWarning)
        peers = [PluginStream(unused, "fixture-key", str(index), "Fixture") for index in range(3)]
        del peers
        gc.collect()
    assert not [warning for warning in observed if warning.category is ResourceWarning]


@pytest.mark.asyncio
async def test_plugin_exit_before_processing_boundary_fails_promptly_and_closes_owned_streams():
    async def app(_scope, receive, send):
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.send", "text": json.dumps({"type": "welcome"})})
        assert (await receive())["type"] == "websocket.receive"
        # Deliberately terminate without a registration acknowledgement or a next
        # receive. The helper must observe real task completion, not a timeout.

    peer = PluginStream(app, "fixture-key", "a", "A")
    async with anyio.create_task_group() as group:
        with anyio.fail_after(watchdog_timeout()):
            with pytest.raises(AssertionError, match="processing boundary"):
                await peer.connect(group)
        await wait(peer.done)
    assert peer.input.statistics().open_send_streams == 0
    assert peer.reader.statistics().open_receive_streams == 0


@pytest.mark.asyncio
async def test_plugin_cancelled_during_registration_closes_actual_stream_owners():
    consumed = anyio.Event()

    async def app(_scope, receive, send):
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.send", "text": json.dumps({"type": "welcome"})})
        assert (await receive())["type"] == "websocket.receive"
        consumed.set()
        await anyio.sleep_forever()

    peer = PluginStream(app, "fixture-key", "a", "A")
    async with anyio.create_task_group() as group:
        group.start_soon(peer.connect, group)
        await wait(consumed)
        group.cancel_scope.cancel()
    assert peer.done.is_set()
    assert peer.input.statistics().open_send_streams == 0
    assert peer.reader.statistics().open_receive_streams == 0
