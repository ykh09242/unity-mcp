"""Versioned scheduling effects, independent of permissions and discovery hints.

Only reviewed routes receive Control or SharedRead. Unknown routes stay Exclusive,
including on legacy permission sessions. Native handler identity must be established
by the registry owner before setting trusted_builtin; caller JSON is never authority.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from services.operation_permissions import (
    JsonValue,
    _CANONICAL_ALIASES,
    _COMPILING_SCRIPT_ACTIONS,
    _PUBLIC_SCRIPT_WRAPPERS,
)
from services.registry.capability_catalog import CAPABILITIES

POLICY_VERSION: Final = 1


class OperationMode(StrEnum):
    CONTROL = "control"
    SHARED_READ = "shared_read"
    EXCLUSIVE = "exclusive"


class OperationLifetime(StrEnum):
    COMMAND = "command"
    TRACKED_JOB = "tracked_job"
    TRIGGER_ONLY = "trigger_only"


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    mode: OperationMode
    lifetime: OperationLifetime
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """Reviewed canonical actions; public wrappers need a separate effects review."""

    command: str
    actions: tuple[str, ...]
    mode: OperationMode
    lifetime: OperationLifetime = OperationLifetime.COMMAND
    public: bool = False
    public_refresh: bool = False


CONTROL: Final = OperationMode.CONTROL
SHARED_READ: Final = OperationMode.SHARED_READ
EXCLUSIVE: Final = OperationMode.EXCLUSIVE
COMMAND: Final = OperationLifetime.COMMAND
TRACKED_JOB: Final = OperationLifetime.TRACKED_JOB
TRIGGER_ONLY: Final = OperationLifetime.TRIGGER_ONLY

# capture_ui_snapshot is deliberately excluded: UguiSnapshotCapture reads custom
# text/color getters and Selectable.IsInteractable; trusted handler identity does
# not prove bound-object callbacks safe. Menu inspection is conservative too:
# Menu.GetEnabled may depend on user menu validation, so it is not a reviewed read.
# These are effects reviews, not another tool/action inventory. Alias admission and
# generated native partitions come from the existing canonical capability catalog.
POLICY_RULES: Final = (
    *(
        PolicyRule(name, (), CONTROL, public=True)
        for name in (
            "ping",
            "get_editor_state",
            "get_compile_job",
            "get_test_job",
            "get_tool_states",
            "cancel_test_job",
            "wait_for_operation",
        )
    ),
    PolicyRule("manage_build", ("status", "cancel"), CONTROL, public=True),
    PolicyRule("manage_packages", ("status",), CONTROL, public=True),
    PolicyRule(
        "manage_editor", ("get_play_mode_job", "cancel_play_mode_job"), CONTROL, public=True
    ),
    PolicyRule(
        "manage_play_scenario",
        ("status", "suite_status", "cancel", "suite_cancel"),
        CONTROL,
        public=True,
    ),
    PolicyRule("manage_navigation", ("bake_status", "cancel_bake"), CONTROL, public=True),
    PolicyRule(
        "manage_profiler",
        ("profiler_status", "window_status", "window_cancel"),
        CONTROL,
        public=True,
    ),
    *(
        PolicyRule(name, (), SHARED_READ, public=True)
        for name in (
            "find_in_file",
            "get_sha",
            "manage_script_capabilities",
            "validate_script",
            "debug_request_context",
        )
    ),
    *(
        PolicyRule(name, (), SHARED_READ)
        for name in (
            "get_selection",
            "get_active_tool",
            "get_tags",
            "get_layers",
            "get_prefab_stage",
            "get_menu_items",
        )
    ),
    PolicyRule("find_gameobjects", (), SHARED_READ, public_refresh=True),
    PolicyRule(
        "manage_scene",
        ("get_active", "get_loaded_scenes", "get_build_settings", "get_hierarchy"),
        SHARED_READ,
        public_refresh=True,
    ),
    PolicyRule(
        "manage_physics",
        ("raycast", "raycast_all", "linecast", "shapecast", "overlap"),
        SHARED_READ,
        public=True,
    ),
    PolicyRule(
        "manage_asset",
        (
            "get_dependencies",
            "get_reverse_references",
            "preview_move",
            "preview_delete",
            "list_asset_bundles",
            "get_bundle_assets",
            "get_bundle_dependencies",
        ),
        SHARED_READ,
        public=True,
    ),
    PolicyRule(
        "manage_ugui", ("diagnose_projection", "diagnose_device_profiles"), SHARED_READ, public=True
    ),
    PolicyRule(
        "manage_packages", ("get_package_info", "list_registries", "ping"), SHARED_READ, public=True
    ),
    PolicyRule(
        "manage_packages",
        ("list_packages", "search_packages"),
        SHARED_READ,
        TRACKED_JOB,
        public=True,
    ),
    PolicyRule(
        "manage_build", ("report_history", "report", "compare_reports"), SHARED_READ, public=True
    ),
    PolicyRule("run_tests", (), EXCLUSIVE, TRACKED_JOB, public=True),
    PolicyRule(
        "manage_build", ("build", "build_from_plan", "batch"), EXCLUSIVE, TRACKED_JOB, public=True
    ),
    PolicyRule("manage_play_scenario", ("run", "suite_run"), EXCLUSIVE, TRACKED_JOB, public=True),
    PolicyRule(
        "manage_packages",
        ("add_package", "remove_package", "embed_package"),
        EXCLUSIVE,
        TRACKED_JOB,
        public=True,
    ),
    PolicyRule(
        "manage_packages",
        ("resolve_packages", "add_registry", "remove_registry"),
        EXCLUSIVE,
        TRIGGER_ONLY,
        public=True,
    ),
    PolicyRule("refresh_unity", (), EXCLUSIVE, TRIGGER_ONLY, public=True),
    PolicyRule("manage_navigation", ("bake",), EXCLUSIVE, TRIGGER_ONLY, public=True),
    PolicyRule(
        "manage_camera",
        ("screenshot", "screenshot_multiview"),
        EXCLUSIVE,
        TRIGGER_ONLY,
        public=True,
    ),
    PolicyRule("manage_ui", ("render_ui",), EXCLUSIVE, TRIGGER_ONLY, public=True),
    PolicyRule(
        "manage_graphics",
        ("bake_start", "bake_reflection_probe"),
        EXCLUSIVE,
        TRIGGER_ONLY,
        public=True,
    ),
    *(
        PolicyRule(name, ("generate",), EXCLUSIVE, TRIGGER_ONLY, public=True)
        for name in ("generate_audio", "generate_image", "generate_model")
    ),
    PolicyRule(
        "manage_script",
        tuple(sorted(_COMPILING_SCRIPT_ACTIONS)),
        EXCLUSIVE,
        TRIGGER_ONLY,
        public=True,
    ),
    *(
        PolicyRule(name, (), EXCLUSIVE, TRIGGER_ONLY, public=True)
        for name in sorted(_PUBLIC_SCRIPT_WRAPPERS)
    ),
)
CAPABILITY_ALIASES: Final = {feature.name: feature for feature in CAPABILITIES}
CANONICAL_ALIASES: Final = _CANONICAL_ALIASES
RULE_INDEX: Final = {
    (rule.command, action): rule for rule in POLICY_RULES for action in rule.actions or (None,)
}


def _exclusive(reason: str, lifetime: OperationLifetime = COMMAND) -> OperationPolicy:
    return OperationPolicy(EXCLUSIVE, lifetime, reason)


def resolve_operation_policy(
    name: str,
    params: Mapping[str, JsonValue],
    *,
    native: bool = False,
    trusted_builtin: bool = True,
) -> OperationPolicy:
    """Resolve the complete route before preflight, without granting permissions."""
    if trusted_builtin is not True:
        return _exclusive("untrusted_handler")
    if not isinstance(name, str) or not isinstance(params, Mapping):
        return _exclusive("malformed_operation")
    token = params.get("action")
    if token is not None and not isinstance(token, str):
        return _exclusive("malformed_action")
    action = token.lower() if isinstance(token, str) else None
    feature = CAPABILITY_ALIASES.get(name)
    if feature is not None:
        admitted = feature.native_actions if native else feature.actions
        if action not in admitted:
            return _exclusive("unsupported_capability_action")
        name = feature.source
    name = CANONICAL_ALIASES.get(name, name)
    # A native execute handler ignores supplied action aliases; no inspection hint
    # or caller-supplied mode can turn arbitrary code into an observation.
    if name in {"execute_menu_item", "execute_code", "execute_custom_tool"}:
        return _exclusive("arbitrary_execution")
    if name in {"batch_execute", "batch_validate", "batch_apply_preview"}:
        return _exclusive("batch_envelope")
    if name == "manage_ugui" and action == "diagnose_raycast":
        if params.get("allow_custom_filters", False) is not False:
            return _exclusive("conditional_callbacks")
        return OperationPolicy(SHARED_READ, COMMAND, "reviewed_read")
    if name == "manage_ui" and action == "patch_source":
        if params.get("preview", True) is True:
            return OperationPolicy(SHARED_READ, COMMAND, "reviewed_preview")
        return _exclusive("conditional_mutation", TRIGGER_ONLY)
    if name == "manage_gameobject" and action == "placement":
        if not native:
            return _exclusive("public_preflight", TRIGGER_ONLY)
        preview = params.get("preview")
        if (
            preview is None
            or preview is True
            or isinstance(preview, str)
            and preview.lower() == "true"
        ):
            return OperationPolicy(SHARED_READ, COMMAND, "reviewed_preview")
        return _exclusive("conditional_mutation")
    if name == "manage_editor" and action == "play":
        wait_until = params.get("wait_until")
        return _exclusive(
            "play_transition",
            TRACKED_JOB if wait_until in ("scene_loaded", "first_frame") else TRIGGER_ONLY,
        )
    if name == "manage_editor" and action == "stop":
        return _exclusive("play_transition", TRIGGER_ONLY)
    rule = RULE_INDEX.get((name, action))
    if rule is None:
        return _exclusive("unreviewed_operation")
    if not native and not rule.public:
        return _exclusive(
            "public_preflight" if rule.public_refresh else "unreviewed_wrapper",
            TRIGGER_ONLY if rule.public_refresh else COMMAND,
        )
    return OperationPolicy(
        rule.mode,
        rule.lifetime,
        "reviewed_control"
        if rule.mode == CONTROL
        else "reviewed_read"
        if rule.mode == SHARED_READ
        else "reviewed_mutation",
    )
