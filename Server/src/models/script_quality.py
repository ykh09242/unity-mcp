"""Versioned, action-specific changed-script diagnostic inputs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QualityAction = Literal["capabilities", "capture_baseline", "analyze", "status", "cancel", "impact"]
QualityId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)]


class QualityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScriptSelection(QualityOptions):
    paths: (
        Annotated[
            list[Annotated[str, Field(min_length=1, max_length=4096)]],
            Field(min_length=1, max_length=256),
        ]
        | None
    ) = None
    change_set_id: QualityId | None = None

    @field_validator("paths")
    @classmethod
    def checked_paths(cls, paths):
        if paths is None:
            return paths
        normalized = []
        for path in paths:
            path = path.replace("\\", "/")
            parts = path.split("/")
            if (
                parts[0] not in {"Assets", "Packages"}
                or not path.lower().endswith(".cs")
                or any(
                    part in {"", ".", ".."} or part.startswith(".") or part.endswith("~")
                    for part in parts
                )
                or any(char in path for char in ':*?"<>|\0\r\n')
                or "/resources/gamedata/" in path.lower()
            ):
                raise ValueError("Select explicit project-relative C# source paths")
            normalized.append(path)
        if len({path.casefold() for path in normalized}) != len(normalized):
            raise ValueError("Duplicate source paths are not permitted")
        return normalized

    @model_validator(mode="after")
    def exactly_one_selection(self):
        if (self.paths is None) == (self.change_set_id is None):
            raise ValueError("Choose exactly one of paths or change_set_id")
        return self


class CaptureBaselineOptions(ScriptSelection):
    include_suppressed: bool = False
    timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 120


class AnalyzeOptions(CaptureBaselineOptions):
    baseline_id: QualityId | None = None


class QualityJobOptions(QualityOptions):
    job_id: QualityId


class QualityStatusOptions(QualityJobOptions):
    cursor: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    page_size: Annotated[int, Field(ge=1, le=500)] = 100


class QualityImpactOptions(QualityOptions):
    analysis_id: QualityId
    baseline_id: QualityId | None = None
    cursor: Annotated[str, Field(min_length=1, max_length=1024)] | None = None
    page_size: Annotated[int, Field(ge=1, le=500)] = 100


_OPTIONS = {
    "capabilities": QualityOptions,
    "capture_baseline": CaptureBaselineOptions,
    "analyze": AnalyzeOptions,
    "status": QualityStatusOptions,
    "cancel": QualityJobOptions,
    "impact": QualityImpactOptions,
}


def parse_quality_options(action: str, options: dict | None) -> QualityOptions:
    model = _OPTIONS.get(action)
    if model is None:
        raise ValueError("Unsupported script quality action")
    return model.model_validate({} if options is None else options)
