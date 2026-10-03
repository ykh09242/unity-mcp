"""
Defines the import_model_file tool: import a local 3D model file (already within Assets,
e.g. exported from Blender) into the Unity project.

Thin pass-through: NO API keys and NO file bytes cross the bridge. The C# side copies
the file under Assets/ and runs the shared model-import pipeline.
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
        "settings; glTF requires glTFast). Carries no API keys and no file bytes over the bridge.\n\n"
        "Params: source_path (Assets-relative path, or an absolute path physically within this Unity project's Assets, "
        "to a .fbx/.obj/.glb/.gltf/.zip; traversal and symbolic links/junctions are rejected), "
        "name, output_folder (under Assets/), target_size, animation_type. "
        "Returns { asset_path, asset_guid }.\n\n"
        "animation_type (FBX/OBJ only): pass 'generic' or 'humanoid' for a rigged/animated mesh so "
        "Unity surfaces its AnimationClips; omitted or 'none' imports no rig (this is the usual "
        "cause of a rigged FBX importing with zero clips); 'legacy' selects Unity's legacy Animation "
        "system (rarely needed). glTF/GLB ignore it — glTFast imports animation itself.\n\n"
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
    source_path: Annotated[str, "Assets-relative or absolute-within-Assets model file path (.fbx/.obj/.glb/.gltf/.zip)."],
    name: Annotated[str, "Base name for the imported asset."] | None = None,
    output_folder: Annotated[str, "Destination folder under Assets/ for the import."] | None = None,
    target_size: Annotated[float, "Normalize the largest dimension to this size (meters)."] | None = None,
    animation_type: Annotated[
        Literal["none", "generic", "humanoid", "legacy"],
        "FBX/OBJ only: rig/animation import mode. 'generic' or 'humanoid' surface the model's "
        "AnimationClips; 'legacy' selects Unity's legacy Animation system (rarely needed); "
        "omitted or 'none' imports no rig. Ignored for glTF/GLB.",
    ] | None = None,
) -> dict[str, Any]:
    source = source_path.replace("\\", "/")
    windows_path = PureWindowsPath(source)
    if (
        not source.strip()
        or "\x00" in source
        or ".." in source.split("/")
        or (windows_path.drive and not windows_path.is_absolute())
        or "://" in source
        or (":" in source and not (len(source) >= 3 and source[0].isalpha() and source[1:3] == ":/"))
        or not (source.startswith("Assets/") or PurePosixPath(source).is_absolute() or windows_path.is_absolute())
    ):
        return {"success": False, "error": "'source_path' must be Assets-relative or an absolute path within Unity's Assets folder, without traversal or links."}

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
