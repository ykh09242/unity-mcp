"""Source and structural preview/apply grants remain closed through native batches."""

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
    "name,params,expected",
    [
        ("manage_ui", {"action": "patch_source"}, Permission.READ),
        ("manage_ui", {"action": "patch_source", "preview": True}, Permission.READ),
        ("manage_ui", {"action": "patch_source", "preview": False}, Permission.EDIT),
        ("manage_scene_edits", {"action": "preview_structure"}, Permission.READ),
        ("manage_scene_edits", {"action": "apply_structure"}, Permission.EDIT),
        ("manage_prefabs", {"action": "preview_modify_contents"}, Permission.READ),
        ("manage_prefabs", {"action": "apply_preview"}, Permission.EDIT),
    ],
)
def test_source_and_structure_effects_require_their_independent_grant(
    name, params, expected, native
):
    # Given: a public/native request and exactly the grant matching its effect.
    authority = parse_permissions({"unity_mcp_permissions": [expected.value]}, strict=True)
    other = parse_permissions(
        {"unity_mcp_permissions": ["edit" if expected == Permission.READ else "read"]}, strict=True
    )
    # When: policy classifies the request before selection or transport.
    result = required_permissions(name, params, native=native)
    # Then: preview grants do not admit source or structural writes.
    assert result == frozenset({expected})
    authorize_operation(authority, name, params, native=native)
    with pytest.raises(OperationPermissionError):
        authorize_operation(other, name, params, native=native)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("preview", [None, "true", "false", "TRUE", " false ", 0, 1, 0.0, [], {}])
def test_patch_source_preview_rejects_null_and_every_nonboolean(preview, native):
    # Given: full grants and a source-patch flag that strict SDK/native parsing rejects.
    authority = parse_permissions(
        {"unity_mcp_permissions": ["read", "test", "edit", "run"]}, strict=True
    )
    # When: the policy is asked to classify its uncertain effect.
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            authority, "manage_ui", {"action": "patch_source", "preview": preview}, native=native
        )
    # Then: explicit null is not confused with the omitted default and strings are not coerced.


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
@pytest.mark.parametrize(
    "name,params,expected",
    [
        ("manage_ui", {"action": "patch_source"}, Permission.READ),
        ("manage_ui", {"action": "patch_source", "preview": True}, Permission.READ),
        ("manage_ui", {"action": "patch_source", "preview": False}, Permission.EDIT),
        ("manage_scene_edits", {"action": "preview_structure"}, Permission.READ),
        ("manage_scene_edits", {"action": "apply_structure"}, Permission.EDIT),
        ("manage_prefabs", {"action": "preview_modify_contents"}, Permission.READ),
        ("manage_prefabs", {"action": "apply_preview"}, Permission.EDIT),
    ],
)
def test_native_batch_members_retain_source_structure_effect_permissions(
    outer, name, params, expected
):
    # Given: a native member wrapped by execution or action-name preview admission.
    operation = {"commands": [{"tool": name, "params": params}]}
    # When: recursive member policy classifies the batch.
    result = required_permissions(outer, operation)
    # Then: neither outer route downgrades an apply member.
    assert result == frozenset({expected})


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
@pytest.mark.parametrize("preview", [None, "false", 0, {}])
def test_native_batch_patch_source_rejects_malformed_preview(outer, preview):
    # Given: a later source-patch member with a malformed preview flag.
    operation = {
        "commands": [
            {"tool": "manage_scene_edits", "params": {"action": "preview_structure"}},
            {"tool": "manage_ui", "params": {"action": "patch_source", "preview": preview}},
        ]
    }
    # When: either batch alias admits the complete member list.
    with pytest.raises(OperationPermissionError):
        required_permissions(outer, operation)
    # Then: ambiguity cannot be hidden behind an earlier safe preview.


@pytest.mark.parametrize("outer", ["batch_execute", "batch_validate"])
def test_mixed_source_structure_batch_requires_all_effects_before_dispatch(outer):
    # Given: a UI source preview followed by a prefab apply.
    operation = {
        "commands": [
            {"tool": "manage_ui", "params": {"action": "patch_source"}},
            {"tool": "manage_prefabs", "params": {"action": "apply_preview", "preview": True}},
        ]
    }
    # When: the complete native member list is classified.
    result = required_permissions(outer, operation)
    # Then: a spoofed preview parameter cannot downgrade the fixed apply action.
    assert result == frozenset({Permission.READ, Permission.EDIT})
