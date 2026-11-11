"""Recovery advice must identify evidence, never infer a modal from silence."""

import pytest

from services.source_recovery_diagnostics import RecoveryObservations, classify_source_recovery


def observation(**changes):
    values = dict(
        observed_at_unix_ms=100_000,
        instance_id="Fixture@aaaa1111",
        generation="a" * 64,
        connection_status="connected",
        native_bound=True,
        state={
            "observed_at_unix_ms": 99_900,
            "staleness": {"age_ms": 100, "is_stale": False},
            "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
            "source_observation_age_ms": 100,
            "source_freshness": {
                "schema": "unity-mcp/source_freshness@1",
                "status": "compile_failed",
                "observed_at_unix_ms": 99_900,
                "source_token": "b" * 64,
                "compile_id": "c" * 32,
            },
        },
    )
    values.update(changes)
    return RecoveryObservations(**values)


def test_compile_failure_preserves_responsive_bridge_and_source_identity():
    result = classify_source_recovery(observation())
    assert result.reason == "compilation_failed"
    assert result.certainty == "observed"
    assert result.next_action == "inspect_compile_diagnostics"
    assert result.source_token == "b" * 64
    assert result.compile_id == "c" * 32
    assert {row.origin for row in result.evidence} >= {"routing", "source_freshness"}
    assert all(row.observed_at_unix_ms > 0 for row in result.evidence)


def test_expected_reload_is_not_a_connection_failure_or_compile_retry():
    source = observation()
    source.state["compilation"]["is_domain_reload_pending"] = True
    result = classify_source_recovery(source)
    assert result.reason == "reload_in_progress"
    assert result.next_action == "wait_and_observe_existing_work"


def test_new_compilation_supersedes_cached_failure_advice():
    source = observation()
    source.state["compilation"]["is_compiling"] = True
    result = classify_source_recovery(source)
    assert result.reason == "unknown"
    assert result.next_action == "wait_and_observe_existing_work"


def test_source_age_is_already_recomputed_at_the_server_observation():
    source = observation()
    source.state["staleness"]["age_ms"] = 4000
    source.state["source_observation_age_ms"] = 4000
    assert classify_source_recovery(source).reason == "compilation_failed"


@pytest.mark.parametrize("remote", [True, False])
def test_stale_heartbeat_alone_cannot_diagnose_dialog_or_exit(remote):
    source = observation(remote_hosted=remote)
    source.state["staleness"] = {"age_ms": 90_000, "is_stale": True}
    source.state["source_observation_age_ms"] = 90_000
    result = classify_source_recovery(source)
    assert result.reason == "unknown"
    assert result.certainty == "unknown"
    assert "modal_and_safe_mode_not_observed" in result.limitations
    assert result.next_action == "inspect_selected_editor"


def test_disconnection_does_not_mean_editor_exited():
    result = classify_source_recovery(
        observation(connection_status="unavailable", native_bound=False)
    )
    assert result.reason == "transport_disconnected"
    assert result.certainty == "observed"
    assert result.compile_id is None
    assert result.next_action == "inspect_selected_connection"


@pytest.mark.parametrize(
    "bound,remote,expected",
    [(True, False, "editor_exited"), (False, False, "unknown"), (True, True, "unknown")],
)
def test_process_exit_needs_exact_local_process_identity(bound, remote, expected):
    result = classify_source_recovery(
        observation(
            connection_status="unknown",
            native_bound=False,
            remote_hosted=remote,
            process_identity_verified=bound,
            process_state="exited",
            process_observed_at_unix_ms=99_950,
        )
    )
    assert result.reason == expected
    if expected == "editor_exited":
        assert result.evidence[-1].origin == "local_process"


def test_unresponsive_is_only_suspected_with_live_process_and_timed_out_probe():
    result = classify_source_recovery(
        observation(
            native_bound=False,
            native_probe_timed_out=True,
            process_identity_verified=True,
            process_state="running",
            process_observed_at_unix_ms=99_950,
        )
    )
    assert result.reason == "editor_unresponsive"
    assert result.certainty == "suspected"
    assert result.next_action == "inspect_selected_editor"


@pytest.mark.parametrize(
    "status,action",
    [
        ("selection_required", "select_exact_instance"),
        ("auth_required", "use_authenticated_client"),
    ],
)
def test_selection_or_denied_access_cannot_reuse_native_evidence(status, action):
    result = classify_source_recovery(observation(connection_status=status))
    assert result.reason == "unknown"
    assert result.next_action == action
    assert result.source_token is None


def test_missing_or_foreign_native_evidence_stays_unknown():
    assert classify_source_recovery(observation(state={})).reason == "unknown"
    assert classify_source_recovery(observation(native_bound=False)).reason == "unknown"
    assert (
        classify_source_recovery(observation(wrong_target=True)).next_action
        == "select_exact_instance"
    )


def test_invalid_or_old_source_evidence_does_not_claim_current_compile_failure():
    source = observation()
    source.state["source_observation_age_ms"] = 90_000
    assert classify_source_recovery(source).reason == "unknown"
    source.state["source_observation_age_ms"] = 1
    source.state["source_freshness"]["compile_id"] = "https://user:secret@host"
    result = classify_source_recovery(source)
    assert result.reason == "unknown"
    assert "secret" not in result.model_dump_json()


def test_capacity_is_not_an_editor_health_failure():
    result = classify_source_recovery(observation(admission_available=False))
    assert result.reason == "unknown"
    assert result.next_action == "observe_existing_jobs"


def test_source_recovery_never_reads_processes_or_editor_logs(monkeypatch):
    import builtins

    monkeypatch.setattr(
        builtins, "open", lambda *_a, **_k: pytest.fail("unexpected filesystem probe")
    )
    result = classify_source_recovery(observation(remote_hosted=True))
    assert result.reason == "compilation_failed"
    assert "editor_local_process_unavailable" in result.limitations
