"""Pair one named screenshot checkpoint from two explicit native reports."""

from models.play_scenario_limits import MAX_REPEATS, MAX_SCENARIO_STEPS, MAX_STEP_RESULTS

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from cli.utils.play_scenario_analysis_io import AnalysisError, read_json, safe_local_path
from cli.utils.play_scenario_visual_compare import VisualPair, VisualPolicy
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS, RecordedEnvironment


class CheckpointSelection(BaseModel):
    """Explicit native identity; comparison never picks a positional or latest screenshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    stage: Literal["setup", "main", "cleanup"]
    step_index: Annotated[int, Field(strict=True, ge=0, le=MAX_SCENARIO_STEPS - 1)]
    iteration: Annotated[int, Field(strict=True, ge=1, le=MAX_REPEATS)]


class ScreenshotEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    name: str
    status: Literal["captured", "unavailable", "failed"]
    path: Annotated[str, Field(strict=True, max_length=4096)] | None = None
    sha256: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")] | None = None
    requested_width: Annotated[int, Field(strict=True, ge=1, le=8192)]
    requested_height: Annotated[int, Field(strict=True, ge=1, le=8192)]
    width: Annotated[int, Field(strict=True, ge=1, le=8192)] | None = None
    height: Annotated[int, Field(strict=True, ge=1, le=8192)] | None = None


class CheckpointStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    stage: Literal["setup", "main", "cleanup"]
    step_index: Annotated[int, Field(strict=True, ge=0, le=MAX_SCENARIO_STEPS - 1)]
    iteration: Annotated[int, Field(strict=True, ge=1, le=MAX_REPEATS)]
    name: Annotated[str, Field(strict=True, max_length=128)]
    action: Annotated[str, Field(strict=True, max_length=128)]
    screenshot: ScreenshotEvidence | None = None


class CaptureReproduction(BaseModel):
    """Native environment snapshot and stable definition identity for capture pairing."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
    unity_version: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    package_version: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    environment: RecordedEnvironment


class CheckpointReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    execution_environment: Literal["editor", "player"]
    reproduction: CaptureReproduction
    steps: list[CheckpointStep] = Field(max_length=MAX_STEP_RESULTS)


def pair_visual_reports(
    sources: tuple[Path, Path], selection: CheckpointSelection, policy: VisualPolicy
) -> VisualPair:
    """Require compatible capture environments, identical step identities and verified hashes."""
    reports = tuple(CheckpointReport.model_validate(read_json(source)) for source in sources)
    left, right = reports
    if left.execution_environment != right.execution_environment:
        raise AnalysisError("Checkpoint execution environments differ")
    for field in ("definition_hash", "unity_version", "package_version"):
        value = getattr(left.reproduction, field)
        if not value.strip() or value != getattr(right.reproduction, field):
            raise AnalysisError(f"Checkpoint reports require matching known {field}")
    for report in reports:
        if any(
            getattr(report.reproduction.environment, field) is None
            or str(getattr(report.reproduction.environment, field)).strip().lower()
            in {"", "unknown", "unavailable"}
            for field in ENVIRONMENT_FIELDS
        ):
            raise AnalysisError("Checkpoint environment has unknown native fields")
    if left.reproduction.environment != right.reproduction.environment:
        raise AnalysisError("Recorded checkpoint environments differ")
    images: list[Path] = []
    hashes: list[str] = []
    identities: list[tuple[str, str]] = []
    for source, report in zip(sources, reports):
        matches = [
            step
            for step in report.steps
            if (step.stage, step.step_index, step.iteration)
            == (selection.stage, selection.step_index, selection.iteration)
        ]
        if len(matches) != 1:
            raise AnalysisError("Selected checkpoint step is missing or duplicated")
        step = matches[0]
        capture = step.screenshot
        if capture is None or capture.name != selection.name or capture.status != "captured":
            raise AnalysisError("Named checkpoint does not have a captured screenshot")
        if capture.path is None or capture.sha256 is None:
            raise AnalysisError("Captured checkpoint lacks path or SHA256")
        if (capture.width, capture.height, capture.requested_width, capture.requested_height) != (
            policy.width,
            policy.height,
            policy.width,
            policy.height,
        ):
            raise AnalysisError("Recorded screenshot dimensions differ from policy")
        directory = safe_local_path(source).parent
        artifact = Path(capture.path)
        if ".." in artifact.parts:
            raise AnalysisError("Screenshot path contains traversal")
        image = safe_local_path(directory / artifact)
        if not image.is_relative_to(directory):
            raise AnalysisError("Screenshot path leaves the selected report directory")
        images.append(image)
        hashes.append(capture.sha256)
        identities.append((step.name, step.action))
    if identities[0] != identities[1]:
        raise AnalysisError("Selected checkpoint step identities differ")
    return VisualPair(images[0], images[1], hashes[0], hashes[1])
