"""Admit only native report-referenced bounded PNGs for owned artifact retention."""

from models.play_scenario_limits import MAX_STEP_RESULTS

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from cli.utils.play_scenario_analysis_io import AnalysisError, read_json, safe_local_path
from cli.utils.play_scenario_visual_compare import _load_png
from cli.utils.play_scenario_visual_reports import ScreenshotEvidence

CHECKPOINT_BYTES_LIMIT: Final = 64 * 1024**2
CHECKPOINT_FILE_LIMIT: Final = 4096


class _CaptureStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    screenshot: ScreenshotEvidence | None = None


class _CaptureReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    steps: list[_CaptureStep] = Field(default_factory=list, max_length=MAX_STEP_RESULTS)


@dataclass(frozen=True, slots=True)
class CheckpointArtifact:
    """Verified report-relative file admitted for preservation or owned eviction."""

    filename: str
    byte_count: int


def admit_checkpoint_artifacts(
    directory: Path, reports: tuple[Path, ...], selected_report: dict[str, JsonValue] | None = None
) -> tuple[CheckpointArtifact, ...]:
    """Verify explicit bare PNG references, fingerprints and dimensions without discovery."""
    if len(reports) > 65:
        raise AnalysisError("Checkpoint report selection exceeds its bound")
    root = safe_local_path(directory)
    captures: dict[str, ScreenshotEvidence] = {}
    projections = (
        [_CaptureReport.model_validate(selected_report)] if selected_report is not None else []
    )
    for source in reports:
        if safe_local_path(source).parent != root:
            raise AnalysisError("Checkpoint report leaves its owned artifact directory")
        projections.append(_CaptureReport.model_validate(read_json(source)))
    for report in projections:
        for step in report.steps:
            capture = step.screenshot
            if capture is None:
                continue
            if capture.status != "captured":
                if capture.path is not None or capture.sha256 is not None:
                    raise AnalysisError("Unavailable checkpoint cannot own an artifact")
                continue
            if capture.path is None or capture.sha256 is None:
                raise AnalysisError("Captured checkpoint lacks its artifact fingerprint")
            filename = capture.path
            if (
                "/" in filename
                or "\\" in filename
                or Path(filename).suffix != ".png"
                or filename in {".", ".."}
            ):
                raise AnalysisError("Checkpoint retention requires a bare PNG filename")
            path = safe_local_path(root / filename)
            if path.parent != root:
                raise AnalysisError("Checkpoint file leaves its owned artifact directory")
            if filename in captures and capture != captures[filename]:
                raise AnalysisError("Checkpoint filename has conflicting report fingerprints")
            captures[filename] = capture
            if len(captures) > CHECKPOINT_FILE_LIMIT:
                raise AnalysisError("Checkpoint artifact count exceeds its bound")
    artifacts: list[CheckpointArtifact] = []
    total = 0
    for filename, capture in captures.items():
        path = root / filename
        byte_count = path.lstat().st_size
        total += byte_count
        if total > CHECKPOINT_BYTES_LIMIT:
            raise AnalysisError("Checkpoint PNG aggregate exceeds 64 MiB")
        artifacts.append(CheckpointArtifact(filename, byte_count))
    for filename, capture in captures.items():
        path = root / filename
        image, _fingerprint = _load_png(path, capture.sha256)
        try:
            if image.size != (capture.width, capture.height) or image.size != (
                capture.requested_width,
                capture.requested_height,
            ):
                raise AnalysisError("Checkpoint PNG dimensions differ from its native report")
        finally:
            image.close()
    return tuple(artifacts)
