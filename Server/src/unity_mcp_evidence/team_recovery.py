"""Reconstruct a receipt-owned Addressables prefab continuation from public observations."""

import re

from .play_scenario_analysis_io import AnalysisError
from .play_scenario_report_json import parse_report
from .team_impact import guid
from .team_projection import Aliases, choice, fields, items, opaque

_ACTIONS = {
    "reconcile": {"reconcile_prefab"},
    "replan": {"replan_prefab"},
    "resumed": {"create_prefab", "register_prefab"},
}
_PHASES = {"planned", "creating", "created", "registering", "registered", "cancelled"}
_DIGEST_FIELDS = (
    "store",
    "project",
    "receipt",
    "session",
    "recorded_session",
    "observing_session",
    "workflow",
    "predecessor",
    "observation",
    "approval",
    "prefab",
    "prefab_version",
    "expected_prefab",
    "expected_version",
    "observed_prefab",
    "observed_version",
    "group",
)


def _mapping(value):
    if not isinstance(value, dict):
        raise AnalysisError("Expected a public prefab workflow response")
    return value


def _prefab_version(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-f]{32}:(?:[A-Za-z0-9+/]{43}=):(?:[A-Za-z0-9+/]{43}=)", value
    ):
        raise ValueError("Invalid saved prefab fingerprint")
    return value


def project_recovery(raw: bytes, role: str, aliases: Aliases) -> dict:
    """Use only the public response and receipt. Older captures keep an explicit gap."""
    document = parse_report(raw)
    if "structuredContent" in document:
        receipt = _mapping(document["structuredContent"])
        retained = receipt.get("result")
        if retained is None:
            return {"evidence": "unavailable"}
        payload = _mapping(_mapping(retained).get("structuredContent"))
        document = _mapping(payload.get("body"))
    else:
        receipt = document.get("execution_receipt")
    data = document.get("data")
    if not isinstance(receipt, dict) or not isinstance(data, dict):
        return {"evidence": "unavailable"}
    provenance = data.get("handoff")
    if not isinstance(provenance, dict):
        return {"evidence": "legacy"}
    if (
        type(receipt.get("version")) is not int
        or receipt["version"] != 1
        or receipt.get("status") != "completed"
        or type(provenance.get("version")) is not int
        or provenance["version"] != 1
        or type(document.get("success")) is not bool
    ):
        return {"evidence": "unavailable"}
    if not any(
        _mapping(attempt).get("observed") is True and attempt.get("dispatched") is True
        for attempt in items(receipt.get("attempts"))
    ):
        return {"evidence": "unavailable"}
    project = receipt.get("project_hash")
    if not isinstance(project, str) or not re.fullmatch(r"[0-9a-f]{16}", project):
        raise ValueError("Invalid execution receipt project identity")
    native = _mapping(data.get("reconciliation") or {})
    expected_guid = native.get("expected_prefab_guid")
    observed_guid = native.get("observed_prefab_guid")
    values = {
        "evidence": "observed",
        "action": choice(provenance.get("action"), _ACTIONS[role]),
        "phase": choice(provenance.get("phase"), _PHASES),
        "recorded_phase": choice(native["recorded_phase"], _PHASES)
        if "recorded_phase" in native
        else None,
        "success": document["success"],
        "claimed": native.get("continuation_claimed") is True,
        "available": native.get("replan_available") is True,
        "store": aliases.token("store", guid(receipt.get("store_id"))),
        "project": aliases.token("project", project),
        "receipt": aliases.token("receipt", guid(receipt.get("receipt_id"))),
        "session": aliases.token("session", guid(data.get("editor_session_id"))),
        "recorded_session": aliases.nullable(
            "session",
            guid(native["recorded_editor_session_id"])
            if native.get("recorded_editor_session_id")
            else None,
        ),
        "observing_session": aliases.nullable(
            "session",
            guid(native["observing_editor_session_id"])
            if native.get("observing_editor_session_id")
            else None,
        ),
        "workflow": aliases.token("workflow", opaque(provenance.get("workflow_id"))),
        "predecessor": aliases.nullable(
            "workflow",
            opaque(provenance["previous_workflow_id"])
            if provenance.get("previous_workflow_id") is not None
            else None,
        ),
        "observation": aliases.nullable(
            "observation",
            opaque(provenance["observation_id"])
            if provenance.get("observation_id") is not None
            else None,
        ),
        "approval": aliases.nullable(
            "observation",
            opaque(provenance["approved_observation_id"])
            if provenance.get("approved_observation_id") is not None
            else None,
        ),
        "prefab": aliases.nullable(
            "guid", guid(data["prefab_guid"]) if data.get("prefab_guid") else None
        ),
        "prefab_version": aliases.nullable(
            "prefab-version", _prefab_version(data.get("prefab_hash"))
        ),
        "expected_prefab": aliases.nullable("guid", guid(expected_guid) if expected_guid else None),
        "expected_version": aliases.nullable(
            "prefab-version", _prefab_version(native.get("expected_prefab_hash"))
        ),
        "observed_prefab": aliases.nullable("guid", guid(observed_guid) if observed_guid else None),
        "observed_version": aliases.nullable(
            "prefab-version", _prefab_version(native.get("observed_prefab_hash"))
        ),
        "group": aliases.token("guid", guid(data.get("group_guid"))),
    }
    return values


def restore_recovery(role: str, row: dict) -> dict | None:
    evidence = choice(_mapping(row).get("evidence"), {"observed", "unavailable", "legacy"})
    if evidence != "observed":
        fields(row, ("evidence",))
        return None
    fields(
        row,
        (
            "evidence",
            "action",
            "phase",
            "recorded_phase",
            "success",
            "available",
            "claimed",
            *_DIGEST_FIELDS,
        ),
    )
    choice(row["action"], _ACTIONS[role])
    choice(row["phase"], _PHASES)
    if row["recorded_phase"] is not None:
        choice(row["recorded_phase"], _PHASES)
    for key in ("success", "available", "claimed"):
        if type(row[key]) is not bool:
            raise ValueError("Expected an explicit prefab workflow observation")
    required = {"store", "project", "receipt", "session", "workflow", "group"}
    for key in _DIGEST_FIELDS:
        if key in required or row[key] is not None:
            opaque(row[key])
    return row


def _prefab(row):
    return row["prefab"], row["prefab_version"]


def _same_scope(left, right):
    return (left["store"], left["project"], left["group"]) == (
        right["store"],
        right["project"],
        right["group"],
    ) and left["receipt"] != right["receipt"]


def _reconciled(row):
    return (
        row is not None
        and row["session"] == row["recorded_session"]
        and row["recorded_phase"] == row["phase"]
        and row["observing_session"] is not None
        and row["recorded_session"] != row["observing_session"]
    )


def assess_recovery(inputs: dict) -> dict:
    prior, approval, resumed = (inputs[name] for name in ("reconcile", "replan", "resumed"))
    reconciled = _reconciled(prior)
    last = None
    if reconciled:
        if prior["phase"] == "registered" and all(_prefab(prior)):
            last = "register_prefab"
        elif prior["phase"] in {"created", "registering", "cancelled"} and all(_prefab(prior)):
            last = "create_prefab"
        elif prior["phase"] in {"planned", "cancelled"} and _prefab(prior) == (None, None):
            last = "none"
    approved_step = None
    if (
        reconciled
        and approval
        and _same_scope(prior, approval)
        and prior["success"]
        and approval["success"]
        and prior["available"]
        and not prior["claimed"]
        and approval["claimed"]
        and not approval["available"]
        and prior["phase"] in {"planned", "created"}
        and prior["phase"] == approval["phase"] == approval["recorded_phase"]
        and prior["workflow"] == approval["predecessor"] != approval["workflow"]
        and prior["recorded_session"] == approval["recorded_session"]
        and prior["observing_session"] == approval["observing_session"] == approval["session"]
        and prior["observation"] is not None
        and prior["observation"] == approval["observation"] == approval["approval"]
        and _prefab(prior) == _prefab(approval)
    ):
        if prior["phase"] == "planned" and last == "none":
            approved_step = "create_prefab"
        if (
            prior["phase"] == "created"
            and last == "create_prefab"
            and _prefab(prior)
            == (prior["observed_prefab"], prior["observed_version"])
            == (prior["expected_prefab"], prior["expected_version"])
        ):
            approved_step = "register_prefab"
    step = None
    settled = None
    if (
        approved_step
        and resumed
        and _same_scope(approval, resumed)
        and _same_scope(prior, resumed)
        and all(
            resumed[key] == approval[key]
            for key in ("session", "workflow", "predecessor", "approval")
        )
        and resumed["action"] == approved_step
        and (approved_step != "register_prefab" or _prefab(resumed) == _prefab(approval))
    ):
        pending, finished = {
            "create_prefab": ("creating", "created"),
            "register_prefab": ("registering", "registered"),
        }[approved_step]
        if resumed["phase"] == pending:
            step = approved_step
        elif resumed["phase"] == finished and resumed["success"] and all(_prefab(resumed)):
            step, settled = approved_step, True
    gaps = {}
    if last is None:
        gaps["last_confirmed_write"] = "receipt_owned_reconciliation_checkpoint_required"
    if step is None:
        gaps["resumed_step"] = (
            "matching_successor_execution_receipt_required"
            if approved_step
            else "matching_reconciliation_and_approved_successor_required"
        )
    if settled is None:
        gaps["resumed_write_confirmed"] = "settled_successor_write_observation_required"
    return {
        "state": "incomplete" if gaps else "reconstructed",
        "last_confirmed_write": last,
        "approved_next_step": approved_step,
        "resumed_step": step,
        "resumed_write_confirmed": settled,
        "workflow": prior["workflow"] if reconciled else None,
        "successor": approval["workflow"] if approved_step else None,
        "unavailable_conclusions": gaps,
    }
