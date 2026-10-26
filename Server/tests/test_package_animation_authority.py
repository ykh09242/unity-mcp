"""Compile-capable packages and persistent Animator defaults need combined authority."""

import pytest

from services.operation_permissions import (
    OperationPermissionError,
    authorize_operation,
    parse_permissions,
    required_permissions,
)


@pytest.mark.parametrize(
    "action",
    [
        "add_package",
        "remove_package",
        "embed_package",
        "resolve_packages",
        "add_registry",
        "remove_registry",
    ],
)
@pytest.mark.parametrize("native", [False, True])
def test_package_mutations_require_edit_and_run(action, native):
    # Given package mutations that can resolve or import executable Editor code.
    params = {"action": action}
    # When scoped authority admits the complete public or native action.
    required = required_permissions("manage_packages", params, native=native)
    # Then neither edit nor run alone can authorize it.
    assert {p.value for p in required} == {"edit", "run"}
    for grants in (["edit"], ["run"]):
        with pytest.raises(OperationPermissionError):
            authorize_operation(
                parse_permissions({"unity_mcp_permissions": grants}, strict=True),
                "manage_packages",
                params,
                native=native,
            )


def test_batch_package_mutation_keeps_combined_requirement():
    # Given a native batch containing a package installation.
    params = {"commands": [{"tool": "manage_packages", "params": {"action": "add_package"}}]}
    # When the shared batch policy classifies it.
    required = required_permissions("batch_execute", params)
    # Then batching cannot remove the execution grant.
    assert {p.value for p in required} == {"edit", "run"}


@pytest.mark.parametrize(
    "extra", [{}, {"runtime_only": False}, {"properties": {"runtime_only": True}}]
)
def test_animator_automatic_defaults_require_edit_and_run(extra):
    # Given automatic behavior that can persist controller defaults in Edit mode.
    params = {"action": "animator_set_parameter", **extra}
    # When authority is calculated without trusting the caller's runtime claim.
    required = required_permissions("manage_animation", params)
    # Then edit is included even if a nested property claims runtime-only behavior.
    assert {p.value for p in required} == {"edit", "run"}


def test_explicit_runtime_only_animator_retains_run_only_authority():
    # Given an explicit native-enforced runtime-only operation.
    params = {"action": "animator_set_parameter", "runtime_only": True}
    # When public and native operations are classified.
    required = [
        required_permissions("manage_animation", params, native=native) for native in (False, True)
    ]
    # Then Play-mode controls remain usable with run alone.
    assert all({p.value for p in grants} == {"run"} for grants in required)


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_runtime_only_non_boolean_is_denied(value):
    # Given malformed opt-in input.
    params = {"action": "animator_set_parameter", "runtime_only": value}
    # When it reaches policy admission, then it fails closed.
    with pytest.raises(OperationPermissionError):
        required_permissions("manage_animation", params)


@pytest.mark.parametrize("key", ["runtimeOnly", "RUNTIME_ONLY", "Runtime_Only"])
def test_conflicting_runtime_only_alias_cannot_lower_authority(key):
    # Given two spellings that could override the native normalized flag.
    params = {"action": "animator_set_parameter", "runtime_only": True, key: False}
    # When an explicit runtime-only grant is requested, then ambiguity is denied.
    with pytest.raises(OperationPermissionError):
        required_permissions("manage_animation", params, native=True)


def test_runtime_only_batch_preserves_native_guard_requirement():
    # Given an explicit runtime-only operation nested in a native batch.
    params = {
        "commands": [
            {
                "tool": "manage_animation",
                "params": {"action": "animator_set_parameter", "runtime_only": True},
            }
        ]
    }
    # When batch admission recursively classifies the native-enforced operation.
    required = required_permissions("batch_execute", params)
    # Then no persistent edit is authorized by the batch envelope.
    assert {p.value for p in required} == {"run"}
