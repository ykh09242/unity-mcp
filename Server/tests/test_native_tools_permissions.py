"""Operation admission for bounded native authoring, reports and source recovery."""

import pytest

from services.operation_permissions import (
    OperationGrants,
    OperationPermissionError,
    Permission,
    authorize_operation,
    required_permissions,
)

READ = frozenset({Permission.READ})
EDIT = frozenset({Permission.EDIT})
COMPILE = frozenset({Permission.EDIT, Permission.RUN})

CASES = [
    (tool, action, expected)
    for tool, actions, expected in (
        ("manage_audio", "diagnose get_routing read_import_settings", READ),
        ("manage_audio", "set_routing apply_import_settings", EDIT),
        ("manage_physics", "diagnose_pair", READ),
        ("manage_build", "report_history report compare_reports", READ),
        ("manage_animation", "clip_get_curves clip_get_curve controller_get_graph", READ),
        (
            "manage_animation",
            "clip_replace_curve clip_delete_curve controller_modify_state "
            "controller_delete_state controller_modify_transition controller_delete_transition",
            EDIT,
        ),
        (
            "manage_navigation",
            "capabilities list read bake_status sample_position calculate_path",
            READ,
        ),
        ("manage_navigation", "add configure bake cancel_bake", EDIT),
        ("manage_script", "prepare_change_set get_change_set", READ),
        ("manage_script", "apply_change_set recover_change_set", COMPILE),
    )
    for action in actions.split()
]


@pytest.mark.parametrize("tool,action,expected", CASES)
def test_native_public_and_batch_require_the_same_operation_grants(tool, action, expected):
    params = {"action": action}
    batch = {"commands": [{"tool": tool, "params": params}]}
    for name, arguments, native in (
        (tool, params, False),
        (tool, params, True),
        ("batch_execute", batch, False),
        ("batch_validate", batch, False),
    ):
        assert required_permissions(name, arguments, native=native) == expected
        authorize_operation(OperationGrants(expected, True), name, arguments, native=native)
        for missing in expected:
            with pytest.raises(OperationPermissionError):
                authorize_operation(
                    OperationGrants(expected - {missing}, True), name, arguments, native=native
                )


@pytest.mark.parametrize(
    "action,expected",
    [("prepare", READ), ("status", READ), ("apply", COMPILE), ("recover", COMPILE)],
)
def test_public_change_set_alias_does_not_bypass_compile_permission(action, expected):
    params = {"action": action}
    assert required_permissions("script_change_set", params) == expected
    authorize_operation(OperationGrants(expected, True), "script_change_set", params)
    for missing in expected:
        with pytest.raises(OperationPermissionError):
            authorize_operation(
                OperationGrants(expected - {missing}, True), "script_change_set", params
            )
    # Native batch accepts the canonical manage_script actions, never the Python wrapper alias.
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            OperationGrants(frozenset(Permission), True), "script_change_set", params, native=True
        )
    with pytest.raises(OperationPermissionError):
        required_permissions(
            "batch_execute", {"commands": [{"tool": "script_change_set", "params": params}]}
        )


@pytest.mark.parametrize("tool", ["manage_navigation", "script_change_set"])
@pytest.mark.parametrize("action", [None, "", "unknown", False, 1, [], {}])
def test_new_tool_unknown_or_malformed_action_is_not_admitted(tool, action):
    with pytest.raises(OperationPermissionError):
        required_permissions(tool, {"action": action})


def test_mixed_batch_preserves_read_and_compilation_requirements():
    batch = {
        "commands": [
            {"tool": "manage_navigation", "params": {"action": "read"}},
            {"tool": "manage_script", "params": {"action": "recover_change_set"}},
        ]
    }
    expected = READ | COMPILE
    assert required_permissions("batch_execute", batch) == expected
    for missing in expected:
        with pytest.raises(OperationPermissionError):
            authorize_operation(OperationGrants(expected - {missing}, True), "batch_execute", batch)
