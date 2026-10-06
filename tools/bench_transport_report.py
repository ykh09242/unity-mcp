"""Typed benchmark observations and conservative distribution summaries."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)
    correlation: str
    workload: str
    phase: str
    client_total_ms: float
    output_sha256: str
    output_bytes: int
    queue_ms: float = 0
    synthetic_unity_work_ms: float = 0
    peer_serialization_ms: float = 0
    wire_response_framework_ms: float = 0
    peer_response_bytes: int = 0


class PeerTiming(BaseModel):
    model_config = ConfigDict(frozen=True)
    correlation: str
    queue_ms: float
    synthetic_unity_work_ms: float
    peer_serialization_ms: float
    response_bytes: int


class QueueTiming(BaseModel):
    model_config = ConfigDict(frozen=True)
    correlation: str
    queue_ms: float


def fingerprint(value: JsonValue) -> tuple[str, int]:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def distribution(values: list[float]) -> dict[str, JsonValue]:
    if not values:
        raise ValueError("A distribution requires observations")
    ordered = sorted(values)
    return {"count": len(values), "min": ordered[0], "max": ordered[-1],
            **{f"p{p}": ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)] for p in (50, 95, 99)}}


def attach_stages(observations: list[Observation], directory: Path) -> list[Observation]:
    """Join owned side-channel timings; clocks are never subtracted across processes."""
    peer: dict[str, list[PeerTiming]] = defaultdict(list)
    for line in (directory / "peer.jsonl").read_text(encoding="utf-8").splitlines():
        row = PeerTiming.model_validate_json(line)
        peer[row.correlation.removesuffix(":readiness")].append(row)
    queues: dict[str, float] = defaultdict(float)
    queue_file = directory / "queue.jsonl"
    if queue_file.exists():
        for line in queue_file.read_text(encoding="utf-8").splitlines():
            row = QueueTiming.model_validate_json(line)
            queues[row.correlation] += row.queue_ms
    attached = []
    for item in observations:
        timings = peer[item.correlation]
        if not timings:
            raise ValueError(f"Missing peer observation: {item.correlation}")
        queue_ms = sum(timing.queue_ms for timing in timings) + queues[item.correlation]
        work_ms = sum(timing.synthetic_unity_work_ms for timing in timings)
        serialization_ms = sum(timing.peer_serialization_ms for timing in timings)
        residual = item.client_total_ms - queue_ms - work_ms - serialization_ms
        if residual < -0.1:
            raise ValueError("Overlapping or mismatched stage observations")
        attached.append(item.model_copy(update={"queue_ms": queue_ms,
                         "synthetic_unity_work_ms": work_ms,
                         "peer_serialization_ms": serialization_ms,
                         "wire_response_framework_ms": max(0.0, residual),
                         "peer_response_bytes": sum(timing.response_bytes for timing in timings)}))
    return attached


def summarize(observations: list[Observation]) -> dict[str, JsonValue]:
    fields = ("client_total_ms", "queue_ms", "synthetic_unity_work_ms", "peer_serialization_ms", "wire_response_framework_ms")
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for item in observations:
        if item.phase == "warm":
            grouped[item.workload].append(item)
    return {name: {"samples": len(items), "output_sha256": sorted({item.output_sha256 for item in items}),
                   "output_bytes": items[0].output_bytes,
                   "stages_ms": {field: distribution([getattr(item, field) for item in items]) for field in fields}}
            for name, items in grouped.items()}


def parse_output(text: str) -> JsonValue:
    value = TypeAdapter(JsonValue).validate_json(text)
    if isinstance(value, dict) and set(value) == {"result"}:
        return value["result"]
    return value


def equivalent_outputs(groups: list[list[Observation]]) -> bool:
    """Require every observed output for each workload to match across modes."""
    hashes: dict[str, set[str]] = defaultdict(set)
    workloads: list[set[str]] = []
    for group in groups:
        workloads.append({item.workload for item in group})
        for item in group:
            hashes[item.workload].add(item.output_sha256)
    return bool(groups) and all(names == workloads[0] for names in workloads) and all(len(values) == 1 for values in hashes.values())
