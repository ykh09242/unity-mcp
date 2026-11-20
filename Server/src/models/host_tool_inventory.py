"""Bounded exact-name evidence reported by the caller about its current connection."""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

MAX_CANDIDATES: Final = 20
ToolName = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
HostToolNames = Annotated[list[ToolName], Field(max_length=512)]


class HostToolEvidence(BaseModel):
    """A host compares only these names against its own callable inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[ToolName, ...] = Field(
        min_length=1, max_length=MAX_CANDIDATES, description="Exact MCP names being checked."
    )
    present: tuple[ToolName, ...] = Field(
        max_length=MAX_CANDIDATES, description="Candidate names observed on valid pages."
    )
    complete: StrictBool = Field(
        description="True only after reading the last page on the same unchanged connection and target."
    )

    @model_validator(mode="after")
    def validate_names(self) -> "HostToolEvidence":
        candidates = set(self.candidates)
        present = set(self.present)
        if (
            len(candidates) != len(self.candidates)
            or len(present) != len(self.present)
            or not present <= candidates
        ):
            raise ValueError("Use unique candidates and report present names only from candidates.")
        return self

    def received(self, name: str) -> bool | None:
        if name in self.present:
            return True
        if self.complete and name in self.candidates:
            return False
        return None
