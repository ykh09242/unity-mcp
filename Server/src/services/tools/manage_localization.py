"""Bounded Localization table diagnostics and explicit existing-key corrections."""

import re
from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, JsonValue, StrictBool, StrictStr

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.refresh_unity import send_mutation
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

AssetPath = Annotated[StrictStr, Field(min_length=8, max_length=512)]
PathList = Annotated[list[AssetPath], Field(min_length=1, max_length=16)]


def _asset_path(value: str) -> bool:
    return (
        value.startswith("Assets/")
        and "\\" not in value
        and not any(part in ("", ".", "..") for part in value.split("/"))
        and not any(ord(char) < 32 or char == ":" for char in value)
    )


@mcp_for_unity_tool(
    group="core",
    description=(
        "Inspect optional Unity Localization locale/table identities, shared keys, missing translations, "
        "static Smart String selectors and asset GUID references. No runtime formatting, loading or locale activation. "
        "inspect requires 1..16 explicit table_paths; argument_names is the caller's available root-selector inventory, "
        "not proof of custom formatter or runtime variable behavior. list_locales/list_tables use optional exact paths "
        "or bounded Assets discovery. set_string_entry corrects an existing shared key ID in one table and preserves "
        "is_smart when omitted. set_fallbacks replaces only explicit locale fallback metadata, refusing cycles. "
        "Writes require expected_revision from inspection and save the exact asset with native Undo. Revision includes "
        "loaded serialization and source bytes; external process races are not excluded. set_asset_entry explicitly "
        "refuses: supported package authoring also changes Addressables groups/labels/shared metadata. "
        "No asset/table/locale creation, key deletion or implicit Addressables changes."
    ),
    annotations=ToolAnnotations(title="Manage Localization", destructiveHint=True),
)
async def manage_localization(
    ctx: Context,
    action: Annotated[
        Literal[
            "capabilities",
            "list_locales",
            "list_tables",
            "inspect",
            "set_string_entry",
            "set_asset_entry",
            "set_fallbacks",
        ],
        "Explicit read or correction action.",
    ],
    paths: Annotated[PathList | None, "Optional exact asset paths for discovery."] = None,
    table_paths: Annotated[PathList | None, "Explicit tables to inspect, at most 16."] = None,
    table_path: Annotated[AssetPath | None, "Exact table asset for correction."] = None,
    locale_path: Annotated[AssetPath | None, "Exact Locale asset for fallback correction."] = None,
    key_id: Annotated[
        StrictStr | None, "Existing positive Int64 shared key ID as a decimal string."
    ] = None,
    value: Annotated[
        StrictStr | None,
        Field(max_length=8192),
        "New string value, including an explicit empty value.",
    ] = None,
    is_smart: Annotated[StrictBool | None, "Explicit Smart String flag; omit to preserve."] = None,
    asset_path: Annotated[
        AssetPath | None, "Asset correction is refused because it requires Addressables authoring."
    ] = None,
    argument_names: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=128)]] | None,
        Field(max_length=128),
        "Available root selectors for static smart diagnostics.",
    ] = None,
    fallback_locale_paths: Annotated[
        list[AssetPath] | None,
        Field(max_length=8),
        "Ordered fallbacks; explicit empty list clears fallback metadata only.",
    ] = None,
    expected_revision: Annotated[
        StrictStr | None,
        Field(pattern="^[0-9a-f]{64}$"),
        "Exact current asset revision from inspection.",
    ] = None,
    offset: Annotated[int | None, Field(strict=True, ge=0, le=4096), "Page offset."] = None,
    limit: Annotated[
        int | None, Field(strict=True, ge=1, le=100), "Discovery page size, default 50."
    ] = None,
) -> dict[str, JsonValue]:
    """Validate corrections before dispatching to the selected Editor."""
    fields = {
        "paths": paths,
        "table_paths": table_paths,
        "table_path": table_path,
        "locale_path": locale_path,
        "key_id": key_id,
        "value": value,
        "is_smart": is_smart,
        "asset_path": asset_path,
        "argument_names": argument_names,
        "fallback_locale_paths": fallback_locale_paths,
        "expected_revision": expected_revision,
        "offset": offset,
        "limit": limit,
    }
    allowed = {
        "capabilities": set(),
        "list_locales": {"paths", "offset", "limit"},
        "list_tables": {"paths", "offset", "limit"},
        "inspect": {"table_paths", "argument_names"},
        "set_string_entry": {"table_path", "key_id", "value", "is_smart", "expected_revision"},
        "set_asset_entry": {"table_path", "key_id", "asset_path", "expected_revision"},
        "set_fallbacks": {"locale_path", "fallback_locale_paths", "expected_revision"},
    }

    def failure(message: str) -> dict[str, JsonValue]:
        return {"success": False, "message": message}

    if action not in allowed or any(
        v is not None and k not in allowed[action] for k, v in fields.items()
    ):
        return failure("Unsupported action or fields for action.")
    for name in ("paths", "table_paths", "fallback_locale_paths"):
        items = fields[name]
        if items is not None and (
            len(set(items)) != len(items) or any(not _asset_path(p) for p in items)
        ):
            return failure("Asset paths must be unique canonical paths under Assets/.")
    for name in ("table_path", "locale_path", "asset_path"):
        if fields[name] is not None and not _asset_path(fields[name]):
            return failure("Asset paths must be canonical paths under Assets/.")
    if action == "inspect" and not table_paths:
        return failure("inspect requires explicit table_paths.")
    if action.startswith("set_") and not expected_revision:
        return failure("Corrections require expected_revision from inspection.")
    if action in ("set_string_entry", "set_asset_entry"):
        if (
            not table_path
            or not key_id
            or not re.fullmatch(r"[1-9][0-9]{0,18}", key_id)
            or int(key_id) > 9223372036854775807
        ):
            return failure("Correction requires table_path and a positive Int64 key_id string.")
        if action == "set_string_entry" and value is None:
            return failure("set_string_entry requires value.")
        if action == "set_asset_entry" and asset_path is None:
            return failure(
                "set_asset_entry requires asset_path; native authoring is explicitly unsupported."
            )
    if action == "set_fallbacks" and (not locale_path or fallback_locale_paths is None):
        return failure("set_fallbacks requires locale_path and explicit fallback_locale_paths.")
    params: dict[str, JsonValue] = {"action": action}
    params.update({k: v for k, v in fields.items() if v is not None})
    selected = await get_unity_instance_from_context(ctx)
    if action.startswith("set_"):
        result = await send_mutation(ctx, selected, "manage_localization", params)
    else:
        result = await send_with_unity_instance(
            async_send_command_with_retry, selected, "manage_localization", params
        )
    return result if isinstance(result, dict) else failure(str(result))
