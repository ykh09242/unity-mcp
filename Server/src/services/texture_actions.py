"""Texture writes that require destination checks in the receiving Editor."""

from typing import Final

from pydantic import JsonValue


IMAGE_GENERATORS: Final = frozenset(
    {"create", "create_sprite", "apply_pattern", "apply_gradient", "apply_noise"}
)
CHECKED_TEXTURE_ACTIONS: Final = {
    action: f"{action}_checked" for action in IMAGE_GENERATORS | {"modify", "delete"}
}


def checked_texture_parameters(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    action = params.get("action")
    if isinstance(action, str) and (checked := CHECKED_TEXTURE_ACTIONS.get(action.lower())):
        return {**params, "action": checked}
    return params
