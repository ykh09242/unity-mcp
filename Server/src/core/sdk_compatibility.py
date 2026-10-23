"""One reviewed SDK set for installation diagnostics and optional adapter gates."""

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
import json
import re
from types import MappingProxyType

_manifest = json.loads(files("core").joinpath("sdk_compatibility.json").read_text(encoding="utf-8"))
REVIEWED_SDK = MappingProxyType(_manifest["packages"])
_VERSION = re.compile(r"[0-9][0-9A-Za-z+_.-]{0,79}\Z")


def sdk_versions(version_reader=None) -> dict[str, str | None]:
    reader = version if version_reader is None else version_reader
    resolved = {}
    for name in REVIEWED_SDK:
        try:
            value = reader(name)
        except PackageNotFoundError:
            value = None
        resolved[name] = value if type(value) is str and _VERSION.fullmatch(value) else None
    return resolved


def is_reviewed_sdk(version_reader=None) -> bool:
    return sdk_versions(version_reader) == REVIEWED_SDK


def runtime_sdk_report(server, *, version_reader=None) -> dict:
    """Read bounded local metadata only; no paths, credentials, Unity probes or activation."""
    resolved = sdk_versions(version_reader)
    supported = resolved == REVIEWED_SDK
    receipts = getattr(server, "_execution_receipts", None)
    tasks = getattr(server, "_test_job_tasks", None)
    configured_receipts = receipts is not None and receipts.root is not None
    configured_tasks = bool(getattr(server, "_test_wait_tasks_requested", False))

    def feature(configured, installed, active):
        if not configured:
            state = "disabled"
        elif not supported:
            state = "sdk_unsupported"
        elif not installed:
            state = "unavailable"
        else:
            state = "active" if active else "inactive"
        return {"configured": configured, "state": state}

    def optional_feature(
        configured, installed, active, *, unavailable_reason, unavailable_action, enable_action
    ):
        result = feature(configured, installed, active)
        reasons = {
            "disabled": ("not_configured", enable_action),
            "sdk_unsupported": ("reviewed_sdk_required", "install_reviewed_sdk"),
            "unavailable": (unavailable_reason, unavailable_action),
            "inactive": ("closed", "restart_server"),
            "active": ("installed", "none"),
        }
        reason, action = reasons[result["state"]]
        return {**result, "installed": installed, "reason": reason, "next_action": action}

    skills = getattr(server, "_packaged_skills", None)
    skills_installed = (
        skills is not None
        and getattr(server, "_extensions", {}).get("io.modelcontextprotocol/skills") is skills
    )
    scene_inputs = getattr(server, "_scene_load_inputs", None)
    scene_installed = scene_inputs is not None and any(
        middleware is scene_inputs for middleware in getattr(server, "middleware", ())
    )
    scene_http = getattr(server, "_scene_load_inputs_transport", None) == "http"
    scene_reason = (
        "transport_unsupported"
        if not scene_http
        else ("middleware_missing" if scene_inputs is not None else "adapter_unavailable")
    )
    scene_action = "check_scene_input_installation" if scene_http else "use_http_transport"

    return {
        "schema_version": "unity-mcp/runtime@1",
        "sdk": {
            "reviewed": dict(REVIEWED_SDK),
            "resolved": resolved,
            "supported": supported,
            "next_action": "none" if supported else "install_reviewed_sdk",
        },
        "features": {
            "execution_receipts": feature(
                configured_receipts,
                receipts is not None and (receipts.store is None or receipts.store.available),
                receipts is not None and receipts.store is not None and not receipts.closed,
            ),
            "wait_tasks": feature(
                configured_tasks,
                tasks is not None,
                tasks is not None and not tasks._closed,
            ),
            "skills": optional_feature(
                bool(getattr(server, "_skills_requested", False)),
                skills_installed,
                skills_installed,
                unavailable_reason="extension_unavailable",
                unavailable_action="repair_packaged_skill_bundle",
                enable_action="use_packaged_server",
            ),
            "scene_load_inputs": optional_feature(
                bool(getattr(server, "_scene_load_inputs_requested", False)),
                scene_installed,
                scene_installed and not scene_inputs.ledger._closed,
                unavailable_reason=scene_reason,
                unavailable_action=scene_action,
                enable_action="enable_scene_load_inputs",
            ),
        },
    }
