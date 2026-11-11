import pytest

from models.source_freshness import parse_freshness


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
    assert parse_freshness(current()).status == "observed_current"


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
async def test_public_compile_inspection_preserves_the_separate_freshness_envelope(monkeypatch):
    from unittest.mock import AsyncMock
    from services.tools import get_compile_job as tool

    monkeypatch.setattr(tool, "get_unity_instance_from_context", AsyncMock(return_value="exact"))
    sender = AsyncMock(
        return_value={
            "success": True,
            "data": tool.unknown_compile_job("a" * 32, "not_found"),
            "source_freshness": current(),
        }
    )
    monkeypatch.setattr(tool.unity_transport, "send_job_observation", sender)
    reply = await tool.get_compile_job(object(), "a" * 32, observe_source=True)
    assert reply.data["compile_id"] == "a" * 32
    assert reply.source_freshness["source_token"] == "c" * 64
    assert "source_freshness" not in reply.data
    assert sender.await_args.args[3]["observe_source"] is True
