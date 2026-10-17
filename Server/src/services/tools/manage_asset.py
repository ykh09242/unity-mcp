"""
Defines the manage_asset tool for interacting with Unity assets.
"""

import asyncio
import json
import logging
import re
import unicodedata
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import parse_json_payload, coerce_int, normalize_properties
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from services.tools.preflight import preflight
from services.tools.pagination import validate_page, page_integer

logger = logging.getLogger("mcp-for-unity-server")


class AssetLabelTarget(BaseModel):
    """One closed label target; mutation revisions are checked by action admission."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: StrictStr
    revision: StrictStr | None = None


def asset_label_params(
    action: str,
    path: str | None = None,
    labels: list[str] | None = None,
    label_targets: list[AssetLabelTarget | dict[str, Any]] | None = None,
    label_revision: str | None = None,
) -> dict[str, Any]:
    """Admit label inputs before context, preflight, confirmation or transport I/O.

    Shared with the CLI so its JSON options have the same intrinsic limits. Native
    resolution, editability and revision observations remain authoritative.
    """
    mutation = action != "get_labels"
    if action not in {"get_labels", "set_labels", "add_labels", "remove_labels"}:
        raise ValueError("Unknown label action.")
    params = {"action": action}
    if label_targets is not None:
        if path is not None or label_revision is not None:
            raise ValueError("Use either path/revision or label_targets.")
        if type(label_targets) is not list or not 1 <= len(label_targets) <= 32:
            raise ValueError("label_targets must contain 1-32 closed target rows.")
        try:
            rows = [AssetLabelTarget.model_validate(row) for row in label_targets]
        except ValidationError as exc:
            raise ValueError(
                "Each target requires a string path and only an optional revision."
            ) from exc
        if not mutation and any("revision" in row.model_fields_set for row in rows):
            raise ValueError("get_labels does not accept revisions.")
        params["labelTargets"] = [row.model_dump(exclude_unset=True) for row in rows]
    else:
        if type(path) is not str or (
            label_revision is not None and type(label_revision) is not str
        ):
            raise ValueError("A single target requires a string path and optional string revision.")
        rows = [AssetLabelTarget(path=path, revision=label_revision)]
        params["path"] = path
        if mutation:
            params["labelRevision"] = label_revision
        elif label_revision is not None:
            raise ValueError("get_labels does not accept a revision.")
    paths = [row.path for row in rows]
    if any(
        not value or value != value.strip() or any(unicodedata.category(c) == "Cc" for c in value)
        for value in paths
    ):
        raise ValueError(
            "Target paths must be nonempty strings without edge whitespace or controls."
        )
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate target paths are not allowed.")
    try:
        if sum(len(value.encode("utf-8")) for value in paths) > 32768:
            raise ValueError("Target paths exceed 32 KiB.")
        if mutation:
            if any(
                row.revision is None
                or re.fullmatch(r"mcp-al-v1_[0-9a-f]{32}_[0-9a-f]{64}_[0-9a-f]{64}", row.revision)
                is None
                for row in rows
            ):
                raise ValueError("Each mutation requires an observed label revision.")
            if (
                type(labels) is not list
                or len(labels) > 128
                or any(type(value) is not str for value in labels)
            ):
                raise ValueError("labels must be an array of at most 128 strings.")
            if any(
                not value
                or value != value.strip()
                or any(unicodedata.category(c) == "Cc" for c in value)
                or len(value.encode("utf-16-le")) // 2 > 256
                for value in labels
            ):
                raise ValueError(
                    "Labels require 1-256 UTF-16 units, no controls or edge whitespace."
                )
            if sum(len(value.encode("utf-8")) for value in labels) > 16384:
                raise ValueError("Requested labels exceed 16 KiB.")
            params["labels"] = labels
        elif labels is not None:
            raise ValueError("get_labels does not accept labels.")
    except UnicodeError as exc:
        raise ValueError("Paths and labels must be valid Unicode.") from exc
    return params


@mcp_for_unity_tool(
    description=(
        "Performs asset operations (import, create, modify, delete, etc.) in Unity. "
        "Read-only list_asset_bundles/get_bundle_assets/get_bundle_dependencies inspect AssetDatabase "
        "assignments, not built bundle files; use bundle_name and pages of at most 100.\n\n"
        'Tip (payload safety): for `action="search"`, prefer paging (`page_size`, `page_number`) and keep '
        "`generate_preview=false` (previews can add large base64 blobs). "
        "Opt-in metadata_only search avoids explicit object/preview loads and returns a short-lived "
        "search_snapshot for reusable pages; keep query, scope, date and page_size unchanged."
        " Label actions get/set/add/remove support 1-32 targets; mutations require observed revisions, "
        "are sequential rather than atomic, and never automatically retry an uncertain write."
    ),
    annotations=ToolAnnotations(
        title="Manage Asset",
        destructiveHint=True,
    ),
)
async def manage_asset(
    ctx: Context,
    action: Annotated[
        Literal[
            "import",
            "create",
            "modify",
            "delete",
            "duplicate",
            "move",
            "rename",
            "search",
            "get_info",
            "create_folder",
            "get_components",
            "list_asset_bundles",
            "get_bundle_assets",
            "get_bundle_dependencies",
            "get_labels",
            "set_labels",
            "add_labels",
            "remove_labels",
        ],
        "Perform CRUD operations on assets.",
    ],
    path: Annotated[
        str,
        "Asset path required for asset operations; search defaults to Assets. "
        "Ignored for bundle assignment inspection.",
    ]
    | None = None,
    asset_type: Annotated[
        str,
        "Asset type (e.g., 'Material', 'Folder') - required for 'create'. Note: For ScriptableObjects, use manage_scriptable_object.",
    ]
    | None = None,
    properties: Annotated[
        dict[str, Any] | str,
        "Dictionary of properties for 'create'/'modify'. Keys are property names, values are property values.",
    ]
    | None = None,
    destination: Annotated[str, "Target path for 'duplicate'/'move'."] | None = None,
    generate_preview: Annotated[
        bool,
        "Generate previews up to 256 pixels per edge and 256 KiB PNG each; "
        "search allows at most 32 results and 4 MiB aggregate base64.",
    ] = False,
    search_pattern: Annotated[
        str,
        "Search pattern (e.g., '*.prefab' or AssetDatabase filters like 't:MonoScript'). "
        "Recommended: put queries like 't:MonoScript' here and set path='Assets'.",
    ]
    | None = None,
    filter_type: Annotated[str, "Filter type for search"] | None = None,
    filter_date_after: Annotated[str, "Date after which to filter"] | None = None,
    page_size: Annotated[
        int | str,
        "Page size: search 1-1000 (default 50), previews 1-32 (default 32), "
        "bundle inspection 1-100 (default 50).",
    ]
    | None = None,
    page_number: Annotated[int | str, "Page number for pagination (1-based)."] | None = None,
    bundle_name: Annotated[str, "Registered bundle assignment name for get_bundle_* actions."]
    | None = None,
    recursive: Annotated[bool, "Include indirect bundle dependencies (default false)."] = False,
    metadata_only: Annotated[
        bool,
        Field(strict=True),
        "Search only: return metadata with instanceID=0 and a bounded 60-second Editor-session snapshot; "
        "no explicit object/preview loads. Default false preserves legacy behavior.",
    ] = False,
    search_snapshot: Annotated[
        str,
        "Search only: opaque token from metadata_only=true; repeat the effective query and page_size.",
    ]
    | None = None,
    labels: Annotated[
        list[StrictStr], Field(max_length=128), "Exact labels; set replaces, empty set clears."
    ]
    | None = None,
    label_targets: Annotated[
        list[AssetLabelTarget],
        Field(min_length=1, max_length=32),
        "Common-operation batch; each mutation row requires revision.",
    ]
    | None = None,
    label_revision: Annotated[
        StrictStr, "Opaque revision from get_labels for a single-target mutation."
    ]
    | None = None,
) -> dict[str, Any]:
    action_l = (action or "").lower()
    label_action = action_l in {"get_labels", "set_labels", "add_labels", "remove_labels"}
    if label_action:
        try:
            if (
                any(
                    value is not None
                    for value in (
                        asset_type,
                        properties,
                        destination,
                        search_pattern,
                        filter_type,
                        filter_date_after,
                        page_size,
                        page_number,
                        bundle_name,
                        search_snapshot,
                    )
                )
                or generate_preview is not False
                or recursive is not False
                or metadata_only is not False
            ):
                raise ValueError("Label actions do not accept other asset options.")
            params = asset_label_params(action_l, path, labels, label_targets, label_revision)
        except (ValueError, TypeError) as exc:
            return {"success": False, "code": "invalid_asset_labels", "message": str(exc)[:512]}
        instance = await get_unity_instance_from_context(ctx)
        gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=False)
        if gate is not None:
            return gate.model_dump()
        result = await send_with_unity_instance(
            async_send_command_with_retry,
            instance,
            "manage_asset",
            params,
            loop=asyncio.get_running_loop(),
            retry_on_reload=action_l == "get_labels",
        )
        return result if isinstance(result, dict) else {"success": False, "message": str(result)}
    if any(value is not None for value in (labels, label_targets, label_revision)):
        return {
            "success": False,
            "code": "invalid_asset_labels",
            "message": "Label options require a label action.",
        }
    if type(metadata_only) is not bool:
        return {
            "success": False,
            "code": "invalid_metadata_search",
            "message": "metadata_only must be a boolean.",
        }
    if (metadata_only or search_snapshot is not None) and action_l != "search":
        return {
            "success": False,
            "code": "invalid_metadata_search",
            "message": "Snapshot options require action='search'.",
        }
    if search_snapshot is not None and (
        not metadata_only
        or not isinstance(search_snapshot, str)
        or len(search_snapshot) != 75
        or re.fullmatch(r"mcp-as-v1_[0-9a-f]{32}_[0-9a-f]{32}", search_snapshot) is None
    ):
        return {
            "success": False,
            "code": "invalid_search_snapshot",
            "message": "A valid search_snapshot requires metadata_only=true.",
        }
    if metadata_only and generate_preview:
        return {
            "success": False,
            "code": "invalid_metadata_search",
            "message": "Metadata search does not generate previews.",
        }
    if action_l not in {
        "import",
        "create",
        "modify",
        "delete",
        "duplicate",
        "move",
        "rename",
        "search",
        "get_info",
        "create_folder",
        "get_components",
        "list_asset_bundles",
        "get_bundle_assets",
        "get_bundle_dependencies",
    }:
        return {"success": False, "message": f"Unknown asset action: '{action}'."}
    bundle_inspection = action_l in {
        "list_asset_bundles",
        "get_bundle_assets",
        "get_bundle_dependencies",
    }
    if action_l != "search" and not bundle_inspection and not path:
        return {"success": False, "message": f"Action '{action}' requires parameter 'path'."}
    if action_l == "search" and path is None:
        path = "Assets"
    if action_l == "create" and not asset_type:
        return {"success": False, "message": "Action 'create' requires parameter 'asset_type'."}
    if action_l == "create" and asset_type.lower() not in {"folder", "material", "physicsmaterial"}:
        return {
            "success": False,
            "message": f"Creating asset type '{asset_type}' is not supported. Supported: Folder, Material, PhysicsMaterial.",
        }
    if action_l in {"move", "rename"} and not destination:
        return {"success": False, "message": f"Action '{action}' requires parameter 'destination'."}
    if action_l in {"get_bundle_assets", "get_bundle_dependencies"} and (
        not bundle_name or not bundle_name.strip() or len(bundle_name) > 512
    ):
        return {
            "success": False,
            "message": "A registered bundle_name of 1-512 characters is required.",
        }

    try:
        page_size = coerce_int(page_size)
    except ValueError as exc:
        return {"success": False, "message": f"Invalid 'page_size': {exc}"}
    try:
        page_number = coerce_int(page_number)
    except ValueError as exc:
        return {"success": False, "message": f"Invalid 'page_number': {exc}"}

    if bundle_inspection:
        try:
            page_size = page_integer(page_size, 50, 1, 100, "page_size")
            page_number = page_integer(page_number, 1, 1, 2_147_483_647, "page_number")
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
    elif action_l == "search":
        try:
            page_size, page_number = validate_page(page_size, page_number, preview=generate_preview)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    # --- Normalize properties using robust module-level helper ---
    properties, parse_error = normalize_properties(properties)
    if parse_error:
        logger.error("manage_asset: invalid properties")
        return {"success": False, "message": parse_error}
    if action_l == "modify" and properties is None:
        return {"success": False, "message": "Action 'modify' requires parameter 'properties'."}

    # --- Payload-safe normalization for common LLM mistakes (search) ---
    # Unity's C# handler treats `path` as a folder scope. If a model mistakenly puts a query like
    # "t:MonoScript" into `path`, Unity will consider it an invalid folder and fall back to searching
    # the entire project, which is token-heavy. Normalize such cases into search_pattern + Assets scope.
    if action_l == "search":
        try:
            raw_path = (path or "").strip()
        except (AttributeError, TypeError):
            # Handle case where path is not a string despite type annotation
            raw_path = ""

        # If the caller put an AssetDatabase query into `path`, treat it as `search_pattern`.
        if (not search_pattern) and raw_path.startswith("t:"):
            search_pattern = raw_path
            path = "Assets"
            logger.info(
                "manage_asset(search): normalized query from `path` into `search_pattern` and set path='Assets'"
            )

        # If the caller used `asset_type` to mean a search filter, map it to filter_type.
        # (In Unity, filterType becomes `t:<filterType>`.)
        if (not filter_type) and asset_type and isinstance(asset_type, str):
            filter_type = asset_type
            logger.info(
                "manage_asset(search): mapped `asset_type` into `filter_type` for safer server-side filtering"
            )

    # Prepare parameters for the C# handler
    params_dict = {
        "action": action.lower(),
        "path": path,
        "assetType": asset_type,
        "properties": properties,
        "destination": destination,
        "generatePreview": generate_preview,
        "searchPattern": search_pattern,
        "filterType": filter_type,
        "filterDateAfter": filter_date_after,
        "pageSize": page_size,
        "pageNumber": page_number,
    }

    # Remove None values to avoid sending unnecessary nulls
    params_dict = {k: v for k, v in params_dict.items() if v is not None}
    if metadata_only:
        params_dict["action"] = "search_metadata_v1"
        if search_snapshot is not None:
            params_dict["searchSnapshot"] = search_snapshot
    if bundle_inspection:
        if bundle_name is not None:
            params_dict["bundleName"] = bundle_name
        if action_l == "get_bundle_dependencies":
            params_dict["recursive"] = recursive

    unity_instance = await get_unity_instance_from_context(ctx)
    if not bundle_inspection:
        gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=True)
        if gate is not None:
            return gate.model_dump()

    # Get the current asyncio event loop
    loop = asyncio.get_running_loop()

    # Use centralized async retry helper with instance routing
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_asset", params_dict, loop=loop
    )
    # Return the result obtained from Unity
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
