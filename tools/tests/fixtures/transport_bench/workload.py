"""Deterministic owned workloads shared by transport experiments."""
from __future__ import annotations

from enum import StrEnum
from typing import assert_never

from pydantic import BaseModel, ConfigDict, JsonValue

SCHEMA_ID = "unity-mcp-transport-bench-v1"
INSTANCE = "OwnedBench@01234567"
PROJECT_HASH = "01234567"


class Workload(StrEnum):
    SMALL = "small"
    STATE = "state"
    LARGE = "large"
    JOB = "job"


class PeerRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    params: dict[str, JsonValue]
    id: str = ""

    @property
    def correlation(self) -> str:
        return str(self.params.get("filterText", self.params.get("job_id", self.params.get("benchCorrelation", "state:0"))))

    @property
    def workload(self) -> Workload:
        return Workload(self.correlation.split(":", 1)[0])


class PeerTiming(BaseModel):
    model_config = ConfigDict(frozen=True)
    correlation: str
    queue_ms: float
    synthetic_unity_work_ms: float
    peer_serialization_ms: float
    response_bytes: int


def make_result(workload: Workload, large_bytes: int) -> dict[str, JsonValue]:
    match workload:
        case Workload.SMALL:
            data: JsonValue = {"lines": [{"message": "owned benchmark", "type": "log"}]}
        case Workload.STATE:
            data = {"schema_version": "owned-bench-state@1", "scene": "OwnedScene",
                    "object_count": 100, "is_compiling": False,
                    "settings": {"batch_execute_max_commands": 25},
                    "objects": [{"name": f"Object{index}", "active": True} for index in range(100)]}
        case Workload.LARGE:
            data = {"lines": [{"message": "x" * large_bytes, "type": "log"}]}
        case Workload.JOB:
            data = {"job_id": "owned-job", "status": "succeeded",
                    "result": {"mode": "EditMode", "summary": {"passed": 10, "failed": 0,
                    "skipped": 0, "total": 10, "durationSeconds": 0.012, "resultState": "Passed"}}}
        case unreachable:
            assert_never(unreachable)
    return {"success": True, "data": data}
