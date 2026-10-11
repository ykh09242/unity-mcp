"""Strict command boundary for Editor-owned scenario draft recordings."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.play_scenarios import JobId, ScenarioName

RecordingAction = Literal["start", "status", "stop", "cancel", "checkpoint"]
RecordingMaxEvents = Annotated[int, Field(strict=True, ge=1, le=31)]
RecordingDuration = Annotated[int, Field(strict=True, ge=1, le=600)]


class ScenarioRecordingCommand(BaseModel):
    """Stop explicitly saves a new draft; Unity owns capture, bounds and lifecycle."""

    model_config = ConfigDict(extra="forbid")

    action: RecordingAction
    name: ScenarioName | None = None
    recording_id: JobId | None = None
    max_events: RecordingMaxEvents | None = None
    duration_seconds: RecordingDuration | None = None
    checkpoint_name: ScenarioName | None = None

    @model_validator(mode="after")
    def validate_action(self):
        allowed = {
            "start": {"name", "max_events", "duration_seconds"},
            "status": {"recording_id"},
            "stop": {"recording_id"},
            "cancel": {"recording_id"},
            "checkpoint": {"recording_id", "checkpoint_name"},
        }[self.action]
        for key in type(self).model_fields.keys():
            if key != "action" and getattr(self, key) is not None and key not in allowed:
                raise ValueError(f"{key} is not permitted for {self.action}")
        if self.action == "start" and self.name is None:
            raise ValueError("name is required for start")
        if self.action in {"stop", "cancel", "checkpoint"} and self.recording_id is None:
            raise ValueError(f"recording_id is required for {self.action}")
        if self.action == "checkpoint" and self.checkpoint_name is None:
            raise ValueError("checkpoint_name is required for checkpoint")
        return self

    def wire_parameters(self) -> dict:
        parameters = self.model_dump(exclude_none=True)
        if self.action == "start":
            parameters.setdefault("max_events", 31)
            parameters.setdefault("duration_seconds", 300)
        return parameters
