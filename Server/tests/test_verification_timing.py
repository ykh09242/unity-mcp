"""The public report ties scoped transport and Player launcher observations to the job."""

import pytest

from core import phase_diagnostics as diag
from core.config import config
from core.verification_timing import VerificationTiming
from services.registry.operation_context import current_operation
from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationScheduler,
    OperationScope,
)
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_player_candidates import registered
from .test_verification_service import service
from .test_http_command_outcome import command_hub, dispatch


@pytest.mark.asyncio
async def test_new_command_lease_and_wire_share_correlation_even_off_log_sample(
    command_hub, monkeypatch
):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: False)
    command_hub.websocket.state.plugin_features |= {diag.CAPABILITY}
    timing = VerificationTiming("direct")
    with timing.bind():
        assert (await dispatch())["success"]
    (row,) = timing.finish()["commands"]
    assert row["scheduler_queue_ms"] >= 0
    assert row["correlation"] == command_hub.sent[0]["diagnostics"]["correlation"]


@pytest.mark.asyncio
async def test_timed_direct_player_records_observed_boundaries(
    service, registered, child, monkeypatch
):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: False)
    subject, ctx, _, native = service
    project, _, register = registered
    register("wind")
    native.return_value["data"]["projectRoot"] = str(project)
    reply = native.return_value

    async def observed(_send, _instance, command, params, **kwargs):
        with diag.invocation(command):
            assert diag.request(command, True)
            diag.step(command, "response")
            return reply

    native.side_effect = observed
    scheduler = OperationScheduler()
    request = OperationAdmission(OperationScope("u", "p", "generation"), "exclusive", lambda: True)
    async with scheduler.session(request) as owner:
        token = current_operation.set(owner)
        try:
            started = await subject.execute(ctx, "run", {"candidate_id": "wind", "wait_seconds": 5})
        finally:
            current_operation.reset(token)
    response = await subject.execute(ctx, "report", {"run_id": started["data"]["run_id"]})
    report = response["data"]
    timing = report["timing"]
    assert timing["entry"] == "direct"
    assert timing["phases_ms"]["scheduler_queue"] == owner.queue_ms
    assert timing["phases_ms"]["orchestration"] >= 0
    assert timing["phases_ms"]["task_dispatch"] >= 0
    assert [row["command"] for row in timing["commands"]] == [
        "manage_verification",
        "get_project_info",
    ]
    assert len({row["correlation"] for row in timing["commands"]}) == 2
    assert all(row["native_requested"] and row["response_ms"] >= 0 for row in timing["commands"])
    player_job = report["executed_checks"][0]["job_id"]
    player = timing["players_ms"][player_job]
    assert all(
        player[name] >= 0
        for name in ("process_spawn", "process_evidence_write", "final_report_read")
    )
    assert timing["client_delivery_ms"] is timing["cross_process_clock_offset_ms"] is None
