"""Bounded Addressables settings inspection, guarded edits and existing build-layout analysis."""

import re
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import StrictInt

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

ALL_ACTIONS = (
    "inspect",
    "preflight",
    "set_address",
    "move_entry",
    "set_profile_value",
    "analyze_layout",
)
_GUID = re.compile(r"[0-9a-f]{32}\Z")


def _path(value: Any, prefixes: tuple[str, ...]) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 512
        and value.startswith(prefixes)
        and "\\" not in value
        and all(p not in ("", ".", "..") for p in value.split("/"))
    )


def _validate(action: str, args: dict[str, Any]) -> str | None:
    if action not in ALL_ACTIONS:
        return "Unknown Addressables action; content builds and remote deployment are not actions."
    allowed = {"action", "page_number", "page_size", "expected_revision"}
    if type(args["page_number"]) is not int or not 1 <= args["page_number"] <= 1000000:
        return "page_number must be a strict integer from1 through1000000."
    if type(args["page_size"]) is not int or not 1 <= args["page_size"] <= 100:
        return "page_size must be a strict integer from1 through100."
    layout = action == "analyze_layout"
    revision = args.get("expected_revision")
    prefix = "addr-layout1:" if layout else "addr1:"
    if revision is not None and (
        not isinstance(revision, str)
        or not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", revision)
    ):
        return "expected_revision has the wrong format or scope."
    write = action.startswith("set_") or action == "move_entry"
    if (write or args["page_number"] > 1) and revision is None:
        return "Writes and continuation pages require expected_revision."
    if layout:
        allowed.add("layout_path")
        if not _path(
            args.get("layout_path"),
            (
                "Library/com.unity.addressables/BuildReports/",
                "Library/com.unity.addressables/buildReports/",
                "Library/MCPForUnity/Addressables/",
            ),
        ) or not args["layout_path"].lower().endswith(".json"):
            return "layout_path must name an existing contained Addressables JSON report."
    else:
        allowed.add("settings_path")
        if not _path(args.get("settings_path"), ("Assets/",)) or not args[
            "settings_path"
        ].lower().endswith(".asset"):
            return "settings_path must name an existing project settings asset."
    if action == "inspect":
        allowed.add("section")
        if args.get("section", "entries") not in ("entries", "groups", "profiles"):
            return "section must be entries, groups or profiles."
    if action in ("set_address", "move_entry"):
        allowed.add("entry_guid")
        if not isinstance(args.get("entry_guid"), str) or not _GUID.fullmatch(args["entry_guid"]):
            return "entry_guid must identify an existing entry."
    if action == "set_address":
        allowed.add("address")
        if (
            not isinstance(args.get("address"), str)
            or not args["address"].strip()
            or len(args["address"]) > 512
            or ("[" in args["address"] and "]" in args["address"])
        ):
            return (
                "address must contain1..512 characters and cannot contain both bracket characters."
            )
    if action == "move_entry":
        allowed.add("group_guid")
        if not isinstance(args.get("group_guid"), str) or not _GUID.fullmatch(args["group_guid"]):
            return "group_guid must identify an existing group."
    if action == "set_profile_value":
        allowed |= {"profile_id", "variable_name", "value"}
        if not isinstance(args.get("profile_id"), str) or not _GUID.fullmatch(args["profile_id"]):
            return "profile_id must identify an existing profile."
        if (
            not isinstance(args.get("variable_name"), str)
            or not 1 <= len(args["variable_name"]) <= 128
        ):
            return "variable_name must contain1..128 characters."
        if not isinstance(args.get("value"), str) or len(args["value"]) > 2048:
            return "value must be a string of at most2048 characters."
    if set(args) - allowed:
        return "A supplied field does not apply to this Addressables action."
    return None


@mcp_for_unity_tool(
    group="core",
    description=(
        "Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), "
        "run bounded preflight for duplicate addresses/missing references/profile issues, or analyze "
        "an existing native build-layout report for duplicated content. Stable Addressables 1.28+ "
        "within 1.x, stable 2.x, stable 3.1+ within 3.x and stable 4.1.1+ within 4.x require "
        "compatible action APIs and stored profile schema 1. Layout reports must match the installed "
        "package version and native schema. Content Directory coverage is unsupported: modern reports "
        "require an empty ContentDirectories array, and nonempty or malformed group ContentDirectoryName "
        "values are refused, including managed-reference data. Reads never create "
        "settings, evaluate arbitrary static profile properties or start content builds. "
        "Guarded set_address/move_entry/set_profile_value preserve existing GUIDs/labels. "
        "Supply explicit settings_path and exact expected_revision for edits/continuation pages. "
        "No group/asset creation, content-build action or remote deployment. "
        "Caps128groups/4096entries/32profiles/64variables,256preflightentries,100rows/256KiB per response. "
        "Native layout files are bounded to16MiB; unsupported versions are explicit."
    ),
    annotations=ToolAnnotations(
        title="Manage Addressables", destructiveHint=True, readOnlyHint=False
    ),
)
async def manage_addressables(
    ctx: Context,
    action: str,
    settings_path: str | None = None,
    section: str | None = None,
    page_number: StrictInt = 1,
    page_size: StrictInt = 50,
    expected_revision: str | None = None,
    entry_guid: str | None = None,
    group_guid: str | None = None,
    address: str | None = None,
    profile_id: str | None = None,
    variable_name: str | None = None,
    value: str | None = None,
    layout_path: str | None = None,
) -> dict[str, Any]:
    args = {
        k: v
        for k, v in {
            "action": action.lower(),
            "settings_path": settings_path,
            "section": section,
            "page_number": page_number,
            "page_size": page_size,
            "expected_revision": expected_revision,
            "entry_guid": entry_guid,
            "group_guid": group_guid,
            "address": address,
            "profile_id": profile_id,
            "variable_name": variable_name,
            "value": value,
            "layout_path": layout_path,
        }.items()
        if v is not None
    }
    error = _validate(args["action"], args)
    if error:
        return {"success": False, "message": error}
    instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, instance, "manage_addressables", args
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
