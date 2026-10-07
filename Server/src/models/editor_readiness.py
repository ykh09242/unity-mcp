"""Native Play Mode readiness job contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class PlayReadinessJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    job_id: str
    status: Literal["running", "succeeded", "failed", "cancelled", "timed_out"]
    error: str | None = None
