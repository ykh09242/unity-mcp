"""Remote grants match reference diagnostics and placement preview/apply effects."""

import pytest

from services.operation_permissions import (
    OperationPermissionError,
    Permission,
    authorize_operation,
    parse_permissions,
    required_permissions,
)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "name,action",
    [
        ("manage_asset", "get_dependencies"),
        ("manage_asset", "get_reverse_references"),
        ("manage_asset", "preview_move"),
        ("manage_asset", "preview_delete"),
        ("manage_scene", "validate_references"),
    ],
)
def test_reference_diagnostics_require_only_read(name, action, native):
    # Given: isolated read and edit authorities for a diagnostic action.
    read = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=True)
    edit = parse_permissions({"unity_mcp_permissions": ["edit"]}, strict=True)
    # When: admitting the public or native operation.
    authorize_operation(read, name, {"action": action}, native=native)
    # Then: asset preview and reference diagnostics grant no editing authority.
    with pytest.raises(OperationPermissionError):
        authorize_operation(edit, name, {"action": action}, native=native)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "flags,expected",
    [
        ({}, Permission.READ),
        ({"preview": None}, Permission.READ),
        ({"preview": True}, Permission.READ),
        ({"preview": "TRUE"}, Permission.READ),
        ({"preview": False}, Permission.EDIT),
        ({"preview": "FALSE"}, Permission.EDIT),
    ],
)
def test_placement_preview_defaults_read_and_explicit_apply_requires_edit(flags, expected, native):
    # Given: a placement operation with a supported preview scalar.
    params = {"action": "placement", **flags}
    authority = parse_permissions({"unity_mcp_permissions": [expected.value]}, strict=True)
    other = parse_permissions(
        {"unity_mcp_permissions": ["edit" if expected == Permission.READ else "read"]}, strict=True
    )
    # When: classifying the operation before optional lookup or transport.
    required = required_permissions("manage_gameobject", params, native=native)
    # Then: the preview/apply effect determines the independent permission.
    assert required == frozenset({expected})
    authorize_operation(authority, "manage_gameobject", params, native=native)
    with pytest.raises(OperationPermissionError):
        authorize_operation(other, "manage_gameobject", params, native=native)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("flag", ["", "yes", "0", " false ", 0, 1, 0.0, [], {}])
def test_malformed_placement_preview_never_becomes_read_or_apply(flag, native):
    # Given: even full authority cannot make an ambiguous flag safe to classify.
    full = parse_permissions(
        {"unity_mcp_permissions": ["read", "edit", "run", "test"]}, strict=True
    )
    # When: a malformed scalar is supplied to the placement action.
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            full, "manage_gameobject", {"action": "placement", "preview": flag}, native=native
        )
    # Then: admission fails rather than interpreting the value's truthiness.


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
@pytest.mark.parametrize("flag,expected", [(True, Permission.READ), (False, Permission.EDIT)])
def test_native_batch_placement_members_keep_their_own_effect_permissions(outer, flag, expected):
    # Given: a native batch member carrying the same preview flag as direct calls.
    params = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "placement", "preview": flag}}
        ]
    }
    # When: execution or action-preview admission classifies every member.
    result = required_permissions(outer, params)
    # Then: neither batch alias can downgrade an apply to a read.
    assert result == frozenset({expected})


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
def test_mixed_native_batch_requires_read_and_edit_before_dispatch(outer):
    # Given: read-only diagnostic work followed by a placement apply.
    params = {
        "commands": [
            {"tool": "manage_asset", "params": {"action": "preview_delete"}},
            {"tool": "manage_gameobject", "params": {"action": "placement", "preview": False}},
        ]
    }
    # When: the complete native batch is admitted.
    result = required_permissions(outer, params)
    # Then: it needs both independent grants before any member can run.
    assert result == frozenset({Permission.READ, Permission.EDIT})


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
def test_malformed_native_batch_placement_flag_fails_closed(outer):
    # Given: an otherwise well-formed placement member with a malformed flag.
    params = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "placement", "preview": "maybe"}}
        ]
    }
    # When: either native batch route classifies the member.
    with pytest.raises(OperationPermissionError):
        required_permissions(outer, params)
    # Then: no read classification can hide the member's uncertain effect.
