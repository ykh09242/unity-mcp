"""Route a public, receipt-scoped prefab operation to its private Editor checkpoint."""

import re

from services.declared_action_contracts import (
    EXPECTED_OBSERVATION_PATTERN,
    OPERATION_ID_PATTERN,
    PREFAB_CHECKPOINT_CONTRACTS,
)
from services.execution_receipt_context import execution_owner


def prefab_parameters(params: dict) -> dict:
    action = params["action"]
    fields = {"action", *PREFAB_CHECKPOINT_CONTRACTS[action].required}
    if set(params) != fields:
        raise ValueError(f"{action} requires exactly {', '.join(sorted(fields))}.")
    if action == "replan_prefab":
        if params["operation_id"] == params["previous_operation_id"]:
            raise ValueError("Replanning requires a fresh operation_id.")
        observation = params["expected_observation"]
        if not isinstance(observation, str) or not re.fullmatch(
            EXPECTED_OBSERVATION_PATTERN, observation
        ):
            raise ValueError("expected_observation must identify a reconciliation in this Editor.")
    aliases = dict(PREFAB_CHECKPOINT_CONTRACTS[action].wire_aliases)
    for field in aliases:
        if not isinstance(params[field], str) or not re.fullmatch(
            OPERATION_ID_PATTERN, params[field]
        ):
            raise ValueError(f"{field} must be a lowercase UUID without hyphens.")
    owner = execution_owner.get()
    if owner is None:
        raise ValueError("Prefab workflows require an execution receipt with an explicit project.")
    result = params.copy()
    for field, native in aliases.items():
        result[native] = owner.prefab_checkpoint(result.pop(field))
    return result
