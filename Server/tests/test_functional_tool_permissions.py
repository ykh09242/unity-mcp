"""Focused public, native, receipt and batch policy retains source action grants."""

import pytest

from services.operation_permissions import (
    OperationGrants,
    OperationPermissionError,
    Permission,
    authorize_operation,
    required_permissions,
)
from services.registry.capability_catalog import CAPABILITIES


@pytest.mark.parametrize(
    "feature,action",
    [(feature, action) for feature in CAPABILITIES for action in feature.actions],
    ids=[f"{feature.name}:{action}" for feature in CAPABILITIES for action in feature.actions],
)
def test_every_feature_action_retains_public_native_and_batch_permissions(feature, action):
    params = {"action": action}
    expected = required_permissions(feature.source, params)
    batch = {"commands": [{"tool": feature.name, "params": params}]}
    for name, arguments, native in (
        (feature.name, params, False),
        (feature.name, params, True),
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
    "tool,params,expected",
    [
        ("ui_source", {"action": "patch_source"}, {"read"}),
        ("ui_source", {"action": "patch_source", "preview": False}, {"edit"}),
        ("gameobject_placement", {"action": "placement"}, {"read"}),
        ("gameobject_placement", {"action": "placement", "preview": "false"}, {"edit"}),
        ("scene_validate", {"action": "validate", "auto_repair": True}, {"read", "edit"}),
        ("capture_camera", {"action": "screenshot"}, {"edit", "run"}),
        ("ui_capture", {"action": "render_ui"}, {"edit", "run"}),
    ],
)
def test_feature_special_cases_retain_conservative_permission_floors(tool, params, expected):
    for native in (False, True):
        assert required_permissions(tool, params, native=native) == expected


@pytest.mark.parametrize(
    "tool,params",
    [
        ("scene_inspect", {"action": "create"}),
        ("scene_lifecycle", {"action": "screenshot"}),
        ("query_physics", {"action": "apply_force"}),
        ("ui_source", {"action": "patch_source", "preview": None}),
        ("gameobject_placement", {"action": "placement", "preview": 0}),
        ("asset_inspect", {"action": "set_labels"}),
        ("scene_inspect", {"action": {"value": "get_active"}}),
    ],
)
def test_feature_policy_never_grants_sibling_actions_or_invalid_special_flags(tool, params):
    authority = OperationGrants(frozenset(Permission), True)
    for native in (False, True):
        with pytest.raises(OperationPermissionError):
            authorize_operation(authority, tool, params, native=native)
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            authority, "batch_validate", {"commands": [{"tool": tool, "params": params}]}
        )


def test_metadata_search_internal_action_is_native_only():
    params = {"action": "search_metadata_v1"}
    assert required_permissions("asset_inspect", params, native=True) == {"read"}
    with pytest.raises(OperationPermissionError):
        required_permissions("asset_inspect", params)
