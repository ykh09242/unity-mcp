"""
Defines the import_model_file tool: import a local 3D model file (already within Assets,
e.g. exported from Blender) into the Unity project.

Thin pass-through: NO API keys and NO file bytes cross the bridge. The C# side copies
the model and supported local dependencies under Assets/ and runs the shared model-import pipeline. Success requires an
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


def _normalize_archive_model_path(value: str | None) -> str | None:
    """Cheap syntax check; C# validates the actual bounded archive before extraction."""
    if value is None:
        return None
    message = (
        "archive_model_path must be an exact relative .fbx/.obj/.glb/.gltf entry path "
        "without traversal, URLs or wildcards (at most 512 UTF-8 bytes and 32 segments)."
    )
    if not isinstance(value, str):
        raise ValueError(message)
    path = value.replace("\\", "/")
    segments = path.split("/")
    try:
        invalid = (
            not path.strip()
            or any(c in ':*?"<>|' or ord(c) < 32 or 127 <= ord(c) <= 159 for c in path)
            or any(part in ("", ".", "..") for part in segments)
            or PurePosixPath(path).suffix.lower() not in {".fbx", ".obj", ".glb", ".gltf"}
            or len(path.encode("utf-8")) > 512
            or len(segments) > 32
        )
    except UnicodeEncodeError as error:
        raise ValueError(message) from error
    if invalid:
        raise ValueError(message)
    return path


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
        "name, output_folder (under Assets/), target_size, animation_type, archive_model_path (ZIP only). "
        "Success returns { asset_path, asset_guid, model_import_stage: 'usable' } after Unity loads "
        "a hierarchy containing indexed mesh geometry. This does not certify materials, rigs or rendering. "
        "Failed imports may retain staged bytes and return asset_path, asset_guid, model_import_stage "
        "('saved' for bytes on disk or 'registered' for a GUID without usable geometry), and "
        "model_import_error_code. The original source is preserved. "
        "target_size applies only when Auto-Normalize Imported Models is enabled; finite zero or negative values preserve imported scale. Successful normalization of a model without ModelImporter may return a prefab variant while retaining its source model. "
        "A missing active glTF importer "
        "is 'missing_gltf_importer'; an active importer that produces no usable model is 'unusable_model'. "
        "Inspect the retained output and Console, repair or re-export, then reimport.\n\n"
        "animation_type (FBX/OBJ only): pass 'generic' or 'humanoid' for a rigged/animated mesh so "
        "Unity surfaces its AnimationClips; omitted or 'none' imports no rig (this is the usual "
        "cause of a rigged FBX importing with zero clips); 'legacy' selects Unity's legacy Animation "
        "system (rarely needed). glTF/GLB ignore it — the selected glTF importer handles animation.\n\n"
        "Core text glTF buffer/image sidecars and OBJ same-directory MTL files with simple PNG/JPEG "
        "texture maps are copied together into a fresh bundle folder, preserving relative paths and "
        "the original model basename. name labels that folder; a single-file import keeps its flat "
        "asset name. Dependencies must stay below the source directory without parent traversal, "
        "links, absolute/network paths or executable files. Limits include 256 files and 512 MiB "
        "combined file/embedded-data bytes. Unsupported text glTF extensions and MTL options or "
        "ambiguous filenames fail before staging; no reference is silently dropped.\n\n"
        "dependency_bundle reports inspection, stage ('not_staged', 'partial', 'staged'), paths and "
        "copy counts. Partial output can remain after a write failure; staged does not mean usable. "
        "GLB core external resources are rejected: export core text glTF with its files or a "
        "self-contained GLB. Embedded GLB extensions are preserved with explicitly limited inspection. "
        "FBX dependencies are not scanned. For ZIP, archive_model_path selects an exact case-sensitive "
        "relative entry (backslashes normalize to slashes). Omit it for FBX/OBJ first, then GLB/glTF, "
        "with ordinal path ordering within each group. Selection is limited to 128 models, 512 UTF-8 "
        "bytes per path and 32 segments; extraction retains its existing byte/entry limits. "
        "archive_selection reports complete candidates, chosen paths and extraction state. Missing, "
        "conflicting or over-limit selections fail; an unusable selected model never falls back to another. "
        "All allowed archive files are extracted/imported; the selector determines the returned model."
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
    name: Annotated[
        str,
        "Asset base name for a single file; folder name for a dependency bundle, whose model basename is preserved.",
    ]
    | None = None,
    output_folder: Annotated[
        str,
        "Destination under Assets/. Dependency bundles use a fresh folder; partial or failed imports may retain output for repair.",
    ]
    | None = None,
    target_size: Annotated[
        float,
        "Largest default-pose mesh-bounds dimension in meters when Auto-Normalize Imported Models is enabled; "
        "finite zero/negative values preserve scale. Omitted defaults to 1. Normalized glTF may return a prefab variant.",
    ]
    | None = None,
    animation_type: Annotated[
        Literal["none", "generic", "humanoid", "legacy"],
        "FBX/OBJ only: rig/animation import mode. 'generic' or 'humanoid' surface the model's "
        "AnimationClips; 'legacy' selects Unity's legacy Animation system (rarely needed); "
        "omitted or 'none' imports no rig. Ignored for glTF/GLB.",
    ]
    | None = None,
    archive_model_path: Annotated[
        str,
        "ZIP only: exact case-sensitive relative model entry. Omit for deterministic FBX/OBJ-first selection; "
        "no basename search or fallback. At most 512 UTF-8 bytes and 32 segments.",
    ]
    | None = None,
) -> dict[str, Any]:
    try:
        archive_model_path = _normalize_archive_model_path(archive_model_path)
    except ValueError as error:
        return {
            "success": False,
            "error": str(error),
            "data": {"model_import_error_code": "archive_model_path_invalid"},
        }
    if (
        archive_model_path is not None
        and PurePosixPath(source_path.replace("\\", "/")).suffix.lower() != ".zip"
    ):
        return {
            "success": False,
            "error": "archive_model_path applies only to ZIP archives.",
            "data": {"model_import_error_code": "archive_selector_requires_zip"},
        }
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
        "archiveModelPath": archive_model_path,
    }
    params_dict = {k: v for k, v in params_dict.items() if v is not None}

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "import_model_file",
        params_dict,
    )

    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
