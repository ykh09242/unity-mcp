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
RUN = frozenset({Permission.RUN})
COMPILE = frozenset({Permission.EDIT, Permission.RUN})

CASES = [
    (tool, action, expected)
    for tool, actions, expected in (
        ("manage_netcode", "capabilities preflight", READ),
        ("manage_input_actions", "capabilities read", READ),
        (
            "manage_input_actions",
            "create_asset add_map add_action add_binding set_binding remove_binding",
            COMPILE,
        ),
        ("manage_localization", "capabilities list_locales list_tables inspect", READ),
        ("manage_localization", "set_string_entry set_fallbacks set_asset_entry", EDIT),
        ("manage_addressables", "inspect preflight analyze_layout", READ),
        ("manage_addressables", "set_address move_entry set_profile_value", EDIT),
        ("manage_timeline", "capabilities read read_director", READ),
        (
            "manage_timeline",
            "create add_track add_clip assign bind preview_start seek preview_stop",
            EDIT,
        ),
        ("manage_simulation_profiles", "capabilities list get diff validate receipt", READ),
        ("manage_simulation_profiles", "capture delete apply restore", EDIT),
        (
            "manage_profiler",
            "memory_aggregate memory_analyze_snapshots memory_snapshot_reader_status",
            READ,
        ),
        ("manage_input", "player_status", READ),
        ("manage_input", "gamepad wheel pair_player release_player", RUN),
        ("manage_play_scenario", "player_suite_build", COMPILE),
        ("manage_audio", "diagnose get_routing read_import_settings", READ),
        ("manage_audio", "set_routing apply_import_settings", EDIT),
        ("manage_physics", "diagnose_pair", READ),
        ("manage_profiler", "window_status window_result", READ),
        ("manage_profiler", "window_start window_cancel", RUN),
        ("manage_sprite_atlas", "capabilities read", READ),
        ("manage_sprite_atlas", "create configure add_packables remove_packables pack", EDIT),
        ("manage_terrain", "get_info get_region get_trees get_layer", READ),
        ("manage_terrain", "set_region set_trees set_layer", EDIT),
        ("manage_sprite", "get_geometry", READ),
        ("manage_sprite", "set_geometry", EDIT),
        ("manage_probuilder", "get_topology", READ),
        ("manage_vfx", "vfx_get_exposed_properties", READ),
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


@pytest.mark.parametrize(
    "tool",
    [
        "manage_navigation",
        "script_change_set",
        "manage_sprite_atlas",
        "manage_terrain",
        "manage_netcode",
        "manage_input_actions",
        "manage_localization",
        "manage_addressables",
        "manage_timeline",
        "manage_simulation_profiles",
    ],
)
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


def test_package_authoring_batch_preserves_read_and_import_compile_requirements():
    batch = {
        "commands": [
            {"tool": "manage_netcode", "params": {"action": "preflight"}},
            {"tool": "manage_input_actions", "params": {"action": "set_binding"}},
        ]
    }
    expected = READ | COMPILE
    assert required_permissions("batch_execute", batch) == expected
    for missing in expected:
        with pytest.raises(OperationPermissionError):
            authorize_operation(OperationGrants(expected - {missing}, True), "batch_execute", batch)


@pytest.mark.parametrize("action", ["analyze_snapshots", "reader_status", "memory_reader_status"])
def test_memory_diagnostics_admit_only_actual_public_action_names(action):
    with pytest.raises(OperationPermissionError):
        required_permissions("manage_profiler", {"action": action})
