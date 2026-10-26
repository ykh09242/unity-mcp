"""Native admission diagnostics must not erase or promote compiler evidence."""

import copy
import json

import pytest
from fastmcp import Client, FastMCP

from services.tools import get_compile_job as status


COMPILE_ID = "a" * 32
NATIVE_IDLE = {
    "scope": "owned_request_pipeline_events",
    "phase": "no_owned_reservation",
    "reason": None,
    "retryable": False,
    "recovery_action": None,
    "completion_requires": "matching_pipeline_finish_and_editor_quiescence",
}


def native_evidence(outcome):
    """Match GetCompileJob's ledger plus CoordinationStatus response shape."""
    return {
        "schema_version": "unity-mcp/compile_job@1",
        "compile_id": COMPILE_ID,
        "session_id": "b" * 32,
        "generation": 1,
        "candidate_generations": [1],
        "requested_at_unix_ms": 1,
        "started_at_unix_ms": 2,
        "finished_at_unix_ms": 3,
        "state": "finished",
        "outcome": outcome,
        "attribution": "observed_after_request",
        "collection_complete": True,
        "assembly_count": 1,
        "error_count": int(outcome == "failed"),
        "diagnostics": (
            [
                {
                    "severity": "error",
                    "assembly": "Fixture",
                    "file": "Assets/Fixture.cs",
                    "line": 1,
                    "column": 1,
                    "message": "Missing type",
                }
            ]
            if outcome == "failed"
            else []
        ),
        "native_coordination": copy.deepcopy(NATIVE_IDLE),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("outcome", ["success", "failed"])
async def test_sdk_preserves_native_compiler_evidence_with_coordination(
    monkeypatch, protocol, outcome
):
    commands = []

    async def selected(ctx):
        return "CompileFixture@one"

    async def send(fn, instance, command, params, **kwargs):
        commands.append(command)
        assert instance == "CompileFixture@one"
        assert params == {"compile_id": COMPILE_ID}
        return {"success": True, "data": native_evidence(outcome)}

    monkeypatch.setattr(status, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(status.unity_transport, "send_with_unity_instance", send)
    app = FastMCP("compile-coordination")
    app.tool(name="get_compile_job")(status.get_compile_job)
    async with Client(app, mode=protocol) as client:
        response = await client.call_tool("get_compile_job", {"compile_id": COMPILE_ID})
    result = json.loads(response.content[0].text)["data"]

    assert commands == ["get_compile_job"]
    assert result["outcome"] == outcome
    assert result["generation"] == 1
    assert result["error_count"] == int(outcome == "failed")
    assert len(result["diagnostics"]) == int(outcome == "failed")
    assert result["native_coordination"] == NATIVE_IDLE


@pytest.mark.parametrize(
    "phase",
    ["pending", "started", "finished", "recovery_required", "other_owned_reservation"],
)
def test_coordination_is_diagnostic_and_cannot_certify_compiler_success(phase):
    job = native_evidence("success")
    job.update(outcome="unknown", attribution="unconfirmed", collection_complete=False)
    coordination = job["native_coordination"]
    coordination.update(
        phase=phase,
        reason="recovery_required" if phase == "recovery_required" else "native_job_active",
        retryable=phase != "recovery_required",
        recovery_action=(
            "inspect_native_work_then_restart_editor" if phase == "recovery_required" else None
        ),
    )

    result = status.parse_compile_job(job, COMPILE_ID)

    assert result["outcome"] == "unknown"
    assert result["attribution"] == "unconfirmed"
    assert result["native_coordination"] == coordination


def test_old_peer_without_coordination_still_preserves_outcome():
    job = native_evidence("success")
    del job["native_coordination"]
    result = status.parse_compile_job(job, COMPILE_ID)
    assert result["outcome"] == "success"
    assert result.get("native_coordination") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "x" * 4096),
        ("reason", "C:/private/exception.txt"),
        ("retryable", "true"),
        ("recovery_action", "execute_arbitrary_code"),
        ("extra", "unexpected"),
    ],
)
def test_malformed_coordination_is_not_accepted_as_evidence(field, value):
    job = native_evidence("success")
    job["native_coordination"][field] = value
    result = status.parse_compile_job(job, COMPILE_ID)
    assert result["outcome"] == "unknown"
    assert result["reason"] == "tracking_unsupported"


def test_valid_coordination_does_not_weaken_ledger_evidence_validation():
    job = native_evidence("success")
    job["collection_complete"] = False
    result = status.parse_compile_job(job, COMPILE_ID)
    assert result["outcome"] == "unknown"
    assert result["reason"] == "tracking_unsupported"
