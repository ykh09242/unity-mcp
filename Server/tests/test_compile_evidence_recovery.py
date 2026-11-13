"""Compile observations retain identity and expose actionable, bounded evidence failures."""

from unittest.mock import AsyncMock

import pytest

from models.source_freshness import parse_freshness
from services.tools import get_compile_job as status


COMPILE_ID = "a" * 32
INSTANCE = "Fixture@0123456789abcdef"


def test_invalid_compile_fields_are_identified_without_rejected_values():
    value = status.unknown_compile_job(COMPILE_ID, "result_unavailable")
    value["error_count"] = "private-value"
    value["private-extra-key"] = "private-value"
    result = status.parse_compile_job(value, COMPILE_ID)
    assert result["reason"] == "invalid_evidence"
    assert result["compile_id"] == COMPILE_ID
    assert result["outcome"] == "unknown"
    diagnostic = result["evidence_diagnostics"]
    assert {"field": "error_count", "code": "int_type"} in diagnostic["issues"]
    assert {"field": "$", "code": "extra_forbidden"} in diagnostic["issues"]
    assert "private" not in str(result)


def test_foreign_compile_identity_has_its_own_reason():
    result = status.parse_compile_job(
        status.unknown_compile_job("b" * 32, "result_unavailable"), COMPILE_ID
    )
    assert result["reason"] == "job_identity_mismatch"
    assert result["compile_id"] == COMPILE_ID
    assert result["session_id"] is None
    assert "b" * 32 not in str(result)


@pytest.mark.parametrize(
    "payload,reason",
    [
        (None, "tracking_unsupported"),
        ([], "invalid_evidence"),
        ({"schema_version": "future"}, "unsupported_schema"),
    ],
)
def test_absent_malformed_and_future_compile_contracts_differ(payload, reason):
    assert status.parse_compile_job(payload, COMPILE_ID)["reason"] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"success": False, "error": "Unknown command: get_compile_job"}, "tracking_unsupported"),
        (
            {
                "success": False,
                "error": "Unity has no tool or resource named 'get_compile_job'. If the MCP server "
                "or CLI is newer than the MCP for Unity package in this project, update the package.",
            },
            "tracking_unsupported",
        ),
        (
            {"success": False, "error": "Unity has no tool or resource named 'another_command'."},
            "observation_rejected",
        ),
        ({"success": False, "error": "disconnected"}, "observation_unavailable"),
        ({"success": False, "code": "forbidden", "error": "private"}, "observation_rejected"),
        ({"success": True, "data": []}, "invalid_evidence"),
        ([], "invalid_evidence"),
    ],
)
async def test_native_read_failures_do_not_masquerade_as_unsupported(monkeypatch, payload, reason):
    send = AsyncMock(return_value=payload)
    monkeypatch.setattr(status.unity_transport, "send_job_observation", send)
    result = await status.read_compile_job(INSTANCE, COMPILE_ID)
    assert result["reason"] == reason
    assert result["outcome"] == "unknown"
    assert "private" not in str(result)
    assert send.call_args.args[1:] == (INSTANCE, "get_compile_job", {"compile_id": COMPILE_ID})
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_lost_compile_result_can_observe_source_once_without_restarting_job(monkeypatch):
    value = status.unknown_compile_job(COMPILE_ID, "result_unavailable")
    send = AsyncMock(
        return_value={
            "success": True,
            "data": value,
            "source_freshness": {
                "schema": "unity-mcp/source_freshness@1",
                "status": "incomplete",
                "reason": "reload_requires_observation",
            },
        }
    )
    monkeypatch.setattr(status.unity_transport, "send_job_observation", send)
    monkeypatch.setattr(status, "get_unity_instance_from_context", AsyncMock(return_value=INSTANCE))
    result = await status.get_compile_job(None, COMPILE_ID)
    assert result.follow_up["next_call"] == {
        "tool": "get_compile_job",
        "arguments": {"compile_id": COMPILE_ID, "unity_instance": INSTANCE, "observe_source": True},
    }
    observed = await status.get_compile_job(None, COMPILE_ID, observe_source=True)
    assert observed.data["outcome"] == "unknown"
    assert "next_call" not in observed.follow_up
    assert send.await_count == 2
    assert all(call.args[2] == "get_compile_job" for call in send.call_args_list)


@pytest.mark.parametrize(
    "reason",
    ["invalid_evidence", "unsupported_schema", "job_identity_mismatch", "observation_rejected"],
)
def test_invalid_or_rejected_evidence_needs_inspection_instead_of_automatic_repetition(reason):
    follow_up = status.compile_follow_up(status.unknown_compile_job(COMPILE_ID, reason), INSTANCE)
    assert "next_call" not in follow_up
    assert follow_up["action"] != "inspect_evidence"


def test_source_validation_reports_known_fields_and_bounds_failures():
    result = parse_freshness(
        {
            "schema": "unity-mcp/source_freshness@1",
            "loaded_assemblies": [{"name": "secret", "loaded_mvid": "private"}] * 20,
        }
    )
    assert result.reason == "invalid_evidence"
    diagnostic = result.model_dump()["evidence_diagnostics"]
    assert diagnostic["code"] == "validation_failed"
    assert (
        diagnostic["issues"]
        == [{"field": "loaded_assemblies", "code": "string_pattern_mismatch"}] * 8
    )
    assert diagnostic["truncated"] is True
    assert "private" not in str(result)
    assert "secret" not in str(result)


def test_source_schema_and_size_failures_are_distinguishable():
    future = parse_freshness({"schema": "future"}).model_dump()
    oversized = parse_freshness(
        {"schema": "unity-mcp/source_freshness@1", "unknown": "x" * (256 * 1024)}
    ).model_dump()
    assert future["evidence_diagnostics"]["code"] == "unsupported_schema"
    assert oversized["evidence_diagnostics"]["code"] == "payload_limit_exceeded"


def test_peer_cannot_supply_server_validation_diagnostics():
    value = status.unknown_compile_job(COMPILE_ID, "result_unavailable")
    value["evidence_diagnostics"] = {"code": "private"}
    assert "evidence_diagnostics" not in status.parse_compile_job(value, COMPILE_ID)
    source = parse_freshness(
        {"schema": "unity-mcp/source_freshness@1", "evidence_diagnostics": {"code": "private"}}
    )
    assert "evidence_diagnostics" not in source.model_dump()


def test_observed_source_does_not_schedule_another_observation():
    source = parse_freshness(
        {"schema": "unity-mcp/source_freshness@1", "status": "changed", "reason": "inputs_changed"}
    )
    job = status.unknown_compile_job(COMPILE_ID, "result_unavailable")
    assert "next_call" not in status.compile_follow_up(job, INSTANCE, source=source)


@pytest.mark.asyncio
async def test_refresh_preserves_the_observed_source_scope_in_follow_up(monkeypatch):
    from services.tools import refresh_unity as refresh

    async def send(_sender, _instance, _command, params, **_options):
        return {
            "success": True,
            "data": {
                "compilation": status.unknown_compile_job(
                    params["compile_id"], "result_unavailable"
                ),
                "source_freshness": {
                    "schema": "unity-mcp/source_freshness@1",
                    "status": "changed",
                    "reason": "inputs_changed",
                },
            },
        }

    monkeypatch.setattr(
        refresh, "get_unity_instance_from_context", AsyncMock(return_value=INSTANCE)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    result = await refresh.refresh_unity(None, compile="request", wait_for_ready=False)
    assert "next_call" not in result.data["follow_up"]
