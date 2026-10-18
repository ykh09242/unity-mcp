"""Scoped authority composes with real leases, transport admission and bounded reads."""

import asyncio
import importlib
import json
import time
from contextlib import asynccontextmanager
from contextvars import Context
from types import SimpleNamespace

import anyio
import pytest

from core.config import config
from services.api_key_service import ApiKeyService, ValidationResult
from services.credential_lease import CredentialLeaseError, bind_credential_lease
from services.operation_permissions import (
    OperationPermissionError,
    PermissionClaimError,
    authorize_operation,
    authorize_resource,
    parse_permissions,
)
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@asynccontextmanager
async def permission_service(monkeypatch, claims, *, strict=False):
    """Replace only validator output/clock; retain real auth, retirement and owner state."""
    now = [0.0]
    service = ApiKeyService("https://fixture.invalid/validate", 10, permissions_strict=strict)
    monkeypatch.setattr(service, "_now", lambda: now[0])

    async def validate(key):
        metadata = claims[key]
        return ValidationResult(valid=True, user_id="alice", metadata=metadata)

    monkeypatch.setattr(service, "_validate_external", validate)
    try:
        yield SimpleNamespace(service=service, now=now)
    finally:
        await service.aclose()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("metadata", [None, {}, {"other": "ignored"}])
def test_missing_claim_has_explicit_migration_semantics(strict, metadata):
    # Given a validator without the server-owned claim.
    grants = parse_permissions(metadata, strict=strict)
    # When its authority is used for an otherwise unknown legacy operation.
    if strict:
        with pytest.raises(OperationPermissionError):
            authorize_operation(grants, "legacy_custom", {})
    else:
        authorize_operation(grants, "legacy_custom", {})
    # Then the migration mode is retained, not confused with an explicit empty claim.
    assert grants.scoped is strict
    assert {permission.value for permission in grants.permissions} == (
        set() if strict else {"read", "test", "edit", "run"}
    )


@pytest.mark.parametrize(
    "claim",
    [None, "read", {}, ["READ"], ["execute"], ["read", "read"], [True], ["read"] * 5],
)
def test_present_malformed_claim_never_falls_back_to_legacy_authority(claim):
    # Given an explicitly supplied malformed or oversized claim.
    # When either migration mode parses it, then it fails closed.
    for strict in (False, True):
        with pytest.raises(PermissionClaimError):
            parse_permissions({"unity_mcp_permissions": claim}, strict=strict)


@pytest.mark.parametrize("grant", ["read", "test", "edit", "run"])
@pytest.mark.parametrize(
    "required,name,params",
    [
        ("read", "manage_asset", {"action": "get_labels"}),
        ("test", "run_tests", {}),
        ("edit", "manage_asset", {"action": "set_labels"}),
        ("run", "manage_editor", {"action": "play"}),
    ],
)
def test_four_grants_are_independent_including_cross_denials(grant, required, name, params):
    # Given exactly one explicit grant, not a privilege hierarchy.
    authority = parse_permissions({"unity_mcp_permissions": [grant]}, strict=False)
    # When admitting a representative operation, then only its own grant suffices.
    if grant == required:
        authorize_operation(authority, name, params)
    else:
        with pytest.raises(OperationPermissionError):
            authorize_operation(authority, name, params)


@pytest.mark.parametrize(
    "name,params",
    [
        ("create_script", {"name": "Example"}),
        ("manage_scene", {"action": "unknown"}),
        ("unknown_builtin", {}),
        ("execute_custom_tool", {"tool_name": "readonly_claimed_by_plugin"}),
        (
            "batch_execute",
            {
                "commands": [
                    {"tool": "manage_asset", "params": {"action": "get_labels"}},
                    {"tool": "readonly_claimed_by_plugin", "params": {}},
                ]
            },
        ),
        ("batch_execute", {"commands": [{"tool": "batch_execute", "params": {}}]}),
    ],
)
def test_read_grant_cannot_escape_through_alias_custom_unknown_or_batch(name, params):
    # Given read-only authority and a concrete bypass candidate.
    authority = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=False)
    # When classifying the full operation, then it is denied.
    with pytest.raises(OperationPermissionError):
        authorize_operation(authority, name, params)


def test_explicit_full_grants_keep_unknown_batches_closed_but_legacy_is_unchanged():
    # Given the same four effective grants, with distinct explicit/legacy provenance.
    scoped = parse_permissions(
        {"unity_mcp_permissions": ["read", "test", "edit", "run"]}, strict=False
    )
    legacy = parse_permissions(None, strict=False)
    operation = {"commands": [{"tool": "registered_custom", "params": {}}]}
    # When a custom batch is admitted, then only legacy behavior remains available.
    with pytest.raises(OperationPermissionError):
        authorize_operation(scoped, "batch_execute", operation)
    authorize_operation(legacy, "batch_execute", operation)


def test_explicit_empty_claim_never_inherits_compatibility_defaults():
    # Given an explicit empty claim in compatibility mode.
    authority = parse_permissions({"unity_mcp_permissions": []}, strict=False)
    # When even a read is requested, then it remains denied.
    with pytest.raises(OperationPermissionError):
        authorize_operation(authority, "manage_asset", {"action": "get_labels"})


@pytest.mark.parametrize(
    "uri",
    ["mcpforunity://unknown", "mcpforunity://tests/Unknown", "mcpforunity://editor/state/unknown"],
)
def test_scoped_resource_authority_rejects_unknown_routes(uri):
    # Given read authority, not permission for arbitrary resource handlers.
    authority = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=False)
    # When an unknown route is requested, then it fails before its handler.
    with pytest.raises(OperationPermissionError):
        authorize_resource(authority, uri)


@pytest.mark.parametrize("command", ["execute_menu_item", "execute_menu_item_observed"])
def test_native_execute_alias_cannot_claim_a_public_inspection_action(command):
    # Given read authority that may inspect via the Python tool's separate native inspector.
    authority = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=False)
    authorize_operation(authority, "execute_menu_item", {"action": "inspect"})
    # When a direct or batch caller addresses an executing native handler, then spoofed action is ignored.
    with pytest.raises(OperationPermissionError):
        authorize_operation(authority, command, {"action": "inspect"}, native=True)
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            authority,
            "batch_execute",
            {"commands": [{"tool": command, "params": {"action": "inspect"}}]},
        )


def test_indispensable_ready_wait_is_admitted_before_refresh_can_start():
    # Given edit/run authority that can dispatch a refresh without an observation.
    authority = parse_permissions({"unity_mcp_permissions": ["edit", "run"]}, strict=False)
    authorize_operation(authority, "refresh_unity", {"wait_for_ready": False})
    authorize_operation(authority, "refresh_unity", {}, native=True)
    # When the public wrapper promises a ready wait (the default), then its required
    # read authority is refused up front, rather than after an import/compile starts.
    with pytest.raises(OperationPermissionError):
        authorize_operation(authority, "refresh_unity", {})


@pytest.mark.parametrize(
    "name,params",
    [
        ("create_script", {}),
        ("delete_script", {}),
        ("apply_text_edits", {}),
        ("script_apply_edits", {}),
        ("manage_script", {"action": "create"}),
        ("manage_script", {"action": "delete"}),
    ],
)
def test_public_compiling_script_operations_require_read_edit_and_run(name, params):
    # Given either the compilation grant or indispensable ready/verification read is absent.
    for grants in (["read", "edit"], ["edit", "run"]):
        authority = parse_permissions({"unity_mcp_permissions": grants}, strict=False)
        with pytest.raises(OperationPermissionError):
            authorize_operation(authority, name, params)
    # The complete public operation is admitted only before all of its work may start.
    authority = parse_permissions({"unity_mcp_permissions": ["read", "edit", "run"]}, strict=False)
    authorize_operation(authority, name, params)


@pytest.mark.parametrize(
    "name,options",
    [
        ("apply_text_edits", {"preview": True}),
        ("apply_text_edits", {"debug_preview": "true"}),
        ("script_apply_edits", {"preview": " TRUE "}),
    ],
)
def test_script_wrapper_previews_keep_their_existing_read_edit_requirement(name, options):
    # Given a confirmed read-only preview route, not a truthy unparsed false string.
    authority = parse_permissions({"unity_mcp_permissions": ["read", "edit"]}, strict=False)
    authorize_operation(authority, name, {"options": options})
    with pytest.raises(OperationPermissionError):
        authorize_operation(authority, name, {"options": {"preview": "false"}})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["create", "delete", "edit", "apply_text_edits", "apply_prepared_script"]
)
async def test_native_compiling_script_mutations_require_run_before_socket_io(
    command_hub, monkeypatch, action
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": {"unity_mcp_permissions": ["edit"]},
        "plugin": {"unity_mcp_permissions": ["edit", "run"]},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # Given edit-only requester authority and a native action with an explicit compile path.
        # When the public wrapper is bypassed, then final native admission still sends nothing.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_script", {"action": action})
        assert result["error"] == "permission_denied"
        assert command_hub.sent == [] and PluginHub._pending == {}
        # Native reads/previews keep their read requirement and do not acquire run.
        read = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=False)
        authorize_operation(read, "manage_script", {"action": "preview_edit"}, native=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_claim,plugin_claim,allowed",
    [
        (["read"], ["read"], True),
        (None, ["read"], False),
        (["edit"], None, True),
        (["edit"], ["read"], False),
    ],
)
async def test_final_native_fence_intersects_requester_and_plugin(
    command_hub, monkeypatch, request_claim, plugin_claim, allowed
):
    # Given real same-user leases and a real Hub connection, one side may be legacy.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": None if request_claim is None else {"unity_mcp_permissions": request_claim},
        "plugin": None if plugin_claim is None else {"unity_mcp_permissions": plugin_claim},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        params = {"action": "get_labels" if request_claim == ["read"] else "set_labels"}
        # When dispatch is reached directly, without public tool middleware.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_asset", params)
        # Then either explicit restriction prevents all native socket work.
        assert result["success"] is allowed
        assert len(command_hub.sent) == (1 if allowed else 0)
        assert PluginHub._pending == {}
        if not allowed:
            assert result["error"] == "permission_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,params",
    [
        ("execute_menu_item_observed", {"action": "inspect", "menuPath": "Window/General/Console"}),
        ("generate_image", {"action": "generate", "prompt": "fixture"}),
        ("generate_audio", {"action": "list_models"}),
        ("blender_bridge", {"action": "check_updates"}),
        ("blender_bridge", {"action": "compare_screenshot", "game_object": "fixture"}),
        ("manage_scene", {"action": "validate", "autoRepair": "true"}),
    ],
)
async def test_read_credentials_cannot_start_hidden_execution_or_asset_writes(
    command_hub, monkeypatch, command, params
):
    # Given real read leases on both sides of the final native socket boundary.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {name: {"unity_mcp_permissions": ["read"]} for name in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # When an executing alias, native catalog refresh or mixed-effect operation is requested,
        # then no socket call can start the work even when public middleware is bypassed.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", command, params)
        assert result["error"] == "permission_denied"
        assert command_hub.sent == [] and PluginHub._pending == {}


@pytest.mark.asyncio
async def test_final_serialized_policy_refusal_sends_neither_execute_nor_cancel(
    command_hub, monkeypatch
):
    hub = importlib.import_module("transport.plugin_hub")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    original_dump = hub.ExecuteCommandMessage.model_dump
    serialized = []

    def changed_payload(message, *args, **kwargs):
        payload = original_dump(message, *args, **kwargs)
        payload["params"]["action"] = "clear"
        serialized.append(payload)
        return payload

    monkeypatch.setattr(hub.ExecuteCommandMessage, "model_dump", changed_payload)
    claims = {name: {"unity_mcp_permissions": ["read"]} for name in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # Given a permitted initial read whose canonical wire payload becomes a write.
        # When the final fence checks the actual serialized action, then pending
        # bookkeeping is released without an execute, cancel, or unknown-outcome claim.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "read_console", {"action": "get"})
        assert len(serialized) == 1 and serialized[0]["params"]["action"] == "clear"
        assert result["error"] == "permission_denied"
        assert command_hub.sent == [] and PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grants,allowed", [(["edit"], False), (["run"], False), (["edit", "run"], True)]
)
async def test_importing_execution_requires_both_grants_before_native_work(
    command_hub, monkeypatch, grants, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {name: {"unity_mcp_permissions": grants} for name in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # Given native generation that submits work and imports an asset.
        # When either independent grant is missing, then even native submission is
        # refused; the combined-grant control witnesses the actual send boundary.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command(
                "session", "generate_image", {"action": "generate", "prompt": "fixture"}
            )
        assert result["success"] is allowed
        assert len(command_hub.sent) == int(allowed) and PluginHub._pending == {}


@pytest.mark.asyncio
async def test_mixed_custom_batch_is_denied_before_settings_or_transport(monkeypatch):
    batch = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    calls = []

    async def forbidden(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("Forbidden settings/preflight/transport was reached")

    monkeypatch.setattr(batch, "_get_max_commands_from_editor_state", forbidden)
    monkeypatch.setattr(batch, "send_with_unity_instance", forbidden)
    async with permission_service(
        monkeypatch, {"key": {"unity_mcp_permissions": ["read"]}}
    ) as fixture:
        lease = (await fixture.service.validate("key")).lease
        # Given a valid first read and a forbidden later custom member.
        commands = [
            {"tool": "manage_asset", "params": {"action": "get_labels"}},
            {"tool": "registered_custom", "params": {}},
        ]
        # When calling the actual Python batch handler, then even the settings probe is unreachable.
        with bind_credential_lease(lease), pytest.raises(OperationPermissionError):
            await batch.batch_execute(SimpleNamespace(), commands)
        assert calls == []


@pytest.mark.asyncio
async def test_untrusted_custom_annotations_cannot_lower_permissions(monkeypatch):
    from services.custom_tool_service import CustomToolService

    monkeypatch.setattr(config, "http_remote_hosted", True)
    service = CustomToolService.__new__(CustomToolService)
    calls = []

    async def definition(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(annotations={"readOnlyHint": True})

    monkeypatch.setattr(service, "get_tool_definition", definition)
    async with permission_service(
        monkeypatch, {"key": {"unity_mcp_permissions": ["read"]}}
    ) as fixture:
        lease = (await fixture.service.validate("key")).lease
        # Given a plugin-defined descriptor that claims read-only behavior.
        # When a read credential calls it, then descriptor/default/polling work is not reached.
        with bind_credential_lease(lease), pytest.raises(OperationPermissionError):
            await service.execute_tool("hash", "custom", "Fixture@hash", user_id="alice")
        assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("grants,expected_refresh", [(["read"], 0), (["read", "edit", "run"], 1)])
async def test_real_preflight_skips_forbidden_refresh_with_positive_control(
    monkeypatch, grants, expected_refresh
):
    preflight = importlib.import_module("services.tools.preflight")
    editor_state = importlib.import_module("services.resources.editor_state")
    refresh = importlib.import_module("services.tools.refresh_unity")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    calls = []

    async def state(_ctx):
        calls.append("state")
        return {"success": True, "data": {"assets": {"external_changes_dirty": True}}}

    async def refreshing(*_args, **_kwargs):
        calls.append("refresh")
        return {"success": True}

    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", state)
    monkeypatch.setattr(refresh, "refresh_unity", refreshing)
    async with permission_service(
        monkeypatch, {"key": {"unity_mcp_permissions": grants}}
    ) as fixture:
        lease = (await fixture.service.validate("key")).lease
        # Given an actually dirty observation, with the test bypass explicitly disabled.
        # When the real preflight runs, then forbidden refresh is skipped while allowed refresh is witnessed.
        with bind_credential_lease(lease):
            assert await preflight.preflight(SimpleNamespace(), refresh_if_dirty=True) is None
        assert calls.count("state") == 1
        assert calls.count("refresh") == expected_refresh


@pytest.mark.asyncio
async def test_grant_downgrade_retires_active_generation_and_preserves_other_key(monkeypatch):
    claims = {
        "first": {"unity_mcp_permissions": ["read", "edit"]},
        "other": {"unity_mcp_permissions": ["read"]},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        first = (await fixture.service.validate("first")).lease
        other = (await fixture.service.validate("other")).lease
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def owner():
            async with first.own():
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()

        # Given active work and a same-principal, different-key healthy lease.
        task = asyncio.create_task(owner())
        await entered.wait()
        async with other.own():
            claims["first"] = {"unity_mcp_permissions": ["read"]}
            fixture.now[0] = 10
            try:
                # When renewal observes a downgrade, then old authority is fenced and cancelled.
                with pytest.raises(CredentialLeaseError):
                    await first.check()
                await cancelled.wait()
                assert first.retired
                assert fixture.service._lease_owners == 2
                await other.check()
                assert not other.retired
            finally:
                release.set()
                await task
        replacement = (await fixture.service.validate("first")).lease
        assert replacement is not None and replacement is not first
        assert (
            replacement.permissions.permissions
            == parse_permissions(claims["first"], strict=False).permissions
        )
        assert fixture.service._lease_owners == 0


@pytest.mark.asyncio
async def test_malformed_claim_is_refused_by_real_validation_without_a_lease(monkeypatch):
    # Given an otherwise valid same-user provider verdict with a malformed present claim.
    async with permission_service(
        monkeypatch, {"key": {"unity_mcp_permissions": "read"}}
    ) as fixture:
        # When real authentication publishes its verdict, then no lease/cache authority exists.
        result = await fixture.service.validate("key")
        assert not result.valid and result.lease is None
        assert fixture.service._key_leases == {} and fixture.service._cache == {}


@pytest.mark.asyncio
async def test_mutating_provider_metadata_cannot_mutate_an_already_validated_grant(monkeypatch):
    metadata = {"unity_mcp_permissions": ["read"]}
    async with permission_service(monkeypatch, {"key": metadata}) as fixture:
        lease = (await fixture.service.validate("key")).lease
        # Given a validated immutable grant, with the original raw metadata still held by a caller.
        metadata["unity_mcp_permissions"].append("edit")
        # When that mutable metadata changes without renewal, then cached authority stays unchanged.
        cached = await fixture.service.validate("key")
        assert cached.lease is lease
        with pytest.raises(OperationPermissionError):
            authorize_operation(lease.permissions, "manage_asset", {"action": "set_labels"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial,renewed,retired",
    [
        (["read", "edit"], ["edit", "read"], False),
        (["read"], ["read", "run"], True),
        (None, ["read", "test", "edit", "run"], True),
    ],
)
async def test_renewal_compares_normalized_grants_and_explicit_scope_provenance(
    monkeypatch, initial, renewed, retired
):
    # Given a lease whose metadata can change at the finite renewal boundary.
    claims = {"key": None if initial is None else {"unity_mcp_permissions": initial}}
    async with permission_service(monkeypatch, claims) as fixture:
        lease = (await fixture.service.validate("key")).lease
        claims["key"] = {"unity_mcp_permissions": renewed}
        fixture.now[0] = 10
        # When order alone changes it remains the same immutable authority; upgrades and
        # legacy-to-explicit transitions retire the old generation just like downgrades.
        if retired:
            with pytest.raises(CredentialLeaseError):
                await lease.check()
        else:
            await lease.check()
        assert lease.retired is retired


@pytest.mark.asyncio
async def test_downgrade_generation_cannot_join_an_old_shared_read_cache(monkeypatch):
    from services.tools.shared_tool_reads import SharedToolReads

    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    claims = {"key": {"unity_mcp_permissions": ["read", "edit"]}}
    async with permission_service(monkeypatch, claims) as fixture:
        old = (await fixture.service.validate("key")).lease
        reads = SharedToolReads(freshness_s=60, retention_s=60)
        key = ("same-user", "same-editor")
        replacement = None
        old_read = None
        calls = []

        async def fetch():
            calls.append(1)
            return {"observation": len(calls)}

        try:
            # Given an actual retained remote cache whose same-generation reuse is witnessed.
            async with old.own():
                async with reads.session(key) as read:
                    old_read = read
                    assert await read.fetch(fetch) == {"observation": 1}
                assert old_read.expiry_task is not None and old_read._flight is not None
                async with reads.session(key) as same_generation:
                    assert same_generation is old_read
                    assert await same_generation.fetch(fetch) == {"observation": 1}
                    assert calls == [1]
            assert old_read.expiry_task is not None and old_read._flight is not None
            claims["key"] = {"unity_mcp_permissions": ["read"]}
            fixture.now[0] = 10
            old_record = fixture.service._lease_records[old.generation]
            with pytest.raises(CredentialLeaseError):
                await old.check()
            # Retirement wakes consumers before its invalidated validator has fully exited.
            # Wait for this exact renewal, without retrying or bypassing real authentication.
            renewal = old_record.renewal
            if renewal is not None:
                await asyncio.wait_for(asyncio.shield(renewal), 1)
            assert old.retired and not fixture.service._inflight
            fresh = await fixture.service.validate("key")
            assert fresh.valid and not fresh.overloaded and fresh.lease is not None
            replacement = fresh.lease
            assert replacement.generation is not old.generation
            assert replacement.permissions == parse_permissions(claims["key"], strict=False)
            # When weaker fresh authority reads the same logical key, the still-retained
            # old cache must remain unjoinable, rather than having expired before the assertion.
            async with replacement.own():
                async with reads.session(key) as read:
                    assert read is not old_read and old_read._flight is not None
                    assert await read.fetch(fetch) == {"observation": 2}
            assert len(calls) == 2 and old.retired and old_read._flight is not None
        finally:
            # Invalidation uses the captured partition handle for cleanup, without renewing it.
            with bind_credential_lease(old):
                await reads.invalidate(key)
            if replacement is not None:
                with bind_credential_lease(replacement):
                    await reads.invalidate(key)
        assert old_read._flight is None and old_read.expiry_task is None
        assert not reads._loops[asyncio.get_running_loop()]


@pytest.mark.asyncio
@pytest.mark.parametrize("grant,action,expected_probe", [("edit", "clear", 0), ("read", "get", 1)])
async def test_optional_readiness_probe_does_not_infer_read_from_another_grant(
    command_hub, monkeypatch, grant, action, expected_probe
):
    # Given a live same-user route and nonzero readiness budget, with a real command positive control.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "1")
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    probes = []

    async def probe(_session, _identity):
        probes.append(1)
        return "ready"

    monkeypatch.setattr(PluginHub, "_readiness_probe", probe)
    claims = {name: {"unity_mcp_permissions": [grant]} for name in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # When the routed real operation runs, then forbidden ping is skipped, not claimed observed-ready.
        with bind_credential_lease(requester):
            result = await PluginHub.send_command_for_instance(
                "Fixture@hash", "read_console", {"action": action}, user_id="alice"
            )
        assert result["success"] is True and len(command_hub.sent) == 1
        assert len(probes) == expected_probe


@pytest.mark.asyncio
async def test_plugin_restriction_refuses_legacy_requester_batch_before_settings(
    command_hub, monkeypatch
):
    batch = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    calls = []

    async def state(_name):
        return "session"

    async def settings(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("Forbidden settings read")

    monkeypatch.setattr(batch, "_get_max_commands_from_editor_state", settings)
    async with permission_service(
        monkeypatch, {"request": None, "plugin": {"unity_mcp_permissions": ["read"]}}
    ) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # Given legacy requester authority, but a restricted selected plugin credential.
        # When a mixed custom batch is admitted, then plugin restrictions apply before settings/native work.
        with bind_credential_lease(requester), pytest.raises(OperationPermissionError):
            await batch.batch_execute(
                SimpleNamespace(get_state=state), [{"tool": "registered_custom", "params": {}}]
            )
        assert calls == [] and command_hub.sent == []


@pytest.mark.asyncio
async def test_downgrade_fences_detached_test_wait_without_a_second_poll(monkeypatch, tmp_path):
    from services.credential_lease import require_credential_lease
    from services.test_wait_journal import DeadlineCheckpoint, WaitJournal, WaitOperation
    from transport.test_job_tasks import CapturedWait, TestJobTasks, WaitAdmission

    claims = {"key": {"unity_mcp_permissions": ["read", "test"]}}
    async with permission_service(monkeypatch, claims) as fixture:
        lease = (await fixture.service.validate("key")).lease
        entered, release = asyncio.Event(), asyncio.Event()
        polls = []

        async def execute(_captured):
            assert await require_credential_lease() is lease
            polls.append("get_test_job")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass  # The already-running poll may finish despite cancellation.
            await require_credential_lease()
            polls.append("forbidden-second-poll")
            return {"result": {"status": "running"}}

        async def unused(*_args):
            pytest.fail("Detached worker must not recapture a caller credential")

        now, wall = asyncio.get_running_loop().time(), time.time()
        operation = WaitOperation(
            owner="hosted:v1:alice",
            user_id="alice",
            project="hash",
            instance="Fixture@hash",
            session_id="session",
            job_id="owned",
            wait=DeadlineCheckpoint.capture(1, now=wall),
            outer=DeadlineCheckpoint.capture(2, now=wall),
        )
        captured = CapturedWait(operation, now + 1, now + 2, {}, credential_lease=lease)
        journal, admission = WaitJournal(tmp_path / "permission-waits"), WaitAdmission()
        runtime = TestJobTasks(
            journal, capture=unused, execute=execute, owner=unused, admission=admission
        )
        runtime._closed = False
        record = journal.create(operation)
        slot = admission.acquire(operation)
        runtime._scopes[record.task_id] = anyio.CancelScope()
        worker = asyncio.create_task(
            runtime._run(record.task_id, captured, slot), context=Context()
        )
        runtime._workers[record.task_id] = worker
        try:
            # Given detached owned work whose first real lease check completed.
            await entered.wait()
            claims["key"] = {"unity_mcp_permissions": ["read"]}
            fixture.now[0] = 10
            # When renewal observes a downgrade, then no later poll or persisted authority escapes.
            with pytest.raises(CredentialLeaseError):
                await lease.check()
            release.set()
            await worker
            stored = journal.get(record.task_id, "hosted:v1:alice")
            assert stored.status == "failed" and polls == ["get_test_job"]
            assert admission.active == {} and fixture.service._lease_owners == 0
            assert "credential_lease" not in stored.operation.model_dump()
            assert "unity_mcp_permissions" not in json.dumps(
                stored.operation.model_dump(mode="json")
            )
        finally:
            release.set()
            await asyncio.gather(worker, return_exceptions=True)
            await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http"])
async def test_local_operations_keep_uncredentialed_dispatch_semantics(monkeypatch, mode):
    from services.operation_permissions import authorize_remote_operation, authorize_remote_resource

    # Given an ordinary local transport without a credential lease.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", mode)
    # When public/final policy helpers see existing local operations, then policy is a no-op.
    await authorize_remote_operation("existing_local_custom", {})
    await authorize_remote_resource("fixture://local-extension")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"action": "diagnose_text", "target": 7}, id="text"),
        pytest.param(
            {"action": "diagnose_raycast", "target": 7, "screen_position": [12.0, 34.0]},
            id="raycast-omitted",
        ),
        pytest.param(
            {
                "action": "diagnose_raycast",
                "target": 7,
                "screen_position": [12.0, 34.0],
                "allow_custom_filters": False,
            },
            id="raycast-false",
        ),
    ],
)
async def test_read_only_ugui_diagnostics_pass_public_and_native_admission(
    command_hub, monkeypatch, params
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {key: {"unity_mcp_permissions": ["read"]} for key in ("request", "plugin")}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        # Both public/default and canonical native classification remain reads.
        for native in (False, True):
            authorize_operation(requester.permissions, "manage_ugui", params, native=native)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_ugui", params)
        assert result["success"] is True and len(command_hub.sent) == 1
        assert command_hub.sent[0]["params"] == params and PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_claim,plugin_claim,allowed",
    [
        pytest.param(["read", "edit", "run"], ["read", "edit", "run"], True, id="full"),
        pytest.param(["edit", "run"], ["read", "edit", "run"], False, id="request-no-read"),
        pytest.param(["read", "run"], ["read", "edit", "run"], False, id="request-no-edit"),
        pytest.param(["read", "edit"], ["read", "edit", "run"], False, id="request-no-run"),
        pytest.param(["read", "edit", "run"], ["edit", "run"], False, id="plugin-no-read"),
        pytest.param(["read", "edit", "run"], ["read", "run"], False, id="plugin-no-edit"),
        pytest.param(["read", "edit", "run"], ["read", "edit"], False, id="plugin-no-run"),
        pytest.param(None, None, True, id="legacy-both"),
        pytest.param(None, ["read"], False, id="legacy-request-scoped-plugin"),
        pytest.param(["read"], None, False, id="scoped-request-legacy-plugin"),
    ],
)
async def test_raycast_user_code_requires_each_grant_on_both_authorities(
    command_hub, monkeypatch, request_claim, plugin_claim, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": None if request_claim is None else {"unity_mcp_permissions": request_claim},
        "plugin": None if plugin_claim is None else {"unity_mcp_permissions": plugin_claim},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        params = {
            "action": "diagnose_raycast",
            "target": 7,
            "screen_position": [12.0, 34.0],
            "allow_custom_filters": True,
        }
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_ugui", params)
        assert result["success"] is allowed
        assert len(command_hub.sent) == int(allowed) and PluginHub._pending == {}
        if not allowed:
            assert result["error"] == "permission_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "false", "true", 0, 1, 0.0, {}, []])
async def test_malformed_raycast_optin_refuses_before_native_io(command_hub, monkeypatch, flag):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        key: {"unity_mcp_permissions": ["read", "test", "edit", "run"]}
        for key in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        params = {
            "action": "diagnose_raycast",
            "target": 7,
            "screen_position": [12.0, 34.0],
            "allow_custom_filters": flag,
        }
        # Even explicit full grants cannot turn a malformed flag into a read/default.
        with pytest.raises(OperationPermissionError):
            authorize_operation(requester.permissions, "manage_ugui", params)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", "manage_ugui", params)
        assert result["error"] == "permission_denied"
        assert command_hub.sent == [] and PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["request", "plugin"])
@pytest.mark.parametrize("missing", ["read", "edit", "run"])
async def test_mixed_raycast_batch_denies_before_settings_and_dispatch(
    command_hub, monkeypatch, restricted, missing
):
    batch = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    calls = []

    async def forbidden(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("Denied mixed raycast batch reached settings or transport")

    async def state(name):
        return {"unity_session_id": "session", "unity_instance": "Fixture@hash"}.get(name)

    monkeypatch.setattr(batch, "_get_max_commands_from_editor_state", forbidden)
    monkeypatch.setattr(batch, "send_with_unity_instance", forbidden)
    full = ["read", "edit", "run"]
    claims = {key: {"unity_mcp_permissions": full} for key in ("request", "plugin")}
    claims[restricted] = {"unity_mcp_permissions": [grant for grant in full if grant != missing]}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        first = {
            "tool": "read_console" if missing == "read" else "manage_ugui",
            "params": {"action": "clear"}
            if missing == "read"
            else {"action": "diagnose_text", "target": 7},
        }
        # The first member is independently permitted on BOTH sides; the later
        # opt-in cannot leave its effects behind when the complete batch is denied.
        for lease in (requester, plugin):
            authorize_operation(lease.permissions, first["tool"], first["params"], native=True)
        commands = [
            first,
            {
                "tool": "manage_ugui",
                "params": {
                    "action": "diagnose_raycast",
                    "target": 7,
                    "screen_position": [12.0, 34.0],
                    "allow_custom_filters": True,
                },
            },
        ]
        with bind_credential_lease(requester), pytest.raises(OperationPermissionError):
            await batch.batch_execute(SimpleNamespace(get_state=state), commands)
        assert calls == [] and command_hub.sent == [] and PluginHub._pending == {}


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
@pytest.mark.parametrize("nested", ["batch_execute", "batch_validate", "BATCH_VALIDATE"])
def test_preview_and_execution_share_closed_member_policy(outer, nested):
    scoped = parse_permissions(
        {"unity_mcp_permissions": ["read", "test", "edit", "run"]}, strict=False
    )
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            scoped, outer, {"commands": [{"tool": nested, "params": {}}]}, native=True
        )


def test_preview_requires_same_full_member_grants_as_execution():
    commands = {"commands": [{"tool": "manage_gameobject", "params": {"action": "create"}}]}
    readonly = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=False)
    editing = parse_permissions({"unity_mcp_permissions": ["edit"]}, strict=False)
    for outer in ("batch_execute", "batch_validate"):
        with pytest.raises(OperationPermissionError):
            authorize_operation(readonly, outer, commands, native=True)
        authorize_operation(editing, outer, commands, native=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_claim,plugin_claim,allowed",
    [
        (["edit"], ["edit"], True),
        (["read"], ["edit"], False),
        (["edit"], ["read"], False),
        (None, ["read"], False),
    ],
)
async def test_preview_alias_final_native_fence_retains_both_authorities(
    command_hub, monkeypatch, request_claim, plugin_claim, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": None if request_claim is None else {"unity_mcp_permissions": request_claim},
        "plugin": {"unity_mcp_permissions": plugin_claim},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command(
                "session",
                "batch_validate",
                {"commands": [{"tool": "manage_gameobject", "params": {"action": "create"}}]},
            )
        assert result["success"] is allowed
        assert len(command_hub.sent) == (1 if allowed else 0)
        assert PluginHub._pending == {}
        if not allowed:
            assert result["error"] == "permission_denied"
