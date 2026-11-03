"""Wire actions that older Animator handlers reject instead of ignoring a guard flag."""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue


RUNTIME_ANIMATOR_ACTIONS: Final = {
    "animator_set_parameter": "animator_set_parameter_runtime",
    "animator_set_enabled": "animator_set_enabled_runtime",
}


def animator_runtime_opt_in(params: Mapping[str, JsonValue]) -> bool:
    """Read explicit client controls before the native handler flattens its properties."""
    if any(key != "action" and key.replace("_", "").upper() == "ACTION" for key in params):
        raise ValueError("Conflicting Animator action spelling")
    flags = [key for key in params if key.replace("_", "").lower() == "runtimeonly"]
    if not flags:
        return False
    if len(flags) != 1 or flags[0] not in {"runtime_only", "runtimeOnly"}:
        raise ValueError("Ambiguous Animator runtime flag")
    value = params[flags[0]]
    if type(value) is not bool or (value and not isinstance(params.get("action"), str)):
        raise ValueError("The Animator runtime flag requires a Boolean and an explicit action")
    return value


def guarded_animator_parameters(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Route only explicit top-level Boolean opt-ins to the guarded native handlers."""
    if animator_runtime_opt_in(params):
        action = params["action"]
        if isinstance(action, str) and (guarded := RUNTIME_ANIMATOR_ACTIONS.get(action.lower())):
            return {**params, "action": guarded}
    return params


def guarded_animator_batch_member(member: dict[str, JsonValue]) -> dict[str, JsonValue]:
    tool = member.get("tool")
    params = member.get("params")
    if isinstance(tool, str) and tool.lower() == "manage_animation" and isinstance(params, dict):
        guarded = guarded_animator_parameters(params)
        if guarded is not params:
            return {**member, "params": guarded}
    return member
