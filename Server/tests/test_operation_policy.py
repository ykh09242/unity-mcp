"""Reviewed operation effects are independent of permissions and client hints."""

import pytest
import importlib

from services.operation_policy import (
    OperationLifetime,
    OperationMode,
    resolve_operation_policy,
)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "name,params",
    [
        ("get_unknown_future_tool", {}),
        ("manage_scene", {"action": "get_unknown", "readOnlyHint": True}),
        ("execute_custom_tool", {"tool_name": "get_safe", "read_only": True}),
        ("execute_code", {"action": "execute", "safety_checks": True}),
        ("manage_reference_graph", {"action": "runtime_start"}),
        ("manage_reference_graph", {"action": "query", "options": {"consistency": "checked"}}),
    ],
)
def test_unknown_arbitrary_and_stateful_operations_never_inherit_read_eligibility(
    name, params, native
):
    # Given / When: semantic names and hints are untrusted input.
    policy = resolve_operation_policy(name, params, native=native)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize(
    "name,params",
    [
        ("ping", {}),
        ("query_physics", {"action": "raycast"}),
        ("manage_script", {"action": "get_sha"}),
    ],
)
def test_registered_override_cannot_inherit_builtin_mode(name, params):
    # Given / When
    policy = resolve_operation_policy(name, params, native=True, trusted_builtin=False)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE
    assert policy.reason == "untrusted_handler"


@pytest.mark.parametrize(
    "name,params",
    [
        ("ping", {}),
        ("get_editor_state", {}),
        ("get_compile_job", {}),
        ("get_test_job", {}),
        ("view_test_results", {}),
        ("export_test_job", {"job_id": "completed", "format": "json"}),
        ("cancel_test_job", {}),
        ("manage_build", {"action": "status"}),
        ("manage_build", {"action": "cancel"}),
    ],
)
def test_reviewed_status_and_cancellation_bypass_ordinary_work(name, params):
    # Given / When
    policy = resolve_operation_policy(name, params, native=True)
    # Then
    assert policy.mode == OperationMode.CONTROL
    assert policy.lifetime == OperationLifetime.COMMAND


@pytest.mark.parametrize("native", [False, True])
def test_console_get_stays_exclusive_during_native_test_cleanup(native):
    # Console reads temporarily alter global Console flags and filter text.
    policy = resolve_operation_policy("read_console", {"action": "get"}, native=native)
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize("name", ["query_physics", "manage_physics"])
def test_focused_alias_uses_canonical_action_policy(name):
    # Given / When
    policy = resolve_operation_policy(name, {"action": "raycast"}, native=True)
    # Then
    assert policy.mode == OperationMode.SHARED_READ


def test_capability_cannot_borrow_sibling_read_action():
    # Given / When
    policy = resolve_operation_policy("apply_physics_force", {"action": "raycast"}, native=True)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


def test_public_refresh_capable_wrapper_does_not_inherit_native_read_mode():
    # Given / When
    public = resolve_operation_policy("find_gameobjects", {})
    native = resolve_operation_policy("find_gameobjects", {}, native=True)
    # Then
    assert public.mode == OperationMode.EXCLUSIVE
    assert public.lifetime == OperationLifetime.TRIGGER_ONLY
    assert native.mode == OperationMode.SHARED_READ


@pytest.mark.parametrize("flag", [True, None, "false", 0, [], {}])
def test_custom_callback_flag_is_shared_only_for_exact_false_or_omission(flag):
    # Given / When
    policy = resolve_operation_policy(
        "manage_ugui", {"action": "diagnose_raycast", "allow_custom_filters": flag}, native=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize(
    "params",
    [{"action": "diagnose_raycast"}, {"action": "diagnose_raycast", "allow_custom_filters": False}],
)
def test_safe_native_raycast_diagnostic_has_no_custom_callback_opt_in(params):
    # Given / When
    policy = resolve_operation_policy("manage_ugui", params, native=True)
    # Then
    assert policy.mode == OperationMode.SHARED_READ


@pytest.mark.parametrize(
    "preview,mode",
    [(True, "shared_read"), (False, "exclusive"), (None, "exclusive"), ("true", "exclusive")],
)
def test_source_patch_preview_uses_exact_boolean_policy(preview, mode):
    # Given / When
    policy = resolve_operation_policy(
        "ui_source", {"action": "patch_source", "preview": preview}, native=True
    )
    # Then
    assert policy.mode.value == mode


def test_native_menu_execution_cannot_spoof_an_inspection_action():
    # Given / When
    policy = resolve_operation_policy(
        "execute_menu_item_observed", {"action": "inspect"}, native=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize(
    "commands",
    [
        [{"tool": "ping", "params": {}}],
        [{"tool": "query_physics", "params": {"action": "raycast"}}],
        [{"tool": "ping", "params": {}}, {"tool": "manage_build", "params": {"action": "build"}}],
    ],
)
def test_batch_envelope_is_exclusive_even_when_children_are_reviewed_reads(commands):
    # Given / When: native core revalidates children under parent ownership.
    policy = resolve_operation_policy("batch_execute", {"commands": commands}, native=True)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


def test_unobservable_package_resolve_is_explicitly_trigger_only():
    # Given / When
    policy = resolve_operation_policy(
        "manage_packages", {"action": "resolve_packages"}, native=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE
    assert policy.lifetime == OperationLifetime.TRIGGER_ONLY


@pytest.mark.parametrize("action", ["ui_click", "key", "mouse", "release_all"])
def test_stable_play_is_not_encoded_as_global_busy_state(action):
    # Given / When: mode depends on operation effects, not play-state caller hints.
    policy = resolve_operation_policy(
        "manage_input", {"action": action, "is_playing": True}, native=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE
    assert policy.lifetime == OperationLifetime.COMMAND


@pytest.mark.parametrize(
    "name,params",
    [
        ("run_tests", {}),
        ("manage_build", {"action": "build"}),
        ("manage_play_scenario", {"action": "suite_run"}),
        ("manage_packages", {"action": "add_package"}),
        ("manage_packages", {"action": "list_packages"}),
        ("manage_editor", {"action": "play", "wait_until": "first_frame"}),
    ],
)
def test_tracked_lifetimes_match_only_native_owned_adapter_routes(name, params):
    # Given / When
    policy = resolve_operation_policy(name, params, native=True)
    # Then
    assert policy.lifetime == OperationLifetime.TRACKED_JOB


@pytest.mark.parametrize(
    "name,params",
    [
        ("refresh_unity", {}),
        ("manage_navigation", {"action": "bake"}),
        ("generate_model", {"action": "generate"}),
        ("manage_camera", {"action": "screenshot"}),
        ("manage_editor", {"action": "play"}),
    ],
)
def test_untracked_starts_do_not_claim_native_quiescence(name, params):
    # Given / When
    policy = resolve_operation_policy(name, params, native=True)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE
    assert policy.lifetime == OperationLifetime.TRIGGER_ONLY


@pytest.mark.parametrize("trusted", [None, "true", 1, [], {}])
def test_truthy_client_metadata_is_not_an_identity_witness(trusted):
    # Given / When
    policy = resolve_operation_policy("ping", {}, native=True, trusted_builtin=trusted)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize("native", [False, True])
def test_snapshot_builtin_identity_does_not_prove_target_getters_are_safe(native):
    # Given: UguiSnapshotCapture reads custom text/color getters and IsInteractable.
    # When: discovery hints and handler identity cannot attest those bound objects.
    policy = resolve_operation_policy(
        "capture_ui_snapshot", {}, native=native, trusted_builtin=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


def test_menu_inspection_is_not_a_reviewed_shared_read():
    # Given / When: availability inspection is distinct from a plain metadata page.
    policy = resolve_operation_policy("inspect_menu_item", {}, native=True)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize(
    "name,missing_mode",
    [
        ("ping", "control"),
        ("get_selection", "shared_read"),
        ("find_gameobjects", "shared_read"),
        ("manage_build", "exclusive"),
        ("query_physics", "exclusive"),
        ("manage_ugui", "exclusive"),
    ],
)
def test_missing_null_empty_and_nonstring_actions_have_distinct_contracts(name, missing_mode):
    # Given / When
    missing = resolve_operation_policy(name, {}, native=True)
    null = resolve_operation_policy(name, {"action": None}, native=True)
    malformed = [
        resolve_operation_policy(name, {"action": value}, native=True)
        for value in ("", False, 0, [], {})
    ]
    # Then
    assert missing.mode.value == missing_mode
    assert null == missing
    assert all(policy.mode == OperationMode.EXCLUSIVE for policy in malformed)


@pytest.mark.parametrize("native", [False, True])
def test_package_recovery_diagnostics_remain_control_while_request_owner_is_blocked(native):
    # Given: package ping reads cached ownership/readiness and never starts a Client request.
    # When
    policy = resolve_operation_policy("manage_packages", {"action": "ping"}, native=native)
    # Then
    assert policy.mode == OperationMode.CONTROL
    assert policy.lifetime == OperationLifetime.COMMAND


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments,expected_mode,expected_action",
    [
        ("get_sha", {}, "shared_read", "get_sha"),
        ("validate_script", {"level": "basic"}, "shared_read", "validate"),
        ("validate_script", {"level": "standard"}, "shared_read", "validate"),
        ("find_in_file", {"pattern": "class"}, "exclusive", "read"),
    ],
)
async def test_actual_script_wrapper_native_routes_fit_the_public_reservation(
    monkeypatch, name, arguments, expected_mode, expected_action
):
    # Given: invoke the real wrapper; substitute only the external peer boundary.
    module = importlib.import_module(
        "services.tools.find_in_file" if name == "find_in_file" else "services.tools.manage_script"
    )
    observed = []

    async def target(_ctx):
        return "policy-fixture"

    async def peer(_send, instance, command, params):
        observed.append((command, params))
        assert instance == "policy-fixture"
        return {"success": True, "data": {"contents": "class PolicyFixture {}"}}

    monkeypatch.setattr(module, "get_unity_instance_from_context", target)
    monkeypatch.setattr(module, "send_with_unity_instance", peer)
    params = {"uri": "Assets/PolicyFixture.cs", **arguments}
    # When
    result = await getattr(module, name)(object(), **params)
    public = resolve_operation_policy(name, params)
    native_name, native_params = observed[0]
    native = resolve_operation_policy(native_name, native_params, native=True)
    # Then: this is a route contract, not a replacement for framed SDK integration.
    assert result["success"] is True
    assert len(observed) == 1
    assert native_name == "manage_script"
    assert native_params["action"] == expected_action
    assert public.mode.value == expected_mode
    assert native.mode == public.mode


@pytest.mark.parametrize("level", [None, "basic", "standard", "BASIC"])
def test_only_audited_native_validation_levels_are_shared(level):
    # Given / When: omitted level uses native standard, not project compilation.
    params = {"action": "validate"}
    if level is not None:
        params["level"] = level
    policy = resolve_operation_policy("manage_script", params, native=True)
    # Then
    assert policy.mode == OperationMode.SHARED_READ


@pytest.mark.parametrize("level", ["strict", "comprehensive", "", "future", False, 0, [], {}, None])
def test_unreviewed_validation_levels_do_not_borrow_safe_wrapper_policy(level):
    # Given / When: explicit null differs from omitted native default.
    policy = resolve_operation_policy(
        "manage_script", {"action": "validate", "level": level}, native=True
    )
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize(
    "action", ["read", "validate_edit", "preview_edit", "future_read", "create", "update", "delete"]
)
def test_script_read_audit_does_not_admit_other_actions(action):
    # Given / When
    policy = resolve_operation_policy("manage_script", {"action": action}, native=True)
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE


@pytest.mark.parametrize("action", ["get_sha", "validate", "read"])
def test_native_script_read_rules_do_not_admit_unaudited_public_router_actions(action):
    # Given / When: public manage_script has its own schema and wrapper review.
    policy = resolve_operation_policy("manage_script", {"action": action})
    # Then
    assert policy.mode == OperationMode.EXCLUSIVE
