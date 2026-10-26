"""Declared public readiness lifecycles, separate from native RPC timeouts.

The declarations follow existing send_mutation callers, including their preview
branches. They grant no permission or concurrency classification.
"""

from dataclasses import dataclass
import importlib
import math
from pathlib import Path

from core.config import config
from services.operation_permissions import JsonValue
from services.registry.capability_catalog import CAPABILITIES
from transport.command_timeout import configured_wait_timeout

READINESS_CALLBACKS = frozenset(
    {
        ("manage_audio", "manage_audio"),
        ("manage_localization", "manage_localization"),
        ("manage_script", "apply_text_edits"),
        ("manage_script", "create_script"),
        ("manage_script", "delete_script"),
        ("manage_script", "manage_script"),
        ("manage_ugui", "manage_ugui"),
        ("manage_ui", "manage_ui"),
        ("script_apply_edits", "script_apply_edits"),
    }
)


@dataclass(frozen=True, slots=True)
class PublicBudgetPolicy:
    readiness_seconds: float = 0.0
    hard_seconds: float | None = None


def resolve_public_budget(
    function, name: str, params: dict[str, JsonValue], *, trusted_builtin: bool
) -> PublicBudgetPolicy:
    """Resolve only the trusted callback actually registered for this route."""
    if trusted_builtin is not True:
        return PublicBudgetPolicy()
    declared_name = name
    if function.__module__ == "services.registry.capability_adapter":
        feature = next((entry for entry in CAPABILITIES if entry.name == name), None)
        if feature is None or params.get("action") not in feature.actions:
            return PublicBudgetPolicy()
        from services.registry.capability_adapter import get_capability_implementation

        function = get_capability_implementation(function, name)
        if function is None:
            return PublicBudgetPolicy()
        declared_name = feature.source
    module_name = getattr(function, "__module__", "")
    callback = getattr(function, "__name__", "")
    if callback != declared_name:
        return PublicBudgetPolicy()
    short_module = module_name.removeprefix("services.tools.")
    refresh = (short_module, callback) == ("refresh_unity", "refresh_unity")
    test_wait = (short_module, callback) == ("run_tests", "get_test_job")
    if not refresh and not test_wait and (short_module, callback) not in READINESS_CALLBACKS:
        return PublicBudgetPolicy()
    if module_name != "services.tools." + short_module:
        return PublicBudgetPolicy()
    module = importlib.import_module(module_name)
    code = getattr(function, "__code__", None)
    if (
        getattr(module, callback, None) is not function
        or code is None
        or Path(code.co_filename).resolve() != Path(module.__file__).resolve()
    ):
        return PublicBudgetPolicy()
    if refresh:
        value = params.get("timeout_seconds", 300.0)
        if type(value) in (int, float) and math.isfinite(value) and 1 <= value <= 86400:
            return PublicBudgetPolicy(hard_seconds=float(value))
        return PublicBudgetPolicy()
    if test_wait:
        value = params.get("wait_timeout")
        return (
            PublicBudgetPolicy(float(value))
            if type(value) is int and 0 < value <= 86400
            else PublicBudgetPolicy()
        )
    action = params.get("action")
    if isinstance(action, str):
        action = (
            action.strip().lower()
            if short_module == "manage_ugui"
            else action.lower()
            if short_module == "manage_ui"
            else action
        )
    else:
        action = None
    mutation = False
    hard = None
    if short_module == "manage_script":
        if callback in {"create_script", "delete_script"}:
            mutation = True
        elif callback == "manage_script":
            mutation = action in {"create", "delete"}
        else:
            try:
                options = module._normalize_script_options(params.get("options"))
            except ValueError:
                return PublicBudgetPolicy()
            mutation = not options.get("preview") and not options.get("debug_preview")
    elif short_module == "script_apply_edits":
        try:
            options = module._normalize_script_options(params.get("options"))
        except ValueError:
            return PublicBudgetPolicy()
        mutation = not options.get("preview")
        if options.get("mixed_order") == "request":
            value = float(config.command_total_timeout)
            hard = value if math.isfinite(value) and value > 0 else None
    elif short_module == "manage_audio":
        mutation = action in {"set_routing", "apply_import_settings"}
    elif short_module == "manage_localization":
        mutation = action in {"set_string_entry", "set_asset_entry", "set_fallbacks"}
    elif short_module == "manage_ugui":
        mutation = action in module._MUTATIONS
    elif short_module == "manage_ui":
        mutation = (
            action
            in {
                "create",
                "update",
                "delete",
                "attach_ui_document",
                "detach_ui_document",
                "create_panel_settings",
                "update_panel_settings",
                "render_ui",
                "link_stylesheet",
                "modify_visual_element",
            }
            or action == "patch_source"
            and params.get("preview", True) is False
        )
    ready = configured_wait_timeout("UNITY_MCP_EDITOR_READY_TIMEOUT", 300.0) if mutation else 0.0
    return PublicBudgetPolicy(ready, hard)
