"""Comparison fails closed on incompatible conditions and hidden regressions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.bench_transport_compare import Capture, compare
from tools.bench_transport_resource import normalize_resource
from tools.bench_transport_report import fingerprint


@pytest.fixture
def report() -> Capture:
    """Small coherent report exercises comparison guards without process timing."""
    clean = {"shared_read_reserved_bytes": 0, "capacity_bytes": 64000000,
             "stdio_delivery_observer_available": True}
    status = {"registrations": 2, "commands": {"read_console": 7, "get_editor_state": 2, "get_test_job": 2},
              "accounting": clean}
    rows = []
    for mode in ("stdio", "http"):
        commands = {**status["commands"], **({"ping": 9} if mode == "http" else {})}
        lifecycle = {"cancel_requested": True, "after_cancel": status,
                     "after_reconnect": {**status, "commands": commands}}
        if mode == "http":
            lifecycle["partial_cancel"] = {"held": {**clean, "assembler_buffer_bytes": 262144, "hub_reserved_bytes": 1314816},
                                           "after_cancel": status, "after_late_chunks": {**status,
                                               "commands": {**commands, "read_console": commands["read_console"] + 1, "ping": commands["ping"] + 1}},
                                           "receiver_final_chunk_processed": True}
        correlations = [f"{w}:{phase}" for w in ("small", "state", "large", "job") for phase in ("cold", "warm:0")]
        correlations += ["small:after_cancel", "small:after_reconnect"]
        rows.append({"mode": mode, "cold_launch_to_initialized_ms": 100,
            "cold_first_calls": {w: 10 for w in ("small", "state", "large", "job")},
            "observations": [{"correlation": c, "workload": c.split(":")[0], "phase": "warm" if "warm" in c else "cold",
                              "client_total_ms": 10, "output_sha256": c.split(":")[0], "output_bytes": 300000}
                             for c in correlations],
            "warmed": {w: {"samples": 1, "output_sha256": [w], "output_bytes": 300000,
                            "stages_ms": {"client_total_ms": {"p50": 10, "p95": 11, "p99": 12}}}
                       for w in ("small", "state", "large", "job")},
            "lifecycle": lifecycle, "tool_schemas": [{"name": "read_console", "inputSchema": {}}],
            "fixture_metadata": {"product_imports": {"main": "main.py"}, "child_packages": {"mcp": "owned"},
                                 "readiness_strategy": "per_call", "runner": "UnityMCP", "retention_middleware": True},
            "client_rpc_counts": {"initialize": 1, "tools/list": 1, "tools/call": 17 + (mode == "http") * 7,
                                  "public_tools/call": 11 + (mode == "http")},
            "native_child_interpreter": "owned-python", "text_structured_parity": True})
    return Capture.model_validate({"schema_id": "unity-mcp-transport-bench-v2", "runtime": {"packages": {"mcp": "owned"}},
        "options": {"samples": 1, "warmup": 0, "large_bytes": 262144, "work_ms": 0, "concurrency": 1, "cohort_gate": False},
        "source": {"asserted_product_revision": "caller-label", "unchanged_during_measurement": True,
                   "sha256_before": {"harness:owned.py": "a", "Server/src/main.py": "b"},
                   "sha256_after": {"harness:owned.py": "a", "Server/src/main.py": "b"}},
        "peer_profile": {"http_capabilities": ["large_result_v1"]}, "output_equivalent": True, "results": rows})


def test_same_protocol_rows_when_product_source_fingerprint_changes(report: Capture) -> None:
    # Given: common harness/runtime and different selected product bytes.
    candidate = report.model_dump(mode="json")
    candidate["source"]["sha256_before"]["Server/src/main.py"] = "candidate"
    candidate["source"]["sha256_after"]["Server/src/main.py"] = "candidate"
    # When: compare same protocols across the product versions.
    result = compare(report, Capture.model_validate(candidate))
    # Then: eight workload rows remain valid; product fingerprints need not match.
    assert result["semantic_parity"] is True
    assert len(result["same_protocol_rows"]) == 8


@pytest.mark.parametrize("mutation,reason", [
    ("runtime", "Runtime"), ("harness", "harness"), ("options", "options"),
    ("output", "outputs"), ("schema", "schemas"), ("readiness", "readiness"),
    ("retained", "leaked"), ("partial", "positive"), ("observations", "missing"),
    ("partial_rpc", "Partial-transfer"), ("receiver", "receiver_final_chunk_processed"),
])
def test_comparison_rejects_when_contract_regresses(report: Capture, mutation: str, reason: str) -> None:
    # Given: one baseline report and one altered candidate contract.
    candidate = json.loads(report.model_dump_json())
    match mutation:
        case "runtime": candidate["runtime"]["platform"] = "different"
        case "harness":
            candidate["source"]["sha256_before"]["harness:owned.py"] = "different"
            candidate["source"]["sha256_after"]["harness:owned.py"] = "different"
        case "options": candidate["options"]["work_ms"] = 2
        case "output": candidate["results"][0]["observations"][0]["output_sha256"] = "drift"
        case "schema": candidate["results"][0]["tool_schemas"][0]["inputSchema"] = {"type": "string"}
        case "readiness": candidate["results"][1]["lifecycle"]["after_reconnect"]["commands"]["ping"] = 10
        case "retained": candidate["results"][0]["lifecycle"]["after_cancel"]["accounting"]["shared_read_reserved_bytes"] = 1
        case "partial": candidate["results"][1]["lifecycle"]["partial_cancel"]["held"]["assembler_buffer_bytes"] = 0
        case "partial_rpc": candidate["results"][1]["lifecycle"]["partial_cancel"]["after_late_chunks"]["commands"]["read_console"] += 1
        case "receiver": candidate["results"][1]["lifecycle"]["partial_cancel"]["receiver_final_chunk_processed"] = False
        case "observations": candidate["results"][0]["observations"].pop()
        case other: raise AssertionError(other)
    # When/Then: the altered contract fails before any performance ratio is accepted.
    with pytest.raises(ValueError, match=reason):
        compare(report, Capture.model_validate(candidate))


@pytest.mark.parametrize("sequence,equal", [(1, True), (2, False)])
def test_resource_parity_when_only_declared_time_fields_change(sequence: int, equal: bool) -> None:
    # Given: actual resource shape with two volatile timing fields.
    first = {"success": True, "data": {"observed_at_unix_ms": 100, "sequence": 1,
                                       "staleness": {"age_ms": 0, "is_stale": False}}}
    second = {"success": True, "data": {"observed_at_unix_ms": 200, "sequence": sequence,
                                        "staleness": {"age_ms": 5, "is_stale": False}}}
    # When: apply the explicit normalization policy to copies of both raw values.
    same = fingerprint(normalize_resource(first)) == fingerprint(normalize_resource(second))
    # Then: semantic hashes match and raw evidence retains both original integers.
    assert same is equal and first["data"]["observed_at_unix_ms"] == 100


@pytest.mark.parametrize("observed,age", [(False, 0), (0, 0), (10, -1), (10, True)])
def test_resource_parity_rejects_when_time_fields_are_invalid(observed, age) -> None:
    # Given: malformed volatile fields must not be hidden by normalization.
    raw = {"success": True, "data": {"observed_at_unix_ms": observed, "staleness": {"age_ms": age}}}
    # When/Then: normalization rejects before any semantic comparison.
    with pytest.raises(ValueError, match="timestamp"):
        normalize_resource(raw)
