"""Benchmark reports must not hide drift, missing stages, or lifecycle leaks."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import anyio

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS.parent))
from tools.bench_transport_report import Observation, attach_stages, distribution, equivalent_outputs
from tools.bench_transport_process import cleanup_process


@dataclass
class SlowChild:
    """Mutable process fake models a child that ignores terminate until kill."""
    returncode: int | None = None
    events: list[str] = field(default_factory=list)

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self.returncode = -9

    async def wait(self) -> int:
        self.events.append("wait")
        if self.returncode is None:
            await anyio.sleep_forever()
        return self.returncode

    async def aclose(self) -> None:
        self.events.append("close")


@pytest.mark.asyncio
async def test_cleanup_escalates_when_caller_is_already_cancelled() -> None:
    # Given: a slow owned child and a cancellation already active on its caller.
    child = SlowChild()
    # When: cleanup runs inside that cancelled scope.
    with anyio.CancelScope() as scope:
        scope.cancel()
        await cleanup_process(child, grace_seconds=0.001)
    # Then: graceful timeout still reaches kill, reap and close under shielding.
    assert child.events == ["terminate", "wait", "kill", "wait", "close"]


@pytest.mark.asyncio
async def test_cleanup_closes_when_owned_real_child_already_exited() -> None:
    # Given: a real subprocess already reaped through normal completion.
    process = await anyio.open_process([sys.executable, "-c", "pass"])
    await process.wait()
    # When: the same cancellation-safe cleanup takes its exited-child path.
    await cleanup_process(process)
    # Then: owned stdout/stderr handles are closed as well as the child reaped.
    assert process.returncode == 0
    with pytest.raises(anyio.ClosedResourceError):
        await process.stdout.receive()


def test_percentiles_when_tail_contains_an_outlier() -> None:
    # Given: ninety-nine fast samples and a single slow sample.
    samples = [1.0] * 99 + [500.0]
    # When: compute nearest-rank percentiles.
    result = distribution(samples)
    # Then: p99 excludes only the slowest 1%, while max retains its evidence.
    assert result == {"count": 100, "min": 1.0, "max": 500.0, "p50": 1.0, "p95": 1.0, "p99": 1.0}


def test_equivalence_rejects_drift_when_later_samples_match() -> None:
    # Given: an earlier mismatch that would be hidden by a last-value map.
    original = Observation(correlation="small:0", workload="small", phase="warm",
                           client_total_ms=10, output_sha256="a", output_bytes=1)
    drift = original.model_copy(update={"correlation": "small:1", "output_sha256": "b"})
    # When: compare the full series, whose final values are equal.
    equivalent = equivalent_outputs([[original, drift], [drift, drift]])
    # Then: the earlier mismatch fails output equivalence.
    assert equivalent is False


def test_stages_include_readiness_when_peer_uses_extra_probe(tmp_path: Path) -> None:
    # Given: one public call, its prerequisite ping, and a legacy admission queue.
    events = [{"correlation": "small:0", "queue_ms": 2, "synthetic_unity_work_ms": 3,
               "peer_serialization_ms": 1, "response_bytes": 80},
              {"correlation": "small:0:readiness", "queue_ms": 1, "synthetic_unity_work_ms": 4,
               "peer_serialization_ms": 2, "response_bytes": 40}]
    (tmp_path / "peer.jsonl").write_text("\n".join(json.dumps(row) for row in events), encoding="utf-8")
    (tmp_path / "queue.jsonl").write_text(json.dumps({"correlation": "small:0", "queue_ms": 1}), encoding="utf-8")
    observation = Observation(correlation="small:0", workload="small", phase="warm",
                              client_total_ms=20, output_sha256="a", output_bytes=1)
    # When: join all stages of the call.
    result = attach_stages([observation], tmp_path)[0]
    # Then: stage accounting includes the extra work and sums to client latency.
    assert (result.queue_ms, result.synthetic_unity_work_ms, result.peer_serialization_ms,
            result.wire_response_framework_ms, result.peer_response_bytes) == (4, 7, 3, 6, 120)


def test_missing_stage_rejected_when_peer_has_no_correlated_observation(tmp_path: Path) -> None:
    # Given: a client observation whose peer evidence is absent.
    (tmp_path / "peer.jsonl").write_text("", encoding="utf-8")
    observation = Observation(correlation="small:0", workload="small", phase="warm",
                              client_total_ms=20, output_sha256="a", output_bytes=1)
    # When/Then: a missing stage cannot become fabricated zero-cost work.
    with pytest.raises(ValueError, match="Missing peer observation"):
        attach_stages([observation], tmp_path)


def test_actual_transports_when_owned_peers_complete_cancel_and_reconnect(tmp_path: Path) -> None:
    # Given: the installed SDK/server runtime and isolated owned fixture endpoints.
    output = tmp_path / "measurement.json"
    command = [sys.executable, str(TOOLS / "bench_transport.py"), "--output", str(output),
               "--samples", "2", "--warmup", "0", "--large-bytes", "262144", "--concurrency", "2"]
    # When: execute the actual CLI over both product routing paths.
    completed = subprocess.run(command, cwd=TOOLS.parent, capture_output=True, text=True, timeout=40, check=False)
    # Then: matched output, observed peer work, and drained state survive replacement.
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["output_equivalent"] is True
    assert report["source"]["unchanged_during_measurement"] is True
    for result in report["results"]:
        assert set(result["warmed"]) == {"small", "state", "large", "job"}
        assert len(result["observations"]) == 14
        assert result["lifecycle"]["cancel_requested"] is True
        state = result["lifecycle"]["after_reconnect"]
        assert (state["pending"], state["retained"], state["registrations"]) == (0, 0, 2)
        assert state["commands"]["read_console"] == 9
        assert state["commands"]["get_editor_state"] == state["commands"]["get_test_job"] == 3
        assert result["warmed"]["large"]["output_bytes"] > 262144
