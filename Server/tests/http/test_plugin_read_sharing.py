"""In-flight HTTP reads keep connection identity, admission and delivery owners."""

import asyncio  # noqa: ANYIO_OK -- exercise the Hub's existing event-loop contract.
import time
from collections import Counter
from types import SimpleNamespace

import pytest
import pytest_asyncio
from starlette.websockets import WebSocketState

from core.config import config
from models.response_limits import ResponseOwner, response_owner, response_size
from services.tools.shared_tool_reads import SharedToolReads
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.charge_ledger import ChargeLedger
from transport.plugin_registry import PluginRegistry


class Peer:
    """Owned in-memory registered peer; commands use production Hub accounting."""

    def __init__(self, session="owned-session", generation="owned-generation", user="owned-user"):
        self.state = SimpleNamespace(
            plugin_registered=True,
            plugin_session_id=session,
            plugin_generation=generation,
            user_id=user,
            plugin_features=frozenset(),
        )
        self.application_state = self.client_state = WebSocketState.CONNECTED
        self.sent = []
        self.commands = asyncio.Queue()

    async def send_json(self, value):
        self.sent.append(value)
        if value["type"] == "execute":
            self.commands.put_nowait(value)

    async def close(self, **_kwargs):
        self.application_state = self.client_state = WebSocketState.DISCONNECTED

    async def next_command(self):
        return await asyncio.wait_for(self.commands.get(), 2)

    async def reply(self, command, result=None):
        if result is None:
            result = (
                {"status": "success", "result": {"message": "pong"}}
                if command["name"] == "ping"
                else {"success": True, "data": {"value": 7}}
            )
        hub = PluginHub({"type": "websocket"}, None, None)
        await hub._handle_command_result(
            self, CommandResultMessage(id=command["id"], result=result)
        )

    def counts(self):
        return Counter(value["name"] for value in self.sent if value["type"] == "execute")


async def yield_callers():
    """Yield scheduler turns without advancing polling/deadline time."""
    for _ in range(50):
        await asyncio.sleep(0)


@pytest_asyncio.fixture
async def peer(monkeypatch):
    # Given: an authenticated synthetic registry and peer, with no sockets/discovery.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    for name in (
        "_connections",
        "_pending",
        "_ping_tasks",
        "_last_pong",
        "_admitted",
        "_retained_results",
    ):
        monkeypatch.setattr(PluginHub, name, ChargeLedger() if name == "_retained_results" else {})
    registry = PluginRegistry()
    PluginHub.configure(registry)
    await registry.register("owned-session", "Owned", "ownedhash", "test", user_id="owned-user")
    wire = Peer()
    PluginHub._connections["owned-session"] = wire
    tasks_before = set(asyncio.all_tasks())
    yield wire
    for task in asyncio.all_tasks() - tasks_before:
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.gather(
        *(
            task
            for task in asyncio.all_tasks() - tasks_before
            if task is not asyncio.current_task()
        ),
        return_exceptions=True,
    )
    await PluginHub.shutdown()
    assert PluginHub._pending == {}
    assert PluginHub._retained_results == {}


def read(command="read_console", **kwargs):
    return PluginHub.send_command_for_instance(
        "ownedhash", command, {}, user_id="owned-user", **kwargs
    )


@pytest.mark.asyncio
async def test_concurrent_readiness_is_one_ping_and_next_request_is_fresh(peer):
    # When: six callers overlap the same main-thread readiness probe.
    callers = [asyncio.create_task(read()) for _ in range(6)]
    await peer.next_command()
    await yield_callers()
    probes = [value for value in peer.sent if value["name"] == "ping"]
    for probe in probes:
        await peer.reply(probe)
    await yield_callers()
    for command in peer.sent[len(probes) :]:
        await peer.reply(command)
    results = await asyncio.gather(*callers)
    # Then: command fanout is preserved, but readiness itself is shared.
    assert all(result["success"] for result in results)
    assert peer.counts() == {"ping": 1, "read_console": 6}
    fresh = asyncio.create_task(read())
    await yield_callers()
    probe = peer.sent[-1]
    assert probe["name"] == "ping"
    await peer.reply(probe)
    await yield_callers()
    await peer.reply(peer.sent[-1])
    assert (await fresh)["success"]
    assert peer.counts() == {"ping": 2, "read_console": 7}


@pytest.mark.asyncio
async def test_cancelled_first_readiness_waiter_preserves_other_waiter(peer):
    # Given: a first request whose probe is blocked, and a joined second request.
    first = asyncio.create_task(read())
    await peer.next_command()
    second = asyncio.create_task(read())
    await yield_callers()
    # When: the first consumer is cancelled before the shared result arrives.
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    for command in list(peer.sent):
        await peer.reply(command)
    await yield_callers()
    await peer.reply(peer.sent[-1])
    # Then: the remaining consumer completes using that same single probe.
    assert (await second)["success"]
    assert peer.counts() == {"ping": 1, "read_console": 1}


@pytest.mark.asyncio
async def test_last_readiness_waiter_cancels_orphan_without_retained_result(peer):
    # Given: one pending readiness probe.
    caller = asyncio.create_task(read())
    await peer.next_command()
    # When: its last waiter leaves.
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)
    # Then: its actual Hub pending/result reservations are returned.
    assert PluginHub._pending == {}
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_ordinary_state_fallback_is_shared_and_next_read_is_fresh(peer):
    # When: ordinary state reads miss the push cache at the same time.
    callers = [
        asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
        for _ in range(6)
    ]
    probe = await peer.next_command()
    await yield_callers()
    await peer.reply(probe)
    command = await peer.next_command()
    await yield_callers()
    assert command["name"] == "get_editor_state"
    await peer.reply(command)
    results = await asyncio.gather(*callers)
    # Then: both raw readiness and state fetch are single flights, without TTL.
    assert all(result["data"]["value"] == 7 for result in results)
    assert peer.counts() == {"ping": 1, "get_editor_state": 1}
    following = asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
    await peer.reply(await peer.next_command())
    await peer.reply(await peer.next_command())
    assert (await following)["success"]
    assert peer.counts() == {"ping": 2, "get_editor_state": 2}


@pytest.mark.asyncio
async def test_state_source_and_consumer_copies_keep_separate_delivery_budgets(peer):
    # Given: two consumers whose delivery lifetimes outlive the shared read.
    owners = [ResponseOwner(), ResponseOwner()]

    async def owned_read(owner):
        token = response_owner.set(owner)
        try:
            return await read("get_editor_state", editor_state_read_mode="ordinary")
        finally:
            response_owner.reset(token)

    callers = [asyncio.create_task(owned_read(owner)) for owner in owners]
    await peer.reply(await peer.next_command())
    command = await peer.next_command()
    await yield_callers()
    # When: a state result creates detached consumer copies.
    await peer.reply(command)
    await asyncio.gather(*callers)
    # Then: the source is gone, while each actual delivery owns a charged copy.
    assert len(PluginHub._retained_results) == 2
    owners[0].release()
    assert len(PluginHub._retained_results) == 1
    owners[1].release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["batch_execute", "unknown_custom_tool"])
async def test_mutation_admission_separates_pending_state_flights(peer, command):
    # Given: an ordinary state observation still executing in the editor.
    old = asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
    await peer.reply(await peer.next_command())
    old_command = await peer.next_command()
    # When: mutation admission occurs before a second ordinary observation.
    mutation = asyncio.create_task(read(command))
    mutation_command = await peer.next_command()
    fresh = asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
    await peer.reply(await peer.next_command())
    fresh_command = await peer.next_command()
    await peer.reply(mutation_command)
    await peer.reply(fresh_command, {"success": True, "data": {"value": 9}})
    await peer.reply(old_command)
    # Then: the new observation never joins or adopts the older result.
    assert (await fresh)["data"]["value"] == 9
    assert (await old)["data"]["value"] == 7
    assert (await mutation)["success"]
    assert peer.counts()["get_editor_state"] == 2


@pytest.mark.asyncio
async def test_authoritative_state_separates_ordinary_flights_before_private_rpc(peer):
    # Given: an older ordinary state source remains pending.
    old = asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
    await peer.reply(await peer.next_command())
    old_command = await peer.next_command()
    # When: strict preflight begins, then another ordinary resource is read.
    strict = asyncio.create_task(read("get_editor_state", editor_state_read_mode="authoritative"))
    await peer.reply(await peer.next_command())
    strict_command = await peer.next_command()
    fresh = asyncio.create_task(read("get_editor_state", editor_state_read_mode="ordinary"))
    await peer.reply(await peer.next_command())
    fresh_command = await peer.next_command()
    await peer.reply(fresh_command, {"success": True, "data": {"value": 11}})
    await peer.reply(strict_command, {"success": True, "data": {"value": 9}})
    await peer.reply(old_command)
    # Then: both the strict read and post-invalidation ordinary read are fresh RPCs.
    assert (await strict)["data"]["value"] == 9
    assert (await fresh)["data"]["value"] == 11
    assert (await old)["data"]["value"] == 7
    assert peer.counts()["get_editor_state"] == 3


@pytest.mark.asyncio
async def test_replaced_socket_between_capture_and_admission_never_receives_old_read(peer):
    # Given: an identity captured from a registered old socket.
    identity = await PluginHub._read_identity("owned-session")
    replacement = Peer(generation="replacement-generation")
    PluginHub._connections["owned-session"] = replacement
    # When: the old flight reaches actual admission after replacement.
    result = await PluginHub.send_command(
        "owned-session", "get_editor_state", {}, _expected_read=identity
    )
    # Then: no request is dispatched to the replacement under the old key.
    assert result["data"]["reason"] == "read_invalidated"
    assert replacement.sent == [] and peer.sent == []


@pytest.mark.asyncio
async def test_mutation_between_probe_and_tool_dispatch_refuses_old_readiness(peer):
    # Given: a reader is waiting for a ping started before mutation admission.
    caller = asyncio.create_task(read())
    probe = await peer.next_command()
    mutation = asyncio.create_task(read("unknown_custom_tool"))
    command = await peer.next_command()
    # When: that old probe replies after mutation admission.
    await peer.reply(probe)
    await peer.reply(command)
    # Then: the actual read is not dispatched using pre-mutation readiness.
    assert (await caller)["data"]["reason"] == "read_invalidated"
    assert (await mutation)["success"]
    assert peer.counts() == {"ping": 1, "unknown_custom_tool": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing_generation", "principal_mismatch"])
async def test_unverified_identity_keeps_readiness_private(peer, invalid):
    # Given: the available socket cannot establish a sharing identity.
    if invalid == "missing_generation":
        peer.state.plugin_generation = None
    else:
        peer.state.user_id = "different-user"
    # When: two readers arrive together.
    callers = [asyncio.create_task(read()) for _ in range(2)]
    probes = [await peer.next_command(), await peer.next_command()]
    for probe in probes:
        await peer.reply(probe)
    commands = [await peer.next_command(), await peer.next_command()]
    for command in commands:
        await peer.reply(command)
    # Then: lack of a verified identity never shares their state.
    assert all(result["success"] for result in await asyncio.gather(*callers))
    assert peer.counts() == {"ping": 2, "read_console": 2}


@pytest.mark.asyncio
async def test_readiness_capacity_refusal_has_no_retry_or_tool_dispatch(peer, monkeypatch):
    # Given: actual Hub command admission has no available slot.
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 0)
    # When: readiness is requested.
    result = await asyncio.wait_for(read(), 0.3)
    # Then: refusal is immediate, without a probe/retry or tool dispatch.
    assert result["data"]["reason"] == "command_capacity"
    assert peer.sent == []


@pytest.mark.asyncio
async def test_readiness_result_capacity_refusal_does_not_retry(peer, monkeypatch):
    # Given: the shared source cannot reserve even the ping result.
    monkeypatch.setattr(PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_SESSION", 0)
    caller = asyncio.create_task(read())
    probe = await peer.next_command()
    # When: an otherwise valid response exceeds the source budget.
    await peer.reply(probe)
    # Then: it returns the bounded capacity error rather than repeating ping.
    assert (await asyncio.wait_for(caller, 0.3))["data"]["reason"] == "result_capacity"
    assert peer.counts() == {"ping": 1}


@pytest.mark.asyncio
async def test_state_copy_capacity_is_reserved_before_fanout_without_another_rpc(peer, monkeypatch):
    # Given: room for the shared source and exactly one detached copy.
    owners = [ResponseOwner(), ResponseOwner()]

    async def owned_read(owner):
        token = response_owner.set(owner)
        try:
            return await read("get_editor_state", editor_state_read_mode="ordinary")
        finally:
            response_owner.reset(token)

    callers = [asyncio.create_task(owned_read(owner)) for owner in owners]
    await peer.reply(await peer.next_command())
    command = await peer.next_command()
    await yield_callers()
    result = {"success": True, "data": {"value": "x" * 1000}}
    monkeypatch.setattr(
        PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_SESSION", 2 * response_size(result)
    )
    # When: both consumers attempt to detach the same admitted source.
    await peer.reply(command, result)
    results = await asyncio.gather(*callers)
    # Then: only one copy is admitted; denial never repeats the editor command.
    assert sum(value["success"] for value in results) == 1
    assert (
        next(value for value in results if not value["success"])["data"]["reason"]
        == "result_capacity"
    )
    assert peer.counts() == {"ping": 1, "get_editor_state": 1}
    assert len(PluginHub._retained_results) == 1
    for owner in owners:
        owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_cancelled_state_first_owner_does_not_own_the_late_shared_result(peer):
    # Given: two actual delivery owners and one blocked ordinary state source.
    first_owner, second_owner = ResponseOwner(), ResponseOwner()

    async def owned_read(owner):
        token = response_owner.set(owner)
        try:
            return await read("get_editor_state", editor_state_read_mode="ordinary")
        finally:
            response_owner.reset(token)

    first = asyncio.create_task(owned_read(first_owner))
    await peer.reply(await peer.next_command())
    source = await peer.next_command()
    second = asyncio.create_task(owned_read(second_owner))
    await peer.reply(await peer.next_command())
    await yield_callers()
    # When: the first request ends before the shared Unity result arrives.
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    first_owner.release()
    await peer.reply(source)
    assert (await second)["success"]
    # Then: only the live delivery retains a charged detached copy.
    assert first_owner.entries == []
    assert len(PluginHub._retained_results) == 1
    second_owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_failed_flight_is_not_reused_by_following_read():
    # Given: a read flight failed while its lease is still active.
    reads = SharedToolReads()

    async def failure():
        raise RuntimeError("owned failed RPC")

    async def success():
        return {"success": True}

    async with reads.session("owned-identity") as shared:
        with pytest.raises(RuntimeError):
            await shared.fetch(failure)
        # When: the next independently requested observation starts.
        result = await shared.fetch(success)
        # Then: it executes its own factory rather than repeating the old error.
        assert result == {"success": True}


@pytest.mark.asyncio
async def test_actual_ordinary_resource_coalesces_raw_fallback_before_private_enrichment(
    peer, monkeypatch
):
    # Given: an HTTP editor without a pushed state, selected by the real resource.
    from unittest.mock import AsyncMock
    import services.resources.editor_state as state
    import transport.unity_transport as transport

    monkeypatch.setattr(
        state, "get_unity_instance_from_context", AsyncMock(return_value="ownedhash")
    )
    monkeypatch.setattr(
        transport, "_resolve_user_id_from_request", AsyncMock(return_value="owned-user")
    )
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="owned-user"))
    callers = [asyncio.create_task(state.get_editor_state(ctx)) for _ in range(6)]
    await peer.reply(await peer.next_command())
    source = await peer.next_command()
    await yield_callers()
    # When: one raw state RPC completes for all ordinary resource callers.
    await peer.reply(
        source,
        {
            "success": True,
            "data": {
                "schema_version": "unity-mcp/editor_state@2",
                "sequence": 7,
                "observed_at_unix_ms": int(time.time() * 1000),
                "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
                "settings": {"batch_execute_max_commands": 25},
            },
        },
    )
    results = await asyncio.gather(*callers)
    # Then: raw RPC counts shrink, while each caller receives private enriched data.
    assert all(result.success and result.data["sequence"] == 7 for result in results)
    assert peer.counts() == {"ping": 1, "get_editor_state": 1}
    results[0].data["settings"]["batch_execute_max_commands"] = 1
    assert results[1].data["settings"]["batch_execute_max_commands"] == 25


@pytest.mark.asyncio
async def test_replacement_between_ping_and_read_never_dispatches_to_new_generation(peer):
    # Given: a successful pong is delivered as the registered socket is replaced.
    caller = asyncio.create_task(read())
    probe = await peer.next_command()
    await peer.reply(probe)
    replacement = Peer(generation="replacement-generation")
    PluginHub._connections["owned-session"] = replacement
    # When: the waiter resumes and reaches final read admission.
    result = await caller
    # Then: old readiness cannot authorize dispatch on the replacement socket.
    assert result["data"]["reason"] == "read_invalidated"
    assert peer.counts() == {"ping": 1}
    assert replacement.sent == []


@pytest.mark.asyncio
async def test_replacement_between_socket_lookup_and_admission_is_refused(peer, monkeypatch):
    # Given: a pinned read pauses after socket lookup but before atomic admission.
    identity = await PluginHub._read_identity("owned-session")
    original = PluginHub._get_connection
    entered, release = asyncio.Event(), asyncio.Event()

    async def parked_lookup(session_id):
        socket = await original(session_id)
        entered.set()
        await release.wait()
        return socket

    monkeypatch.setattr(PluginHub, "_get_connection", parked_lookup)
    pending = asyncio.create_task(
        PluginHub.send_command("owned-session", "get_editor_state", {}, _expected_read=identity)
    )
    await entered.wait()
    # When: the same routing session now refers to a different actual socket.
    replacement = Peer(generation="replacement-generation")
    PluginHub._connections["owned-session"] = replacement
    release.set()
    result = await pending
    # Then: even the post-lookup race returns a bounded error without socket I/O.
    assert result["data"]["reason"] == "read_invalidated"
    assert replacement.sent == [] and peer.sent == []


@pytest.mark.asyncio
async def test_registered_principals_with_the_same_project_never_share(peer):
    # Given: two authenticated users have the same project hash.
    registry = PluginHub._registry
    await registry.register("second-session", "Owned", "ownedhash", "test", user_id="second-user")
    second_peer = Peer("second-session", "second-generation", "second-user")
    PluginHub._connections["second-session"] = second_peer
    # When: both read that project concurrently.
    first = asyncio.create_task(read())
    second = asyncio.create_task(
        PluginHub.send_command_for_instance("ownedhash", "read_console", {}, user_id="second-user")
    )
    await peer.reply(await peer.next_command())
    await second_peer.reply(await second_peer.next_command())
    await peer.reply(await peer.next_command(), {"success": True, "data": {"owner": "owned-user"}})
    await second_peer.reply(
        await second_peer.next_command(), {"success": True, "data": {"owner": "second-user"}}
    )
    # Then: each request observes only its own registered socket.
    assert (await first)["data"]["owner"] == "owned-user"
    assert (await second)["data"]["owner"] == "second-user"
    assert peer.counts() == second_peer.counts() == {"ping": 1, "read_console": 1}


@pytest.mark.asyncio
async def test_malformed_optional_ping_reason_does_not_turn_pong_into_a_retry_loop(peer):
    # Given: a valid pong contains an irrelevant malformed optional reason.
    caller = asyncio.create_task(read())
    probe = await peer.next_command()
    # When: its untrusted JSON reason is an array, not a capacity code.
    await peer.reply(
        probe, {"status": "success", "result": {"message": "pong"}, "data": {"reason": []}}
    )
    await peer.reply(await peer.next_command())
    # Then: readiness preserves the existing pong meaning without another probe.
    assert (await caller)["success"]
    assert peer.counts() == {"ping": 1, "read_console": 1}


@pytest.mark.asyncio
async def test_parameterized_state_commands_stay_private(peer):
    # Given: internal ordinary mode was used with different noncanonical params.
    callers = [
        asyncio.create_task(
            PluginHub.send_command_for_instance(
                "ownedhash",
                "get_editor_state",
                {"owned_projection": value},
                user_id="owned-user",
                editor_state_read_mode="ordinary",
            )
        )
        for value in (1, 2)
    ]
    probes = [await peer.next_command(), await peer.next_command()]
    for probe in probes:
        assert probe["name"] == "ping"
        await peer.reply(probe)
    commands = [await peer.next_command(), await peer.next_command()]
    # When: each parameterized command responds with its own requested value.
    for command in commands:
        await peer.reply(
            command, {"success": True, "data": {"value": command["params"]["owned_projection"]}}
        )
    # Then: raw state sharing is confined to the ordinary resource's empty params.
    assert sorted(value["data"]["value"] for value in await asyncio.gather(*callers)) == [1, 2]
    assert peer.counts() == {"ping": 2, "get_editor_state": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,params", [(None, {}), ("ordinary", {"owned_projection": "settings"})]
)
async def test_private_latest_state_invalidates_older_ordinary_flight(peer, mode, params):
    # Given: an ordinary state result has not arrived yet.
    old = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    old_command = await peer.next_command()
    # When: a private latest-state query completes before the next ordinary read.
    private = asyncio.create_task(
        PluginHub.send_command_for_instance(
            "ownedhash",
            "get_editor_state",
            params,
            user_id="owned-user",
            retry_on_reload=False,
            editor_state_read_mode=mode,
        )
    )
    await peer.reply(await peer.next_command(), {"success": True, "data": {"value": 9}})
    assert (await private)["data"]["value"] == 9
    following = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    await yield_callers()
    # Then: a subsequent ordinary request cannot adopt the older pending snapshot.
    assert peer.counts() == {"get_editor_state": 3}
    await peer.reply(peer.sent[-1], {"success": True, "data": {"value": 11}})
    await peer.reply(old_command)
    assert (await following)["data"]["value"] == 11
    assert (await old)["data"]["value"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["clear", "CLEAR", "unknown_action", 42])
async def test_console_mutation_admission_invalidates_older_state_flight(peer, action):
    # Given: a state source began before a console operation.
    old = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    old_command = await peer.next_command()
    # When: clear (or an ambiguous action) is admitted before another state read.
    console = asyncio.create_task(
        PluginHub.send_command_for_instance(
            "ownedhash",
            "read_console",
            {"action": action},
            user_id="owned-user",
            retry_on_reload=False,
        )
    )
    await peer.reply(await peer.next_command())
    assert (await console)["success"]
    following = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    await yield_callers()
    # Then: the post-admission resource cannot join the pre-operation state source.
    assert peer.counts() == {"get_editor_state": 2, "read_console": 1}
    await peer.reply(peer.sent[-1], {"success": True, "data": {"value": 9}})
    await peer.reply(old_command)
    assert (await following)["data"]["value"] == 9
    assert (await old)["data"]["value"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"action": None}, {"action": "get"}, {"action": "GET"}])
async def test_console_get_keeps_ordinary_state_flight_shareable(peer, params):
    # Given: the console's canonical/default get action is a read-only operation.
    old = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    old_command = await peer.next_command()
    console = asyncio.create_task(
        PluginHub.send_command_for_instance(
            "ownedhash", "read_console", params, user_id="owned-user", retry_on_reload=False
        )
    )
    await peer.reply(await peer.next_command())
    assert (await console)["success"]
    # When: another ordinary state request arrives after this read-only console RPC.
    following = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    await yield_callers()
    await peer.reply(old_command)
    # Then: it can safely share the same state RPC, preserving the optimization.
    assert (await following)["data"]["value"] == (await old)["data"]["value"] == 7
    assert peer.counts() == {"get_editor_state": 1, "read_console": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,params",
    [(None, {}), ("authoritative", {}), ("ordinary", {"owned_projection": "settings"})],
)
async def test_two_private_state_reads_succeed_after_both_identity_captures(
    peer, monkeypatch, mode, params
):
    # Given: the first private read parks after capturing its invalidation epoch.
    original = PluginHub._read_identity
    entered, release = asyncio.Event(), asyncio.Event()
    captures = 0

    async def parked_identity(session_id, **kwargs):
        nonlocal captures
        identity = await original(session_id, **kwargs)
        captures += 1
        if captures == 1:
            entered.set()
            await release.wait()
        return identity

    monkeypatch.setattr(PluginHub, "_read_identity", parked_identity)

    async def private_read():
        return await PluginHub.send_command_for_instance(
            "ownedhash",
            "get_editor_state",
            params,
            user_id="owned-user",
            editor_state_read_mode=mode,
        )

    first = asyncio.create_task(private_read())
    await entered.wait()
    # When: another fresh private read advances the epoch before first admission.
    second = asyncio.create_task(private_read())
    await peer.reply(await peer.next_command())
    second_command = await peer.next_command()
    release.set()
    await peer.reply(await peer.next_command())
    first_command = await peer.next_command()
    await peer.reply(second_command, {"success": True, "data": {"value": 9}})
    await peer.reply(first_command, {"success": True, "data": {"value": 11}})
    # Then: neither independent fresh read is refused by the other observation.
    assert (await first)["data"]["value"] == 11
    assert (await second)["data"]["value"] == 9
    assert peer.counts() == {"ping": 2, "get_editor_state": 2}


@pytest.mark.asyncio
async def test_direct_private_state_admission_separates_ordinary_flights(peer):
    # Given: a raw ordinary source precedes a direct Hub private state query.
    old = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    old_command = await peer.next_command()
    private = asyncio.create_task(PluginHub.send_command("owned-session", "get_editor_state", {}))
    await peer.reply(await peer.next_command(), {"success": True, "data": {"value": 9}})
    assert (await private)["data"]["value"] == 9
    # When: another ordinary request follows the private command's admission.
    following = asyncio.create_task(
        read("get_editor_state", editor_state_read_mode="ordinary", retry_on_reload=False)
    )
    await yield_callers()
    # Then: direct callers cannot bypass the ordinary late-join barrier.
    assert peer.counts() == {"get_editor_state": 3}
    await peer.reply(peer.sent[-1], {"success": True, "data": {"value": 11}})
    await peer.reply(old_command)
    assert (await following)["data"]["value"] == 11
    assert (await old)["data"]["value"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_sdk_delivery_retains_shared_state_copy_until_flush(peer, monkeypatch, cancel):
    # Given: actual installed SDK HTTP transport and a blocked final ASGI consumer.
    from unittest.mock import AsyncMock
    from main import UnityMCP
    from services.api_key_service import ApiKeyService, ValidationResult
    from transport.response_limit_middleware import ResponseLimitMiddleware, _http_response_owners
    from test_plugin_response_delivery import Wire

    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(
        ApiKeyService,
        "_instance",
        SimpleNamespace(
            validate=AsyncMock(return_value=ValidationResult(valid=True, user_id="owned-user")),
            aclose=AsyncMock(),
        ),
    )
    server = UnityMCP("owned-shared-delivery")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    async def shared_state() -> dict:
        return await read("get_editor_state", editor_state_read_mode="ordinary")

    app = server.http_app(transport="http", json_response=True)
    async with app.router.lifespan_context(app):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "owned-shared-delivery", "version": "1"},
            },
        }
        init = Wire(app, "POST", "/mcp", initialize)
        await asyncio.wait_for(init.run(), 2)
        start = next(value for value in init.sent if value["type"] == "http.response.start")
        session = dict(start["headers"])[b"mcp-session-id"]
        headers = [(b"mcp-session-id", session), (b"mcp-protocol-version", b"2025-03-26")]
        initialized = Wire(
            app, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers
        )
        await asyncio.wait_for(initialized.run(), 2)
        requests = [
            Wire(
                app,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": value,
                    "method": "tools/call",
                    "params": {"name": "shared_state", "arguments": {}},
                },
                headers,
                blocked_id=value,
            )
            for value in (9, 10)
        ]
        tasks = [asyncio.create_task(request.run()) for request in requests]
        try:
            await peer.reply(await peer.next_command())
            source = await peer.next_command()
            await yield_callers()
            # When: one state source completes but both actual sends are blocked.
            await peer.reply(source)
            await asyncio.gather(
                *(asyncio.wait_for(request.blocked.wait(), 2) for request in requests)
            )
            assert len(PluginHub._retained_results) == 2
            assert peer.counts() == {"ping": 1, "get_editor_state": 1}
            if cancel:
                tasks[0].cancel()
            else:
                requests[0].resume.set()
            await asyncio.gather(tasks[0], return_exceptions=cancel)
            # Then: finishing one HTTP delivery leaves the other copy charged.
            assert len(PluginHub._retained_results) == 1
            requests[1].resume.set()
            await tasks[1]
            assert PluginHub._retained_results == {}
            assert _http_response_owners == {}
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
