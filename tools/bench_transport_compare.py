# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "anyio", "fastmcp>=4,<5", "mcp>=2,<3", "httpx2", "uvicorn", "websockets"]
# ///
# ─── How to run ───
# Server/.venv/Scripts/python.exe tools/bench_transport_compare.py --help
"""Compare selected product sources using one fixed owned transport harness."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bench_transport import Options, run
from tools.bench_transport_report import Observation, equivalent_outputs, fingerprint
from tools.bench_transport_resource import normalize_resource

class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)


class Profile(FrozenModel):
    diagnostic: Literal[False] = False
    order: Literal["stdio-first", "http-first"]
    samples: int
    warmup: int
    large_bytes: int
    work_ms: float
    concurrency: int
    cohort_gate: bool
    resource_contract: bool = False


class Source(FrozenModel):
    asserted_product_revision: str
    unchanged_during_measurement: bool
    sha256_before: dict[str, str]
    sha256_after: dict[str, str]


class AccountingSnapshot(FrozenModel):
    shared_read_reserved_bytes: int
    capacity_bytes: int
    hub_reserved_bytes: int = 0
    assembler_buffer_bytes: int = 0
    pending_commands: int = 0
    charged_stdio_deliveries: int = 0
    stdio_delivery_observer_available: bool | None = None

    def clean(self) -> bool:
        return all(value == 0 for value in (self.shared_read_reserved_bytes, self.hub_reserved_bytes,
                                           self.assembler_buffer_bytes, self.pending_commands,
                                           self.charged_stdio_deliveries))


class Status(FrozenModel):
    commands: dict[str, int]
    registrations: int
    accounting: AccountingSnapshot


class Partial(FrozenModel):
    held: AccountingSnapshot
    after_cancel: Status
    after_late_chunks: Status
    receiver_final_chunk_processed: Literal[True]


class Lifecycle(FrozenModel):
    cancel_requested: bool
    after_cancel: Status
    after_reconnect: Status
    partial_cancel: Partial | None = None


class Metadata(FrozenModel):
    product_imports: dict[str, str]
    child_packages: dict[str, str]
    readiness_strategy: Literal["inflight_shared", "per_call"]
    ordinary_resource_strategy: Literal["inflight_shared", "per_call"] = "per_call"
    readiness_private_workloads: list[Literal["state"]] = []
    runner: Literal["UnityMCP"]
    retention_middleware: Literal[True]


class Quantiles(FrozenModel):
    p50: float
    p95: float
    p99: float


class WorkloadSummary(FrozenModel):
    output_sha256: list[str]
    output_bytes: int
    samples: int
    stages_ms: dict[str, Quantiles]


class ResourceCohort(FrozenModel):
    mode: Literal["ordinary", "authoritative"]
    before: Status
    held: AccountingSnapshot
    after: Status
    raw_results: list[JsonValue]
    normalized_sha256: list[str]
    normalization_paths: list[str]


class ModeCapture(FrozenModel):
    mode: Literal["stdio", "http"]
    cold_launch_to_initialized_ms: float
    cold_first_calls: dict[str, float]
    warmed: dict[str, WorkloadSummary]
    observations: list[Observation]
    lifecycle: Lifecycle
    tool_schemas: list[dict[str, JsonValue]]
    fixture_metadata: Metadata
    client_rpc_counts: dict[str, int]
    text_structured_parity: Literal[True]
    native_child_interpreter: str
    resource_contract: list[ResourceCohort] | None = None


def check_resources(row: ModeCapture) -> None:
    """Require positive held delivery, exact producer calls and explicit normalization."""
    cohorts = row.resource_contract
    if cohorts is None or [cohort.mode for cohort in cohorts] != ["ordinary", "authoritative"]:
        raise ValueError("Separate ordinary/authoritative resource cohorts are missing")
    for cohort in cohorts:
        shared = cohort.mode == "ordinary" and row.fixture_metadata.ordinary_resource_strategy == "inflight_shared"
        expected = 1 if shared else 2
        before, after = cohort.before.commands, cohort.after.commands
        delta = {name: after.get(name, 0) - before.get(name, 0) for name in set(before) | set(after)}
        delta = {name: count for name, count in delta.items() if count}
        if delta != {"get_editor_state": expected, "ping": expected}:
            raise ValueError("Resource source/readiness RPC contract changed")
        if (cohort.held.hub_reserved_bytes + cohort.held.shared_read_reserved_bytes <= 0
                or cohort.held.hub_reserved_bytes > cohort.held.capacity_bytes or not cohort.after.accounting.clean()):
            raise ValueError("Resource held-body ownership is unobserved, unbounded or leaked")
        if cohort.normalization_paths != ["data.observed_at_unix_ms", "data.staleness.age_ms"]:
            raise ValueError("Resource parity normalized additional fields")
        hashes = [fingerprint(normalize_resource(raw))[0] for raw in cohort.raw_results]
        if len(hashes) != 2 or len(set(hashes)) != 1 or hashes != cohort.normalized_sha256:
            raise ValueError("Raw resource results do not match normalized semantic hashes")


class Capture(FrozenModel):
    data_kind: Literal["natural"] = "natural"
    schema_id: Literal["unity-mcp-transport-bench-v2"]
    runtime: dict[str, JsonValue]
    options: Profile
    source: Source
    peer_profile: dict[str, JsonValue]
    output_equivalent: Literal[True]
    results: list[ModeCapture]


def check_contract(capture: Capture) -> None:
    """Reject missing work, extra RPCs and undrained owned reservations."""
    if not capture.source.unchanged_during_measurement or capture.source.sha256_before != capture.source.sha256_after:
        raise ValueError("Product or common harness changed during capture")
    if {row.mode for row in capture.results} != {"stdio", "http"} or len(capture.results) != 2:
        raise ValueError("Capture requires exactly one stdio and HTTP result")
    expected_order = ["stdio", "http"] if capture.options.order == "stdio-first" else ["http", "stdio"]
    if [row.mode for row in capture.results] != expected_order:
        raise ValueError("Recorded transport order differs from the requested order")
    n = 1 + capture.options.warmup + capture.options.samples
    for row in capture.results:
        lifecycle = row.lifecycle
        if not lifecycle.cancel_requested or lifecycle.after_reconnect.registrations != 2:
            raise ValueError("Owned cancellation/reconnect was not exercised")
        for status in (lifecycle.after_cancel, lifecycle.after_reconnect):
            if not status.accounting.clean():
                raise ValueError("Owned reservation or pending command leaked")
        if set(row.warmed) != {"small", "state", "large", "job"}:
            raise ValueError("Representative workloads are incomplete")
        if any(summary.samples != capture.options.samples for summary in row.warmed.values()):
            raise ValueError("Warmed sample counts differ from requested profile")
        expected_correlations = {f"{workload}:cold" for workload in row.warmed}
        expected_correlations.update(f"{workload}:warmup:{index}" for workload in row.warmed
                                     for index in range(capture.options.warmup))
        expected_correlations.update(f"{workload}:warm:{index}" for workload in row.warmed
                                     for index in range(capture.options.samples))
        expected_correlations.update(("small:after_cancel", "small:after_reconnect"))
        if ({item.correlation for item in row.observations} != expected_correlations
                or len(row.observations) != len(expected_correlations)):
            raise ValueError("Client observation phases are missing or duplicated")
        expected = {"read_console": 2 * n + 3, "get_editor_state": n, "get_test_job": n}
        commands = lifecycle.after_reconnect.commands
        if set(commands) - set(expected) - {"ping"}:
            raise ValueError("Unexpected peer RPC was observed")
        if {name: commands.get(name, 0) for name in expected} != expected:
            raise ValueError("Public peer RPC counts changed")
        readiness = commands.get("ping", 0)
        if row.mode == "stdio":
            if readiness or not lifecycle.after_reconnect.accounting.stdio_delivery_observer_available:
                raise ValueError("Stdio readiness or actual delivery observer contract failed")
        else:
            upper = 3 * n + 3
            if capture.options.concurrency == 1 or row.fixture_metadata.readiness_strategy == "per_call":
                lower = upper
            else:
                private = len(row.fixture_metadata.readiness_private_workloads)
                lower = private * n + (3 - private) * (1 + capture.options.warmup + (capture.options.samples + capture.options.concurrency - 1)
                             // capture.options.concurrency) + 3
            if not lower <= readiness <= upper:
                raise ValueError("HTTP readiness RPC budget changed")
            if capture.options.cohort_gate and readiness != lower:
                raise ValueError("Cohort-gated readiness did not use the declared strategy")
            partial = lifecycle.partial_cancel
            if partial is None or not 0 < partial.held.assembler_buffer_bytes <= partial.held.hub_reserved_bytes <= partial.held.capacity_bytes:
                raise ValueError("Partial transfer lacks positive bounded actual reservation evidence")
            if not partial.after_cancel.accounting.clean() or not partial.after_late_chunks.accounting.clean():
                raise ValueError("Partial/late result reservations leaked")
            late = partial.after_late_chunks.commands
            delta = {name: late.get(name, 0) - commands.get(name, 0) for name in set(late) | set(commands)}
            if {name: count for name, count in delta.items() if count} != {"read_console": 1, "ping": 1}:
                raise ValueError("Partial-transfer peer RPC counts changed")
        counts = row.client_rpc_counts
        if counts.get("initialize") != 1 or counts.get("tools/list") != 1 or counts.get("public_tools/call") != 4 * n + 3 + (row.mode == "http"):
            raise ValueError("MCP initialization/public call counts changed")
        expected_tool_calls = 4 * n + 9 + (7 if row.mode == "http" else 0)
        if capture.options.resource_contract and row.mode == "http":
            expected_tool_calls += 10
        if counts.get("tools/call") != expected_tool_calls:
            raise ValueError("Explicit MCP control/public RPC count changed")
        if row.fixture_metadata.child_packages != capture.runtime.get("packages"):
            raise ValueError("Child dependencies differ from the pinned parent runtime")
        if not row.fixture_metadata.product_imports or "main" not in row.fixture_metadata.product_imports:
            raise ValueError("Actual selected product imports were not observed")
        if capture.options.resource_contract and row.mode == "http":
            if row.client_rpc_counts.get("resources/read") != 4:
                raise ValueError("Real MCP resource read count changed")
            check_resources(row)
        elif row.resource_contract is not None:
            raise ValueError("Resource contract unexpectedly mixed into latency capture")


def compare(baseline: Capture, candidate: Capture) -> dict[str, JsonValue]:
    """Compare same-protocol rows only after compatibility and semantic checks."""
    if baseline.options.order != candidate.options.order:
        raise ValueError("Transport order differs between baseline and candidate")
    check_contract(baseline)
    check_contract(candidate)
    if baseline.runtime != candidate.runtime or baseline.options != candidate.options or baseline.peer_profile != candidate.peer_profile:
        raise ValueError("Runtime, workload options or peer protocol profiles are incompatible")
    harnesses = [{name: sha for name, sha in report.source.sha256_before.items() if name.startswith("harness:")}
                 for report in (baseline, candidate)]
    if not harnesses[0] or harnesses[0] != harnesses[1]:
        raise ValueError("Common fixed harness fingerprints differ or are absent")
    rows = []
    controls = []
    for mode in ("stdio", "http"):
        left = next(row for row in baseline.results if row.mode == mode)
        right = next(row for row in candidate.results if row.mode == mode)
        for field in ("readiness_strategy", "ordinary_resource_strategy"):
            if getattr(left.fixture_metadata, field) == "inflight_shared" and getattr(right.fixture_metadata, field) == "per_call":
                raise ValueError(f"Candidate downgraded {field} sharing strategy")
        if (left.native_child_interpreter != right.native_child_interpreter
                or left.client_rpc_counts != right.client_rpc_counts
                or fingerprint(sorted(left.tool_schemas, key=lambda item: str(item["name"])))
                != fingerprint(sorted(right.tool_schemas, key=lambda item: str(item["name"])))):
            raise ValueError("Native interpreter, MCP call counts or advertised tool schemas differ")
        if not equivalent_outputs([left.observations, right.observations]):
            raise ValueError("Baseline/candidate decoded outputs differ")
        if left.resource_contract is not None and right.resource_contract is not None:
            if [cohort.normalized_sha256 for cohort in left.resource_contract] != [cohort.normalized_sha256 for cohort in right.resource_contract]:
                raise ValueError("Baseline/candidate resource semantics differ")
        if (baseline.options.cohort_gate
                and right.lifecycle.after_reconnect.commands.get("ping", 0) > left.lifecycle.after_reconnect.commands.get("ping", 0)):
            raise ValueError("Candidate increased readiness RPC count")
        controls.append({"mode": mode,
            "baseline_initialize_ms": left.cold_launch_to_initialized_ms, "candidate_initialize_ms": right.cold_launch_to_initialized_ms,
            "baseline_first_calls_ms": left.cold_first_calls, "candidate_first_calls_ms": right.cold_first_calls,
            "baseline_explicit_client_rpc_counts": left.client_rpc_counts, "candidate_explicit_client_rpc_counts": right.client_rpc_counts,
            "baseline_peer_rpc_counts": left.lifecycle.after_reconnect.commands, "candidate_peer_rpc_counts": right.lifecycle.after_reconnect.commands,
            "baseline_cleanup": left.lifecycle.model_dump(mode="json"), "candidate_cleanup": right.lifecycle.model_dump(mode="json"),
            "baseline_resource_contract": [cohort.model_dump(mode="json") for cohort in left.resource_contract] if left.resource_contract else None,
            "candidate_resource_contract": [cohort.model_dump(mode="json") for cohort in right.resource_contract] if right.resource_contract else None})
        for workload in ("small", "state", "large", "job"):
            before, after = left.warmed[workload], right.warmed[workload]
            rows.append({"mode": mode, "workload": workload,
                "baseline_client_ms": before.stages_ms["client_total_ms"].model_dump(mode="json"),
                "candidate_client_ms": after.stages_ms["client_total_ms"].model_dump(mode="json"),
                "candidate_over_baseline_p50": after.stages_ms["client_total_ms"].p50 / before.stages_ms["client_total_ms"].p50})
    return {"semantic_parity": True, "same_protocol_rows": rows, "initialization_counts_cleanup": controls,
            "readiness_comparison_policy": "Per-revision budgets; cross-revision nonincrease only for cohort-gated captures; declared sharing downgrades rejected.",
            "sharing_proof_scope": "Natural counts do not prove sharing; use separately paired cohort-gated and resource-contract captures to verify effective sharing.",
            "timing_scope": "Owned peer-emulated UnityMCP subset; main import and actual adapters, not full catalog/Editor/product speed",
            "timing_policy": "Descriptive ratios only; no wall-time/RSS CI threshold"}


async def execute(arguments: argparse.Namespace) -> None:
    """Run alternating baseline/candidate captures serially, preserving raw evidence."""
    pairs = []
    frozen_sources = {}
    control_sources = None
    schedule = []
    manifest = None
    if arguments.same_source_control and arguments.baseline_revision != arguments.candidate_revision:
        raise ValueError("Same-source control requires the same revision assertion for both roots")
    if arguments.baseline_manifest:
        path = arguments.baseline_manifest
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (Path(manifest["source_root"]).resolve() != arguments.baseline_root.resolve()
                or manifest["revision"] != arguments.baseline_revision):
            raise ValueError("Baseline manifest root/revision does not match selected snapshot")
        manifest = {"path": str(path.resolve()), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "claims": manifest, "verification": "Parent-provided archive provenance; raw product source fingerprints independently recorded"}
    for iteration in range(arguments.rounds):
        reports = {}
        labels = ("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")
        order = arguments.order
        if order == "alternating":
            order = "stdio-first" if iteration % 2 == 0 else "http-first"
        for label in labels:
            root = getattr(arguments, label + "_root").resolve()
            revision = getattr(arguments, label + "_revision")
            path = arguments.output.parent / f"{arguments.output.stem}-round{iteration + 1}-{label}.json"
            options = Options(output=path, samples=arguments.samples, warmup=arguments.warmup,
                large_bytes=arguments.large_bytes, work_ms=arguments.work_ms, concurrency=arguments.concurrency,
                cohort_gate=arguments.cohort_gate, order=order,
                resource_contract=arguments.resource_contract,
                product_root=root, product_revision=revision)
            reports[label] = Capture.model_validate(await run(options))
            sources = reports[label].source.sha256_before
            if arguments.same_source_control:
                if (not reports[label].source.unchanged_during_measurement
                        or sources != reports[label].source.sha256_after
                        or not any(name.startswith("Server/src/") for name in sources)
                        or not any(name.startswith("harness:") for name in sources)
                        or (control_sources is not None and sources != control_sources)):
                    raise ValueError("Same-source control product/common-harness maps differ or are incomplete")
                control_sources = sources
            if label in frozen_sources and frozen_sources[label] != sources:
                raise ValueError("Selected source or common harness changed between rounds")
            frozen_sources[label] = sources
            schedule.append({"round": iteration + 1, "label": label, "transport_order": order,
                             "raw_capture": str(path), "asserted_product_revision": revision})
        pairs.append(compare(reports["baseline"], reports["candidate"]))
    result = {"schema_id": "unity-mcp-product-comparison-v1", "rounds": pairs,
              "transport_order_policy": arguments.order, "capture_schedule": schedule,
              "comparison_kind": "same_source_control" if arguments.same_source_control else "product_comparison",
              "same_source_control_verified": arguments.same_source_control,
              "same_source_control_sha256": control_sources,
              "baseline_revision_label": arguments.baseline_revision, "candidate_revision_label": arguments.candidate_revision,
              "baseline_root": str(arguments.baseline_root.resolve()), "candidate_root": str(arguments.candidate_root.resolve()),
              "baseline_archive_manifest": manifest,
              "revision_provenance": "Caller-asserted labels; source bytes/fixed-harness fingerprints are recorded in raw captures. Labels are not Git verification."}
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Validate explicit caller labels and workload options before any capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--order", choices=("alternating", "stdio-first", "http-first"), default="alternating",
                        help="Transport order within every capture; revision order still alternates AB/BA")
    parser.add_argument("--same-source-control", action="store_true",
                        help="Require equal revision assertions and all actual product/common-harness source maps")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--large-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--work-ms", type=float, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--cohort-gate", action="store_true")
    parser.add_argument("--resource-contract", action="store_true")
    args = parser.parse_args(argv)
    if args.rounds < 1 or args.samples < 1 or args.warmup < 0 or args.concurrency < 1 or not 262144 <= args.large_bytes <= 8 * 1024 * 1024 or args.work_ms < 0:
        parser.error("Use positive rounds/samples/concurrency, nonnegative warmup/work-ms, and payload 256KiB..8MiB")
    return args


if __name__ == "__main__":
    anyio.run(execute, parse_args())
