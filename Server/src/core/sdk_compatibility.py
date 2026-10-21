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
                receipts is not None,
                receipts is not None and receipts.store is not None and not receipts.closed,
            ),
            "wait_tasks": feature(
                configured_tasks,
                tasks is not None,
                tasks is not None and not tasks._closed,
            ),
        },
    }
