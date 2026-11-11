"""Classify already-authorized observations without repair, discovery, or log reads."""

from dataclasses import dataclass, field
from typing import Literal

from models.source_freshness import parse_freshness
from models.source_recovery import RecoveryDiagnostic, RecoveryEvidence


@dataclass(frozen=True, slots=True)
class RecoveryObservations:
    observed_at_unix_ms: int
    connection_status: Literal[
        "connected", "unavailable", "selection_required", "auth_required", "unknown"
    ]
    instance_id: str | None = None
    generation: str | None = None
    native_bound: bool = False
    wrong_target: bool = False
    admission_available: bool | None = None
    remote_hosted: bool = False
    state: dict = field(default_factory=dict)
    # Optional evidence adapters must verify PID + start identity against the selected
    # local Editor. A remote server's process table is never Editor-local evidence.
    process_identity_verified: bool = False
    process_state: Literal["running", "exited", "unknown"] = "unknown"
    process_observed_at_unix_ms: int | None = None
    native_probe_timed_out: bool = False


def _section(value, key):
    result = value.get(key)
    return result if isinstance(result, dict) else {}


def _age(value):
    return value if type(value) is int and value >= 0 else None


def classify_source_recovery(observations: RecoveryObservations) -> RecoveryDiagnostic:
    """Evidence is scoped by the caller's verified routing capture, never by a path."""
    source = observations
    evidence = [
        RecoveryEvidence(
            origin="routing",
            observed_at_unix_ms=source.observed_at_unix_ms,
            fact=source.connection_status,
            age_ms=0,
        )
    ]
    limitations = ["modal_and_safe_mode_not_observed", "editor_log_not_inspected"]

    def result(reason="unknown", certainty="unknown", action="inspect_selected_editor", **identity):
        return RecoveryDiagnostic(
            reason=reason,
            certainty=certainty,
            next_action=action,
            evidence=evidence,
            limitations=limitations,
            **identity,
        )

    if source.wrong_target or source.connection_status == "selection_required":
        return result(action="select_exact_instance")
    if source.connection_status == "auth_required":
        return result(action="use_authenticated_client")
    if source.admission_available is False:
        evidence.append(
            RecoveryEvidence(
                origin="routing",
                observed_at_unix_ms=source.observed_at_unix_ms,
                fact="capacity",
                age_ms=0,
            )
        )
        return result(action="observe_existing_jobs")
    local = (
        not source.remote_hosted
        and source.process_identity_verified
        and source.instance_id is not None
        and source.generation is not None
        and source.process_observed_at_unix_ms is not None
        and 0 <= source.observed_at_unix_ms - source.process_observed_at_unix_ms <= 5000
    )
    if local and source.process_state in ("running", "exited"):
        evidence.append(
            RecoveryEvidence(
                origin="local_process",
                observed_at_unix_ms=source.process_observed_at_unix_ms,
                age_ms=source.observed_at_unix_ms - source.process_observed_at_unix_ms,
                fact="process_" + source.process_state,
            )
        )
        if source.process_state == "exited":
            return result("editor_exited", "observed", "inspect_selected_connection")
    else:
        limitations.append("editor_local_process_unavailable")
    state = source.state if source.native_bound else {}
    age = _age(_section(state, "staleness").get("age_ms"))
    stamp = _age(state.get("observed_at_unix_ms"))
    fresh = (
        stamp is not None
        and age is not None
        and age <= 5000
        and _section(state, "staleness").get("is_stale") is False
    )
    if stamp is not None and age is not None:
        evidence.append(
            RecoveryEvidence(
                origin="editor_state",
                observed_at_unix_ms=stamp,
                age_ms=age,
                fact="native_observed" if fresh else "stale_snapshot",
            )
        )
    else:
        limitations.append("native_evidence_unavailable")
    if fresh and _section(state, "compilation").get("is_domain_reload_pending") is True:
        evidence.append(
            RecoveryEvidence(
                origin="editor_state", observed_at_unix_ms=stamp, age_ms=age, fact="reload_pending"
            )
        )
        return result("reload_in_progress", "observed", "wait_and_observe_existing_work")
    if fresh and _section(state, "compilation").get("is_compiling") is True:
        return result(action="wait_and_observe_existing_work")
    freshness = parse_freshness(state.get("source_freshness"))
    source_age = _age(state.get("source_observation_age_ms"))
    if (
        fresh
        and freshness.status == "compile_failed"
        and source_age is not None
        and source_age <= 5000
        and freshness.observed_at_unix_ms is not None
    ):
        evidence.append(
            RecoveryEvidence(
                origin="source_freshness",
                observed_at_unix_ms=freshness.observed_at_unix_ms,
                age_ms=source_age,
                fact="compile_failed",
            )
        )
        return result(
            "compilation_failed",
            "observed",
            "inspect_compile_diagnostics",
            source_token=freshness.source_token,
            compile_id=freshness.compile_id,
        )
    if source_age is not None and source_age > 5000:
        limitations.append("source_evidence_stale")
    if local and source.process_state == "running" and source.native_probe_timed_out and not fresh:
        evidence.append(
            RecoveryEvidence(
                origin="routing",
                observed_at_unix_ms=source.observed_at_unix_ms,
                age_ms=0,
                fact="native_probe_timed_out",
            )
        )
        return result("editor_unresponsive", "suspected")
    if source.connection_status == "unavailable":
        return result("transport_disconnected", "observed", "inspect_selected_connection")
    return result()
