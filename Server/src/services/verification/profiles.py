"""Code-defined defaults never create a project settings file."""

from models.verification import VerificationProfile


def builtins():
    return {
        "script-check": VerificationProfile(),
        "editmode-check": VerificationProfile(test_mode="EditMode"),
        "playmode-check": VerificationProfile(test_mode="PlayMode"),
    }


def resolve_profile(identity, document):
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(document.get("profiles"), dict)
        or len(document["profiles"]) > 32
    ):
        raise ValueError("invalid_profile_document")
    profiles = builtins()
    profiles.update(
        {
            key: VerificationProfile.model_validate(value)
            for key, value in document["profiles"].items()
        }
    )
    if identity not in profiles:
        raise ValueError("profile_not_found")
    return profiles[identity]


def profile_permissions(profile):
    grants = {"read"}
    if profile.analysis_required:
        grants.add("run")
    if profile.refresh_policy == "if_needed":
        grants.update({"edit", "run"})
    if profile.test_mode:
        grants.add("test")
    return grants


def profile_operations(profile, *, include_impact=False):
    operations = [("get_compile_job", {})]
    if profile.refresh_policy == "if_needed":
        operations.append(("refresh_unity", {"scope": "all", "compile": "request"}))
    if profile.analysis_required:
        operations.append(("manage_script_quality", {"action": "analyze"}))
    if profile.impact == "required" or profile.impact == "if_baseline" and include_impact:
        operations.append(("manage_reference_graph", {"action": "query"}))
    if profile.test_mode:
        operations.extend(
            [("run_tests", {"mode": profile.test_mode}), ("read_console", {"action": "get"})]
        )
    return operations
