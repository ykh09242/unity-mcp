"""Route a public, receipt-scoped prefab operation to its private Editor checkpoint."""

import re

from services.execution_receipt_context import execution_owner

PREFAB_ACTIONS = (
    "preview_prefab",
    "create_prefab",
    "register_prefab",
    "prefab_status",
    "cancel_prefab",
)


def prefab_parameters(params: dict) -> dict:
    action = params["action"]
    fields = {"action", "operation_id"}
    if action == "preview_prefab":
        fields.update({"source_prefab", "prefab_path", "settings_path", "group_guid"})
    if set(params) != fields:
        raise ValueError(f"{action} requires exactly {', '.join(sorted(fields))}.")
    operation_id = params["operation_id"]
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("operation_id must be a lowercase UUID without hyphens.")
    owner = execution_owner.get()
    if owner is None:
        raise ValueError("Prefab workflows require an execution receipt with an explicit project.")
    result = params.copy()
    del result["operation_id"]
    result["checkpoint"] = owner.prefab_checkpoint(operation_id)
    return result
