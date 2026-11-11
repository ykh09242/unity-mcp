"""Reviewed overflow admission for status/cancel calls, never a caller policy hint."""

from collections.abc import Mapping

from services.operation_policy import OperationMode, resolve_operation_policy


# Keep this narrower than Control policy: exports, result viewers, issue lists and
# arbitrary wait wrappers must not consume recovery headroom.
_COMMANDS = frozenset(
    {
        "ping",
        "get_editor_state",
        "get_compile_job",
        "get_test_job",
        "get_tool_states",
        "cancel_test_job",
    }
)
_ACTIONS = {
    "manage_script_quality": frozenset({"capabilities", "status", "cancel"}),
    "manage_build": frozenset({"status", "cancel"}),
    "manage_project_auditor": frozenset({"availability", "status"}),
    "manage_packages": frozenset({"status", "ping"}),
    "manage_editor": frozenset({"get_play_mode_job", "cancel_play_mode_job"}),
    "manage_play_scenario": frozenset({"status", "suite_status", "cancel", "suite_cancel"}),
    "manage_navigation": frozenset({"bake_status", "cancel_bake"}),
    "manage_profiler": frozenset({"profiler_status", "window_status", "window_cancel"}),
}

CONTROL_PARTICIPANTS = 8
CONTROL_PER_PRINCIPAL = 2
CONTROL_PER_INSTANCE = 1
CONTROL_BYTES = 256 * 1024
CONTROL_BYTES_PER_PRINCIPAL = 64 * 1024
CONTROL_BYTES_PER_INSTANCE = 32 * 1024
CONTROL_RESULT_BYTES = 1024 * 1024


def reserved_control(
    name: str, params: Mapping, *, native: bool = False, trusted_builtin: bool = True
) -> bool:
    """Require a reviewed route as well as the server's trusted built-in identity."""
    if trusted_builtin is not True or not isinstance(name, str) or not isinstance(params, Mapping):
        return False
    action = params.get("action")
    if action is not None and not isinstance(action, str):
        return False
    action = action.lower() if isinstance(action, str) else None
    if not (name in _COMMANDS and action is None or action in _ACTIONS.get(name, ())):
        return False
    return resolve_operation_policy(name, params, native=native).mode == OperationMode.CONTROL
