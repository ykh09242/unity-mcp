"""Project capability discovery composes current, scoped native observations."""

import json
import time
from types import SimpleNamespace

import anyio
from fastmcp import Client
import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_http_request_deadline import request_server
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from tests.remote_credentials import remote_credentials


def capability_server():
    from services.resources import _serialize_pydantic
    from services.resources.capabilities import get_capabilities

    server = request_server()
    server.resource("mcpforunity://capabilities")(_serialize_pydantic(get_capabilities))
    return server


def native_observations():
    return {
        "get_editor_state": {
            "schema_version": "unity-mcp/editor_state@2",
            "observed_at_unix_ms": int(time.time() * 1000),
            "sequence": 1,
            "unity": {
                "instance_id": "Fixture@hash",
                "platform": "WindowsEditor",
                "is_batch_mode": False,
            },
            "editor": {"play_mode": {"is_playing": True, "is_paused": False}},
            "compilation": {"is_compiling": False},
            "assets": {"is_updating": False},
            "tests": {"is_running": False},
        },
        "get_project_info": {
            "packages": {"inputsystem": True, "ugui": True},
            "activeInputHandler": "Both",
        },
        "manage_input": {
            "input_system": True,
            "ui_click": True,
            "playing": True,
            "paused": False,
            "max_frames": 600,
            "max_touch_contacts": 10,
        },
        "manage_recording": {"supported": True, "max_fps": 30, "max_duration_seconds": 60},
        "manage_play_scenario": {"scenarios": []},
    }


async def install_native_replies(hub, observations):
    async def reply(message):
        hub.sent.append(message)
        if message["type"] == "execute":
            await hub.endpoint._handle_command_result(
                hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": True, "data": observations[message["name"]]}
                    if message["name"] in observations
                    else {"success": False, "error": "Unknown command"},
                ),
            )

    hub.websocket.send_json = reply


async def read_capabilities(client):
    result = await client.read_resource("mcpforunity://capabilities")
    return json.loads(result[0].text)["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_capabilities_separates_backend_support_from_scene_readiness(command_hub, mode):
    # Given: live native backend and package evidence for one selected project.
    await install_native_replies(command_hub, native_observations())
    async with Client(capability_server(), mode=mode) as client:
        await client.call_tool("select_fixture")

        # When: the public capability resource composes existing read-only sources.
        data = await read_capabilities(client)

        # Then: known support does not pretend to prove target, codec or Player readiness.
        assert data["target"]["instance_id"] == "Fixture@hash"
        assert data["target"]["execution_environment"] == "editor"
        assert data["connection"]["status"] == "connected"
        assert data["connection"]["generation"]
        assert data["readiness"]["status"] == "ready"
        assert data["features"]["input"]["supported"] is True
        assert data["features"]["input"]["ready"] is True
        assert data["features"]["ui_click"]["supported"] is True
        assert data["features"]["ui_click"]["ready"] is None
        assert data["features"]["recording"]["ready"] is None
        assert data["features"]["play_scenario_player"]["ready"] is None
        assert (
            data["features"]["play_scenario_player"]["next_action"]
            == "validate_player_bundle_with_existing_preflight"
        )
        assert data["cache_ttl_seconds"] == 0
        assert len([message for message in command_hub.sent if message["type"] == "execute"]) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_old_peer_missing_status_sources_remain_unknown(command_hub, mode):
    # Given: an older peer advertises no negotiated flags and lacks newer status handlers.
    observations = native_observations()
    for name in ("manage_input", "manage_recording", "manage_play_scenario"):
        observations.pop(name)
    observations["get_project_info"] = {}
    observations["get_editor_state"].pop("compilation")
    command_hub.websocket.state.plugin_features = frozenset()
    await install_native_replies(command_hub, observations)
    async with Client(capability_server(), mode=mode) as client:
        # When: capability discovery reaches an older selected peer.
        data = await read_capabilities(client)

    # Then: absence is unobserved rather than unsupported or ready by default.
    assert data["connection"]["negotiated"] == []
    assert data["connection"]["peer_advertised"] is None
    assert data["packages"]["inputsystem"] is None
    assert data["features"]["input"]["supported"] is None
    assert data["features"]["recording"]["supported"] is None
    assert data["features"]["play_scenario_editor"]["supported"] is None
    assert data["readiness"]["status"] == "unknown"
    assert data["partial"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("native_input", [False, True])
async def test_package_presence_does_not_replace_native_backend_evidence(command_hub, native_input):
    # Given: package presence alone disagrees with actual backend availability.
    observations = native_observations()
    observations["get_project_info"]["packages"]["inputsystem"] = not native_input
    observations["manage_input"]["input_system"] = native_input
    await install_native_replies(command_hub, observations)
    async with Client(capability_server(), mode="legacy") as client:
        # When: discovery composes the two different evidence sources.
        data = await read_capabilities(client)

    # Then: native backend status decides support; package evidence remains independently visible.
    assert data["packages"]["inputsystem"] is (not native_input)
    assert data["features"]["input"]["supported"] is native_input
    assert data["features"]["input"]["ready"] is native_input


@pytest.mark.asyncio
async def test_headless_editor_reports_recording_unsupported_without_claiming_player_readiness(
    command_hub,
):
    # Given: the native capability handler rejects recording on a headless Editor.
    observations = native_observations()
    observations["get_editor_state"]["unity"]["is_batch_mode"] = True
    observations["manage_recording"]["supported"] = False
    await install_native_replies(command_hub, observations)
    async with Client(capability_server(), mode="legacy") as client:
        # When: a caller discovers this project's capabilities.
        data = await read_capabilities(client)

    # Then: Editor support and Player preflight evidence remain distinct.
    assert data["target"]["headless"] is True
    assert data["target"]["execution_environment"] == "editor"
    assert data["features"]["recording"]["supported"] is False
    assert data["features"]["recording"]["ready"] is False
    assert data["features"]["play_scenario_player"]["supported"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["unavailable", "capacity"])
async def test_connection_refusal_returns_unknown_features_without_probes(
    command_hub, monkeypatch, refusal
):
    # Given: the selected connection is unavailable or currently has no admission capacity.
    if refusal == "unavailable":
        await command_hub.registry.unregister("session")
    else:
        monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 0)
    async with Client(capability_server(), mode="legacy") as client:
        # When: callers read current capability evidence.
        data = await read_capabilities(client)

    # Then: discovery remains useful without consuming a command slot or guessing support.
    assert command_hub.sent == []
    assert data["partial"] is True
    assert data["features"]["input"]["supported"] is None
    assert data["readiness"]["status"] == "unknown"
    assert data["connection"]["next_action"] == (
        "connect_unity" if refusal == "unavailable" else "retry_later"
    )


@pytest.mark.asyncio
async def test_reconnect_discards_mixed_generation_observations(command_hub):
    # Given: the connection generation changes while the final native response arrives.
    observations = native_observations()
    await install_native_replies(command_hub, observations)
    reply = command_hub.websocket.send_json

    async def reconnect_after_reply(message):
        await reply(message)
        if len([item for item in command_hub.sent if item["type"] == "execute"]) == 5:
            command_hub.websocket.state.plugin_generation = "replacement-generation"

    command_hub.websocket.send_json = reconnect_after_reply
    async with Client(capability_server(), mode="legacy") as client:
        # When: discovery tries to publish those observations as one generation.
        data = await read_capabilities(client)

    # Then: the old evidence and scoped counters are discarded and the caller can retry.
    assert data["connection"]["status"] == "unknown"
    assert data["connection"]["generation"] is None
    assert data["connection"]["pending_commands"] is None
    assert data["connection"]["negotiated"] is None
    assert all(item["status"] == "unknown" for item in data["evidence"].values())
    assert data["features"]["input"]["supported"] is None


@pytest.mark.asyncio
async def test_slow_probe_spends_one_bounded_budget_and_keeps_verified_partial_results(
    command_hub, monkeypatch
):
    # Given: four status sources reply while one never completes within the read budget.
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.15)
    await install_native_replies(command_hub, native_observations())
    reply = command_hub.websocket.send_json

    async def slow_recording(message):
        if message["type"] == "execute" and message["name"] == "manage_recording":
            command_hub.sent.append(message)
            await anyio.sleep_forever()
        else:
            await reply(message)

    command_hub.websocket.send_json = slow_recording
    async with Client(capability_server(), mode="legacy") as client:
        # When: the aggregate resource reaches its single deadline.
        started = anyio.current_time()
        data = await read_capabilities(client)
        elapsed = anyio.current_time() - started

    # Then: verified completed sources survive and only the blocked source stays unknown.
    assert elapsed < 0.5
    assert data["connection"]["status"] == "connected"
    assert data["partial"] is True
    assert data["features"]["input"]["supported"] is True
    assert data["features"]["recording"]["supported"] is None
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_known_negotiated_flags_and_counters_are_tenant_scoped(command_hub, monkeypatch):
    async with remote_credentials(
        {
            "requester:user-a": "user-a",
            "plugin:user-a": "user-a",
            "requester:user-b": "user-b",
            "plugin:user-b": "user-b",
        }
    ) as auth:
        # Given: two authenticated tenants own the same project hash with different sessions.
        from core.config import config

        monkeypatch.setattr(config, "http_remote_hosted", True)
        await command_hub.registry.register("owned", "Owned", "hash", "6000", user_id="user-a")
        await command_hub.registry.register("foreign", "Foreign", "hash", "6000", user_id="user-b")
        owned = command_hub.websocket
        owned.state.plugin_session_id = "owned"
        owned.state.user_id = "user-a"
        await auth.attach_socket(owned, "plugin:user-a")
        owned.state.plugin_features = frozenset({"command_cancel_v1", "unknown-peer-secret"})
        PluginHub._connections["owned"] = owned
        PluginHub._pending["owned-command"] = {
            "user_id": "user-a",
            "session_id": "owned",
            "payload_bytes": 1,
        }
        PluginHub._pending["foreign-command"] = {
            "user_id": "user-b",
            "session_id": "foreign",
            "payload_bytes": 1,
        }
        try:
            # When: the existing registry and current socket establish one tenant's snapshot.
            result = await auth.call(
                "requester:user-a",
                PluginHub.get_capability_snapshot,
                "Owned@hash",
                user_id="user-a",
            )
            denied = await PluginHub.get_capability_snapshot("Owned@hash")

            # Then: foreign counters, unknown flags and unauthenticated metadata are never exposed.
            assert result.instance_id == "Owned@hash"
            assert result.negotiated == ["command_cancel_v1"]
            assert result.pending_commands == result.tenant_pending_commands == 1
            assert "unknown-peer-secret" not in result.model_dump_json()
            assert denied.status == "auth_required"
            assert denied.instance_id is None
        finally:
            PluginHub._pending.clear()


@pytest.mark.asyncio
async def test_legacy_stdio_without_authenticated_generation_stays_unknown(monkeypatch):
    # Given: an older stdio connection cannot prove an authenticated generation.
    from core.config import config
    import services.capabilities as capabilities

    monkeypatch.setattr(config, "transport_mode", "stdio")

    async def state(_name):
        return "Fixture@hash"

    async def no_generation(_instance):
        return None

    monkeypatch.setattr(capabilities, "get_authenticated_stdio_generation", no_generation)

    # When: discovery cannot bind observations to one connection generation.
    data = await capabilities.query_project_capabilities(SimpleNamespace(get_state=state))

    # Then: it reports the evidence gap without opening or mutating a connection.
    assert data.connection.status == "unknown"
    assert data.connection.generation is None
    assert data.features["input"].supported is None
    assert data.connection.next_action == "establish_authenticated_unity_connection"


@pytest.mark.asyncio
async def test_principal_change_discards_selected_project_identity(command_hub, monkeypatch):
    async with remote_credentials(
        {
            "requester:user-a": "user-a",
            "plugin:user-a": "user-a",
            "requester:user-b": "user-b",
            "plugin:user-b": "user-b",
        }
    ) as auth:
        # Given: request ownership changes before aggregate evidence can be verified.
        from core.config import config
        import services.capabilities as capabilities

        monkeypatch.setattr(config, "http_remote_hosted", True)
        await command_hub.registry.register(
            "session", "PrivateOwned", "hash", "6000", user_id="user-a"
        )
        command_hub.websocket.state.user_id = "user-a"
        await auth.attach_socket(command_hub.websocket, "plugin:user-a")
        owner = {"user_id": "user-a", "unity_instance": "Selected@hash"}
        await install_native_replies(command_hub, native_observations())
        reply = command_hub.websocket.send_json

        async def change_owner(message):
            await reply(message)
            owner["user_id"] = "user-b"

        async def state(name):
            return owner.get(name)

        command_hub.websocket.send_json = change_owner

        # When: the composition service observes that its authenticated owner no longer matches.
        data = await auth.call(
            "requester:user-a",
            capabilities.query_project_capabilities,
            SimpleNamespace(get_state=state),
        )

        assert command_hub.sent, "Principal-change fixture must reach a real native observation"

        # Then: even previously captured project identity is absent from the invalidated result.
        assert data.connection.status == "unknown"
        assert data.connection.instance_id is None
        assert data.target.instance_id is None
        assert data.target.project_hash is None
        assert "PrivateOwned" not in data.model_dump_json()


@pytest.mark.asyncio
async def test_stored_selection_reconnect_wait_is_inside_capability_budget(
    command_hub, monkeypatch
):
    # Given: a real persisted SDK selection loses its plugin before resource middleware runs.
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "0.25")
    async with Client(capability_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        await command_hub.registry.unregister("session")

        # When: discovery includes middleware selection rather than starting its clock afterward.
        started = anyio.current_time()
        data = await read_capabilities(client)
        elapsed = anyio.current_time() - started

    # Then: an unrelated reconnect policy cannot add its own wait before discovery's deadline.
    assert elapsed < 0.2
    assert data["connection"]["status"] in {"unknown", "unavailable"}
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_stored_selection_registry_lock_cannot_block_capability_resource(
    command_hub, monkeypatch
):
    # Given: stored selection routing is blocked before the resource handler is reached.
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.05)
    async with Client(capability_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        async with command_hub.registry._lock:
            # When: the public resource request encounters that locked registry.
            with anyio.fail_after(0.2):
                data = await read_capabilities(client)

    # Then: it returns an honest unknown snapshot without dispatching a native probe.
    assert data["connection"]["status"] == "unknown"
    assert data["target"]["instance_id"] is None
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_stalled_cancel_has_explicit_grace_and_keeps_verified_partial_evidence(
    command_hub, monkeypatch
):
    # Given: one probe and its cancellation write both stall; four other sources complete.
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0.2)
    await install_native_replies(command_hub, native_observations())
    reply = command_hub.websocket.send_json

    async def stalled(message):
        if message["type"] == "cancel" or (
            message["type"] == "execute" and message["name"] == "manage_recording"
        ):
            command_hub.sent.append(message)
            await anyio.sleep_forever()
        await reply(message)

    command_hub.websocket.send_json = stalled
    async with Client(capability_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")

        # When: probe execution expires and the transport drains its bounded cancellation.
        started = anyio.current_time()
        data = await read_capabilities(client)
        elapsed = anyio.current_time() - started

    # Then: explicit cleanup time does not erase completed evidence before revalidation.
    assert data["connection"]["status"] == "connected"
    assert data["features"]["input"]["supported"] is True
    assert data["features"]["recording"]["supported"] is None
    assert data["limits"]["query_cleanup_grace_seconds"] == 0.2
    assert data["limits"]["query_max_elapsed_seconds"] == pytest.approx(0.6)
    assert elapsed < data["limits"]["query_max_elapsed_seconds"] + 0.15
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_source_evidence_preserves_known_inspect_state_recovery_hint(command_hub):
    # Given: an existing source returns the conservative transport recovery hint.
    await install_native_replies(command_hub, native_observations())
    reply = command_hub.websocket.send_json

    async def unknown_outcome(message):
        if message["type"] == "execute" and message["name"] == "manage_input":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={
                        "success": False,
                        "hint": "inspect_state_before_retry",
                        "data": {"reason": "outcome_unknown"},
                    },
                ),
            )
        else:
            await reply(message)

    command_hub.websocket.send_json = unknown_outcome
    async with Client(capability_server(), mode="legacy") as client:
        # When: the capability resource exposes the unavailable source's evidence.
        data = await read_capabilities(client)

    # Then: discovery preserves the existing allowlisted recovery decision.
    assert data["evidence"]["manage_input"]["reason"] == "outcome_unknown"
    assert data["evidence"]["manage_input"]["next_action"] == "inspect_state_before_retry"


@pytest.mark.asyncio
async def test_final_revalidation_lock_clears_identity_within_disclosed_maximum(
    command_hub, monkeypatch
):
    # Given: one stalled cancellation also prevents final ownership revalidation.
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0.2)
    await install_native_replies(command_hub, native_observations())
    reply = command_hub.websocket.send_json

    async def block_final_identity(message):
        if message["type"] == "cancel":
            await command_hub.registry._lock.acquire()
            await anyio.sleep_forever()
        elif message["name"] == "manage_recording":
            command_hub.sent.append(message)
            await anyio.sleep_forever()
        else:
            await reply(message)

    command_hub.websocket.send_json = block_final_identity
    async with Client(capability_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        try:
            # When: cleanup finishes but verification cannot establish a current owner.
            started = anyio.current_time()
            data = await read_capabilities(client)
            elapsed = anyio.current_time() - started
        finally:
            if command_hub.registry._lock.locked():
                command_hub.registry._lock.release()

    # Then: the maximum includes revalidation and unverifiable evidence never escapes.
    assert elapsed < data["limits"]["query_max_elapsed_seconds"] + 0.15
    assert data["connection"]["status"] == "unknown"
    assert data["target"]["instance_id"] is None
    assert data["connection"]["generation"] is None
    assert all(item["status"] == "unknown" for item in data["evidence"].values())
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_stdio_worker_cannot_dispatch_after_capability_deadline(monkeypatch):
    # Given: a real admitted blocking worker waits for a connection's I/O lock.
    import asyncio
    from core.config import config
    import services.capabilities as capabilities
    import transport.legacy.unity_connection as legacy

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.05)
    connection = legacy.UnityConnection(port=1, instance_id="Fixture@hash")
    loop = asyncio.get_running_loop()
    entered, finished = asyncio.Event(), asyncio.Event()
    sent = []

    async def generation(_instance):
        return "stable-authenticated-generation"

    async def state(name):
        return "Fixture@hash" if name == "unity_instance" else None

    def send(name, _params, max_attempts=None, deadline=None):
        loop.call_soon_threadsafe(entered.set)
        try:
            with connection._command_lock(deadline):
                sent.append(name)
                return {"success": True, "data": {}}
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(capabilities, "get_authenticated_stdio_generation", generation)
    monkeypatch.setattr(legacy, "get_unity_connection", lambda _instance: connection)
    monkeypatch.setattr(connection, "send_command", send)
    connection._io_lock.acquire()
    query = asyncio.create_task(
        capabilities.query_project_capabilities(SimpleNamespace(get_state=state))
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)

        # When: the query expires before the independently held I/O lock is released.
        data = await asyncio.wait_for(query, 1)
    finally:
        connection._io_lock.release()
        await asyncio.wait_for(finished.wait(), 1)
        await query

    # Then: the blocking transport shares the deadline and cannot send after return.
    assert sent == []
    assert data.partial is True


@pytest.mark.asyncio
async def test_doctor_projection_and_native_metadata_preserve_complete_capability_wire(command_hub):
    # Existing consumers must see identical capabilities@1 before/after the new view.
    from tests.http.test_doctor import doctor_server, observations, read_doctor

    await install_native_replies(command_hub, native_observations())
    async with Client(doctor_server(), mode="legacy", cache=False) as client:
        await client.call_tool("select_fixture")
        original = await read_capabilities(client)
        await install_native_replies(command_hub, observations())
        with_metadata = await read_capabilities(client)
        await read_doctor(client)
        following = await read_capabilities(client)
    assert original == with_metadata == following
    commands = [message for message in command_hub.sent if message["type"] == "execute"]
    assert len(commands) == 20
    assert sorted(message["name"] for message in commands) == sorted(
        list(native_observations()) * 4
    )
