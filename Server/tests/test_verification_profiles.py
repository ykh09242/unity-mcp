import pytest
from pydantic import ValidationError

from models.verification import VerificationProfile, parse_verification_options
from services.verification.profiles import builtins, profile_permissions, resolve_profile


def test_builtins_are_independent_declarative_copies_without_storage():
    profiles = builtins()
    assert set(profiles) == {"script-check", "editmode-check", "playmode-check"}
    assert profiles["script-check"].test_mode is None
    assert profiles["editmode-check"].test_mode == "EditMode"
    assert profiles["playmode-check"].test_mode == "PlayMode"
    profiles["script-check"].fail_on.append("warning")
    assert builtins()["script-check"].fail_on == ["error"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", True),
        ("command", "dotnet format"),
        ("stages", ["execute_code"]),
        ("executable", "cmd.exe"),
        ("schema_version", 2),
    ],
)
def test_profiles_reject_unknown_execution_and_format_fields(field, value):
    with pytest.raises(ValidationError):
        VerificationProfile.model_validate({field: value})


def test_permission_union_keeps_test_read_edit_and_run_independent():
    profile = builtins()["playmode-check"]
    assert profile_permissions(profile) == {"read", "run", "edit", "test"}
    profile.refresh_policy = "never"
    assert profile_permissions(profile) == {"read", "run", "test"}
    profile.analysis_required = False
    assert profile_permissions(profile) == {"read", "test"}


def test_optional_impact_without_baseline_does_not_require_graph_availability():
    from services.verification.profiles import profile_operations

    profile = builtins()["script-check"]
    assert "manage_reference_graph" not in {name for name, _ in profile_operations(profile)}
    assert "manage_reference_graph" in {
        name for name, _ in profile_operations(profile, include_impact=True)
    }


def test_profile_override_is_explicit_and_missing_names_are_refused():
    profile = resolve_profile(
        "script-check",
        {"schema_version": 1, "profiles": {"script-check": {"refresh_policy": "never"}}},
    )
    assert profile.refresh_policy == "never"
    with pytest.raises(ValueError, match="profile_not_found"):
        resolve_profile("missing", {"schema_version": 1, "profiles": {}})


def test_write_requires_an_explicit_compare_and_swap_value():
    with pytest.raises(ValidationError):
        parse_verification_options("set_profile", {"profile_id": "local", "profile": {}})
    parsed = parse_verification_options(
        "set_profile", {"profile_id": "local", "profile": {}, "expected_hash": "missing"}
    )
    assert parsed.expected_hash == "missing"
    with pytest.raises(ValidationError):
        parse_verification_options("prepare", {"profile_id": "script-check"})


def test_stage_deadlines_and_test_selectors_are_bounded():
    with pytest.raises(ValidationError):
        VerificationProfile.model_validate({"deadlines": {"tests": 0}})
    with pytest.raises(ValidationError):
        VerificationProfile.model_validate({"test_names": ["A"], "test_mode": None})


def test_verification_public_permissions_do_not_make_status_a_test_grant():
    from services.operation_permissions import Permission, required_permissions

    assert required_permissions("manage_verification", {"action": "status"}) == {Permission.READ}
    assert required_permissions("manage_verification", {"action": "set_profile"}) == {
        Permission.READ,
        Permission.EDIT,
    }
    assert required_permissions(
        "manage_verification", {"action": "prepare", "options": {"capture_baseline": True}}
    ) == {Permission.READ, Permission.RUN}
