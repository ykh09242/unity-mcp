"""
Defines the generate_image tool for AI 2D image generation in Unity.

Thin pass-through: this tool carries NO API keys and NO file bytes. The C# side
reads the user's provider key from the OS secure store, performs the provider
HTTPS call, downloads/decodes the result, and imports it as a texture.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    group="asset_gen",
    description=(
        "Generate 2D images with AI providers (fal.ai, OpenRouter) and import them as "
        "textures/sprites into the Unity project. Bring-your-own-key: provider keys live "
        "in the editor's secure store and never cross the bridge.\n\n"
        "ACTIONS:\n"
        "- generate: Submit an image job (text->image or image->image). Returns { job_id }; "
        "poll with the status action. Params: "
        "provider, mode (text|image), prompt, image_path|image_url, model, transparent, "
        "width, height, name, output_folder.\n"
        "- status: Poll an async job by job_id; includes recovery_state, recovery_code, provider_task_id and provider_phase. "
        "Recorded provider tasks can reconnect after a domain reload within the same Editor session and original two-hour budget. "
        "Unknown submission/import outcomes are not replayed; inspect retained output before retrying. "
        "Saved local output can resume without a provider key when its recorded file/metadata identity still matches. "
        "OpenRouter responses not yet saved locally cannot reconnect.\n"
        "- cancel: Stop local job continuation by job_id; this does not guarantee provider-side cancellation or a refund.\n"
        "- list_providers: List configured image providers and capabilities (no key values).\n"
        "- list_models: Search/paginate live fal and OpenRouter models, modes and freshness.\n"
        "- refresh_models: Force discovery. Repeat list_models while catalogs[].refreshing is true.\n\n"
        "Background removal is unsupported. Legacy direct remove_background requests return "
        "an unsupported_capability error without submitting a job. The transparent option "
        "only sets the Unity texture import flag; it does not remove backgrounds."
    ),
    annotations=ToolAnnotations(
        title="Generate Image",
        destructiveHint=False,
    ),
)
async def generate_image(
    ctx: Context,
    action: Annotated[
        Literal[
            "generate",
            "status",
            "cancel",
            "list_providers",
            "list_models",
            "refresh_models",
        ],
        "Action to perform.",
    ],
    provider: Annotated[str, "Provider id (fal, openrouter)."] | None = None,
    mode: Annotated[str, "Generation mode: text or image."] | None = None,
    prompt: Annotated[str, "Text prompt for text->image."] | None = None,
    image_path: Annotated[str, "Path to a source image for image->image mode."] | None = None,
    image_url: Annotated[str, "URL of a source image for image->image."] | None = None,
    model: Annotated[
        str, "Provider model id/slug returned by list_models; omit for the GUI-selected default."
    ]
    | None = None,
    transparent: Annotated[
        bool,
        "Mark the imported texture as alpha-is-transparency. NOTE: fal/FLUX "
        "and OpenRouter have no generation-time transparency, so this only sets the "
        "Unity import flag — it does not make the model render a transparent background.",
    ]
    | None = None,
    width: Annotated[int, "Output width in pixels."] | None = None,
    height: Annotated[int, "Output height in pixels."] | None = None,
    name: Annotated[str, "Base name for the imported asset."] | None = None,
    output_folder: Annotated[str, "Destination folder under Assets/ for the import."] | None = None,
    job_id: Annotated[str, "Job id for status/cancel."] | None = None,
    search: Annotated[str, "Filter list_models by name, id or use case."] | None = None,
    limit: Annotated[int, "Model page size (1..200; default 50)."] | None = None,
    offset: Annotated[int, "Model page offset (default 0)."] | None = None,
) -> dict[str, Any]:
    action_lower = action.lower()
    if action_lower == "remove_background":
        return {
            "success": False,
            "error": "Background removal is unsupported in this version. 'transparent' only "
            "sets the Unity texture import flag and does not remove backgrounds.",
            "data": {
                "reason": "unsupported_capability",
                "capability": "remove_background",
                "supported": False,
            },
        }

    unity_instance = await get_unity_instance_from_context(ctx)

    params_dict = {
        "action": action_lower,
        "provider": provider,
        "mode": mode,
        "prompt": prompt,
        "imagePath": image_path,
        "imageUrl": image_url,
        "model": model,
        "transparent": transparent,
        "width": width,
        "height": height,
        "name": name,
        "outputFolder": output_folder,
        "jobId": job_id,
        "search": search,
        "limit": limit,
        "offset": offset,
    }

    # Remove None values
    params_dict = {k: v for k, v in params_dict.items() if v is not None}

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "generate_image",
        params_dict,
    )

    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
