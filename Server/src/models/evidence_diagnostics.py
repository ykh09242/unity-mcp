"""Bounded validation feedback containing only server-known field names and codes."""

from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EvidenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    field: str = Field(max_length=80, pattern=r"^[a-z_]+$|^\$$")
    code: str = Field(max_length=80, pattern=r"^[a-z_]+$")


class EvidenceDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    code: Literal[
        "validation_failed",
        "unsupported_schema",
        "payload_limit_exceeded",
        "invalid_shape",
        "invalid_encoding",
    ]
    issues: list[EvidenceIssue] = Field(default_factory=list, max_length=8)
    truncated: bool = False

    @classmethod
    def from_validation(
        cls, error: ValidationError, fields: Collection[str]
    ) -> "EvidenceDiagnostics":
        issues = []
        for issue in error.errors(include_url=False, include_context=False, include_input=False)[
            :8
        ]:
            location = issue["loc"]
            field = location[0] if location and location[0] in fields else "$"
            issues.append(EvidenceIssue(field=field, code=issue["type"]))
        return cls(code="validation_failed", issues=issues, truncated=error.error_count() > 8)
