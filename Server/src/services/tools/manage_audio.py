"""Play and stop existing scene AudioSources through Unity's native audio tool."""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
)

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.refresh_unity import send_mutation
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class AudioImportPolicy(BaseModel):
    """An explicit patch to one platform's existing sample settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    load_type: Literal["DecompressOnLoad", "CompressedInMemory", "Streaming"] | None = None
    compression_format: (
        Literal["PCM", "Vorbis", "ADPCM", "MP3", "VAG", "HEVAG", "AAC", "GCADPCM", "ATRAC9"] | None
    ) = None
    quality: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None
    sample_rate_setting: (
        Literal["PreserveSampleRate", "OptimizeSampleRate", "OverrideSampleRate"] | None
    ) = None
    sample_rate_override: Annotated[int, Field(strict=True, ge=8000, le=192000)] | None = None
    force_to_mono: StrictBool | None = None
    load_in_background: StrictBool | None = None


def _provided_policy(value: AudioImportPolicy | JsonValue) -> AudioImportPolicy | JsonValue:
    """Omitted settings are allowed; an explicitly supplied JSON null is not a policy."""
    if value is None:
        raise ValueError(
            "Omit settings entirely when clearing an override; supplied null is invalid."
        )
    return value


@mcp_for_unity_tool(
    group="core",
    description=(
        "Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies. "
        "Routing records Undo and dirties the scene; saving the scene is separate. Importer policy saves/reimports and reads native settings back; reimport may limit Undo. "
        "Play or stop an existing scene AudioSource in Play mode. "
        "Play requires an active, enabled source and a usable AudioClip; optionally supply "
        "a clip asset path under Assets/ or Packages/. Stop also supports inactive, disabled "
        "or clipless sources. No component is created. A successful response confirms the "
        "request was issued, not audible output. Omit search_method for automatic ID, path "
        "or name resolution."
    ),
    annotations=ToolAnnotations(title="Manage Audio", destructiveHint=True),
)
async def manage_audio(
    ctx: Context,
    action: Annotated[
        Literal[
            "play",
            "stop",
            "diagnose",
            "get_routing",
            "set_routing",
            "read_import_settings",
            "apply_import_settings",
        ],
        "Audio observation, explicit routing/import policy, or playback action.",
    ],
    target: Annotated[
        StrictStr | StrictInt,
        "Scene GameObject name, hierarchy path or integer instance ID. Importer actions instead require an AudioClip asset path under Assets/.",
    ],
    clip: Annotated[
        str | None,
        "Optional AudioClip asset path for play only; omit to use the source's current clip.",
    ] = None,
    search_method: Annotated[
        Literal["by_id", "by_name", "by_path", "by_id_or_name_or_path"] | None,
        "Target selector. Omitted or null uses automatic ID, path or name resolution.",
    ] = None,
    mixer_path: Annotated[
        StrictStr | None, "get_routing inventory or set_routing existing AudioMixer asset path."
    ] = None,
    group_id: Annotated[
        StrictStr | None,
        "set_routing exact persistent group local file ID from get_routing inventory; never a first-name match.",
    ] = None,
    clear_routing: Annotated[
        bool,
        Field(
            strict=True,
            description="set_routing explicit reset to the default output; excludes mixer_path/group_id.",
        ),
    ] = False,
    platform: Annotated[
        StrictStr | None,
        "Importer platform: Default (omitted) or exact BuildTargetGroup name, e.g. Standalone, Android, iOS. Read values without override are Unity-translated defaults.",
    ] = None,
    settings: Annotated[
        AudioImportPolicy | None,
        BeforeValidator(_provided_policy, json_schema_input_type=AudioImportPolicy),
    ] = None,
    clear_override: Annotated[
        bool,
        Field(
            strict=True,
            description="apply_import_settings removes the selected platform override; excludes settings and Default platform.",
        ),
    ] = False,
) -> dict[str, Any]:
    routing = action in ("get_routing", "set_routing")
    importer = action in ("read_import_settings", "apply_import_settings")
    if (mixer_path is not None or group_id is not None or clear_routing) and not routing:
        return {"success": False, "message": "Routing parameters require a routing action."}
    if (platform is not None or settings is not None or clear_override) and not importer:
        return {"success": False, "message": "Importer parameters require an importer action."}
    if action == "set_routing" and (
        (clear_routing and (mixer_path is not None or group_id is not None))
        or (not clear_routing and (not mixer_path or not group_id))
    ):
        return {
            "success": False,
            "message": "set_routing requires mixer_path/group_id or clear_routing alone.",
        }
    if action == "get_routing" and (group_id is not None or clear_routing):
        return {"success": False, "message": "get_routing accepts only mixer_path inventory."}
    if importer:
        if type(target) is not str or not target.startswith("Assets/"):
            return {
                "success": False,
                "message": "Importer target must be an asset path under Assets/.",
            }
        if clip is not None or search_method is not None:
            return {
                "success": False,
                "message": "Importer actions do not accept clip or search_method.",
            }
        if action == "read_import_settings" and (settings is not None or clear_override):
            return {"success": False, "message": "read_import_settings cannot apply a policy."}
        if action == "apply_import_settings" and (
            (clear_override and (platform in (None, "Default") or settings is not None))
            or (not clear_override and (settings is None or not settings.model_fields_set))
        ):
            return {
                "success": False,
                "message": "Apply requires explicit settings, or clear_override for a selected platform.",
            }
    if action in ("diagnose", "get_routing", "set_routing") and clip is not None:
        return {"success": False, "message": "clip is reserved for play."}
    unity_instance = await get_unity_instance_from_context(ctx)
    params = {"action": action, "target": target}
    if clip is not None:
        params["clip"] = clip
    if search_method is not None:
        params["searchMethod"] = search_method

    if mixer_path is not None:
        params["mixer_path"] = mixer_path
    if group_id is not None:
        params["group_id"] = group_id
    if clear_routing:
        params["clear_routing"] = True
    if platform is not None:
        params["platform"] = platform
    if settings is not None:
        params["settings"] = settings.model_dump(exclude_unset=True)
    if clear_override:
        params["clear_override"] = True

    if action in ("set_routing", "apply_import_settings"):
        result = await send_mutation(ctx, unity_instance, "manage_audio", params)
    else:
        result = await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, "manage_audio", params
        )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
