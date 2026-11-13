"""Shared importer key handling; callers retain their own value admission rules."""

from copy import deepcopy
import json
from typing import Any, Final


IMPORT_FIELD_NAMES: Final = {
    "texture_type": "textureType",
    "texture_shape": "textureShape",
    "srgb": "sRGBTexture",
    "alpha_source": "alphaSource",
    "alpha_is_transparency": "alphaIsTransparency",
    "readable": "isReadable",
    "generate_mipmaps": "mipmapEnabled",
    "mipmap_filter": "mipmapFilter",
    "wrap_mode": "wrapMode",
    "wrap_mode_u": "wrapModeU",
    "wrap_mode_v": "wrapModeV",
    "filter_mode": "filterMode",
    "aniso_level": "anisoLevel",
    "max_texture_size": "maxTextureSize",
    "compression": "textureCompression",
    "compression_crunched": "crunchedCompression",
    "compression_quality": "compressionQuality",
    "sprite_mode": "spriteImportMode",
    "sprite_pixels_per_unit": "spritePixelsPerUnit",
    "sprite_pivot": "spritePivot",
    "sprite_mesh_type": "spriteMeshType",
    "sprite_extrude": "spriteExtrude",
    "platform_settings": "platformSettings",
}


def finalize_import_settings(
    value: dict[str, Any], normalized: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Merge native fields without discarding unknown requests or conflicting aliases."""
    result = dict(normalized)
    if "platform_settings" in value or "platformSettings" in value:
        if "platform_settings" in value and "platformSettings" in value:
            if json.dumps(value["platform_settings"], sort_keys=True) != json.dumps(
                value["platformSettings"], sort_keys=True
            ):
                return None, "Conflicting platform_settings and platformSettings aliases."
        platforms = value.get("platform_settings", value.get("platformSettings"))
        if not isinstance(platforms, dict) or any(
            not isinstance(settings, dict) for settings in platforms.values()
        ):
            return None, "platform_settings must be an object of platform-name objects."
        # Backend names, fields, values and version support remain native decisions.
        result["platformSettings"] = deepcopy(platforms)

    for key, raw in value.items():
        if key in IMPORT_FIELD_NAMES or key == "platformSettings":
            continue
        public_alias = next(
            (public for public, native in IMPORT_FIELD_NAMES.items() if native == key), None
        )
        if key in result or (public_alias is not None and public_alias in value):
            if json.dumps(result.get(key), sort_keys=True) != json.dumps(raw, sort_keys=True):
                return None, f"Conflicting importer aliases for '{key}'."
            continue
        # Preserve unknown fields for native partial-rejection diagnostics.
        result[key] = deepcopy(raw)
    return result, None
