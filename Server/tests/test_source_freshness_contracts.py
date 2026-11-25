import pytest

from models.source_freshness import parse_freshness

SOURCE_BYTE_LIMIT = 256 * 1024 * 1024


def current():
    return {
        "schema": "unity-mcp/source_freshness@1",
        "status": "observed_current",
        "basis": "stable_inputs_compile_and_load_observed",
        "project_id": "a" * 64,
        "session_id": "b" * 32,
        "source_token": "c" * 64,
        "source_digest": "d" * 64,
        "context_digest": "e" * 64,
        "observed_at_unix_ms": 1,
        "compile_id": "f" * 32,
        "compile_generation": 1,
        "source_revision_confirmed": False,
        "reason": "compile_and_load_observed",
        "coverage": {
            "complete": True,
            "reasons": [],
            "file_count": 1,
            "input_bytes": 1,
            "atomic_compiler_input_provenance": False,
        },
        "loaded_assemblies": [{"name": "Game", "output_mvid": "1" * 32, "loaded_mvid": "1" * 32}],
    }


def test_complete_evidence_is_preserved():
    evidence = current()
    evidence["coverage"]["input_bytes"] = SOURCE_BYTE_LIMIT
    observation = parse_freshness(evidence)

    assert observation.status == "observed_current"
    assert observation.coverage.input_bytes == SOURCE_BYTE_LIMIT


@pytest.mark.parametrize(
    "status,input_bytes",
    [
        ("incomplete", SOURCE_BYTE_LIMIT + 1),
        ("pending", 268_485_099),
        ("incomplete", 268_489_073),
        ("incomplete", SOURCE_BYTE_LIMIT + 65_536),
    ],
)
def test_historical_buffer_overshoot_preserves_incomplete_native_evidence(status, input_bytes):
    evidence = current()
    reasons = ["excluded_compilation_input", "input_limit", "metadata_limit"]
    reason = "compilation_requested" if status == "pending" else "input_coverage_incomplete"
    evidence.update(status=status, reason=reason)
    evidence["coverage"].update(
        complete=False, reasons=reasons, file_count=10, input_bytes=input_bytes
    )

    observation = parse_freshness(evidence)

    assert observation.status == status
    assert observation.reason == evidence["reason"]
    assert observation.evidence_diagnostics is None
    assert observation.coverage.model_dump() == evidence["coverage"]
    assert observation.project_id == evidence["project_id"]
    assert observation.session_id == evidence["session_id"]
    assert observation.source_token == evidence["source_token"]
    assert observation.source_digest == evidence["source_digest"]
    assert observation.context_digest == evidence["context_digest"]


@pytest.mark.parametrize(
    "status,coverage_changes",
    [
        ("incomplete", {"input_bytes": SOURCE_BYTE_LIMIT + 65_537}),
        ("incomplete", {"input_bytes": -1}),
        ("incomplete", {"input_bytes": str(SOURCE_BYTE_LIMIT + 1)}),
        ("incomplete", {"input_bytes": float(SOURCE_BYTE_LIMIT + 1)}),
        ("incomplete", {"complete": True}),
        ("incomplete", {"reasons": ["metadata_limit"]}),
        ("observed_current", {"complete": True, "reasons": []}),
        ("changed", {}),
        ("compile_failed", {}),
        ("unavailable", {}),
    ],
)
def test_buffer_overshoot_outside_the_native_contract_is_rejected(status, coverage_changes):
    evidence = current()
    evidence["status"] = status
    evidence["coverage"].update(
        complete=False, reasons=["input_limit"], input_bytes=SOURCE_BYTE_LIMIT + 1
    )
    evidence["coverage"].update(coverage_changes)

    observation = parse_freshness(evidence)

    assert (observation.status, observation.reason) == ("unavailable", "invalid_evidence")


def test_buffer_overshoot_requires_explicit_incomplete_coverage():
    evidence = current()
    evidence["status"] = "incomplete"
    evidence["coverage"].update(reasons=["input_limit"], input_bytes=SOURCE_BYTE_LIMIT + 1)
    del evidence["coverage"]["complete"]

    observation = parse_freshness(evidence)

    assert (observation.status, observation.reason) == ("unavailable", "invalid_evidence")


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_token", None),
        ("compile_generation", None),
        ("source_revision_confirmed", True),
        ("loaded_assemblies", []),
    ],
)
def test_inconsistent_current_claim_is_not_trusted(field, value):
    evidence = current()
    evidence[field] = value
    assert parse_freshness(evidence).status == "unavailable"


def test_mismatched_assembly_and_partial_coverage_are_rejected():
    evidence = current()
    evidence["loaded_assemblies"][0]["loaded_mvid"] = "2" * 32
    assert parse_freshness(evidence).status == "unavailable"
    evidence = current()
    evidence["coverage"]["complete"] = False
    assert parse_freshness(evidence).status == "unavailable"


def test_legacy_peer_is_explicitly_unsupported():
    assert parse_freshness(None).reason == "unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["observed_current", "incomplete"])
async def test_compile_inspection_preserves_separate_freshness_evidence(monkeypatch, status):
    from unittest.mock import AsyncMock

    from services.tools import get_compile_job as tool

    evidence = current()
    if status == "incomplete":
        evidence.update(status=status, reason="input_coverage_incomplete")
        evidence["coverage"].update(
            complete=False, reasons=["input_limit", "metadata_limit"], input_bytes=268_489_073
        )
    monkeypatch.setattr(tool, "get_unity_instance_from_context", AsyncMock(return_value="exact"))
    sender = AsyncMock(
        return_value={
            "success": True,
            "data": tool.unknown_compile_job("a" * 32, "not_found"),
            "source_freshness": evidence,
        }
    )
    monkeypatch.setattr(tool.unity_transport, "send_job_observation", sender)
    reply = await tool.get_compile_job(object(), "a" * 32, observe_source=True)
    assert reply.data["compile_id"] == "a" * 32
    assert reply.source_freshness["status"] == status
    assert reply.source_freshness["source_token"] == evidence["source_token"]
    assert reply.source_freshness["coverage"] == evidence["coverage"]
    assert "source_freshness" not in reply.data
    assert sender.await_args.args[3]["observe_source"] is True
