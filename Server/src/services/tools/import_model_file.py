"""
Defines the import_model_file tool: import a local 3D model file (already within Assets,
e.g. exported from Blender) into the Unity project.

Thin pass-through: NO API keys and NO file bytes cross the bridge. The C# side copies
the file under Assets/ and runs the shared model-import pipeline. Success requires an
imported hierarchy with indexed mesh geometry. Failed imports can retain staged files
and return their path, GUID, import stage and error code; the original source is preserved.
"""

from typing import Annotated, Any, Literal
from pathlib import PurePosixPath, PureWindowsPath

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    group="asset_gen",
    description=(
        "Import a local 3D model file that already exists within the Unity project's Assets folder (e.g. an FBX/OBJ/glTF "
        "exported from Blender or another DCC tool) into the Unity project. The file is copied "
        "under Assets/ and run through Unity's model-import pipeline (scale-normalize, material "
        "settings; glTF needs an active compatible importer such as glTFast). Carries no API keys "
        "and no file bytes over the bridge.\n\n"
        "Params: source_path (Assets-relative path, or an absolute path physically within this Unity project's Assets, "
        "to a .fbx/.obj/.glb/.gltf/.zip; traversal and symbolic links/junctions are rejected), "
        "name, output_folder (under Assets/), target_size, animation_type. "
        "Success returns { asset_path, asset_guid, model_import_stage: 'usable' } after Unity loads "
        "a hierarchy containing indexed mesh geometry. This does not certify materials, rigs or rendering. "
        "Failed imports may retain staged bytes and return asset_path, asset_guid, model_import_stage "
        "('saved' for bytes on disk or 'registered' for a GUID without usable geometry), and "
        "model_import_error_code. The original source is preserved. A missing active glTF importer "
        "is 'missing_gltf_importer'; an active importer that produces no usable model is 'unusable_model'. "
        "Inspect the retained output and Console, repair or re-export, then reimport.\n\n"
        "animation_type (FBX/OBJ only): pass 'generic' or 'humanoid' for a rigged/animated mesh so "
        "Unity surfaces its AnimationClips; omitted or 'none' imports no rig (this is the usual "
        "cause of a rigged FBX importing with zero clips); 'legacy' selects Unity's legacy Animation "
        "system (rarely needed). glTF/GLB ignore it — the selected glTF importer handles animation.\n\n"
        "For multi-file exports (a text .gltf with an external .bin, or an .obj with a sibling "
        ".mtl/textures), zip them and pass the .zip — a bare .gltf/.obj is copied without its sidecars."
    ),
    annotations=ToolAnnotations(
        title="Import Model File",
        destructiveHint=False,
    ),
)
async def import_model_file(
    ctx: Context,
    source_path: Annotated[
        str,
        "Assets-relative or absolute-within-Assets model file path (.fbx/.obj/.glb/.gltf/.zip).",
    ],
    name: Annotated[str, "Base name for the imported asset."] | None = None,
    output_folder: Annotated[
        str, "Destination under Assets/. Failed imports may retain staged output here for repair."
    ]
    | None = None,
    target_size: Annotated[float, "Normalize the largest dimension to this size (meters)."]
    | None = None,
    animation_type: Annotated[
        Literal["none", "generic", "humanoid", "legacy"],
        "FBX/OBJ only: rig/animation import mode. 'generic' or 'humanoid' surface the model's "
        "AnimationClips; 'legacy' selects Unity's legacy Animation system (rarely needed); "
        "omitted or 'none' imports no rig. Ignored for glTF/GLB.",
    ]
    | None = None,
) -> dict[str, Any]:
    source = source_path.replace("\\", "/")
    windows_path = PureWindowsPath(source)
    if (
        not source.strip()
        or "\x00" in source
        or ".." in source.split("/")
        or (windows_path.drive and not windows_path.is_absolute())
        or "://" in source
        or (
            ":" in source and not (len(source) >= 3 and source[0].isalpha() and source[1:3] == ":/")
        )
        or not (
            source.startswith("Assets/")
            or PurePosixPath(source).is_absolute()
            or windows_path.is_absolute()
        )
    ):
        return {
            "success": False,
            "error": "'source_path' must be Assets-relative or an absolute path within Unity's Assets folder, without traversal or links.",
        }

    unity_instance = await get_unity_instance_from_context(ctx)

    params_dict = {
        "sourcePath": source_path,
        "name": name,
        "outputFolder": output_folder,
        "targetSize": target_size,
        "animationType": animation_type,
    }
    params_dict = {k: v for k, v in params_dict.items() if v is not None}

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "import_model_file",
        params_dict,
    )

    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
