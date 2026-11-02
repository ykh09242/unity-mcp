"""Auditor observation grants are distinct from explicit compilation/settings writes."""

import pytest

from services.operation_permissions import (
    CUSTOM_GRANTS,
    Permission,
    OperationPermissionError,
    required_permissions,
)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("action", ["availability", "status", "issues"])
def test_observation_needs_read_only(action, native):
    assert required_permissions("manage_project_auditor", {"action": action}, native=native) == {
        Permission.READ
    }


@pytest.mark.parametrize("native", [False, True])
def test_start_needs_compilation_and_settings_authority(native):
    assert required_permissions("manage_project_auditor", {"action": "start"}, native=native) == (
        CUSTOM_GRANTS
    )


def test_native_batch_admits_only_real_auditor_actions():
    assert required_permissions(
        "batch_execute",
        {"commands": [{"tool": "manage_project_auditor", "params": {"action": "status"}}]},
    ) == {Permission.READ}
    with pytest.raises(OperationPermissionError):
        required_permissions("manage_project_auditor", {"action": "cancel"}, native=True)
