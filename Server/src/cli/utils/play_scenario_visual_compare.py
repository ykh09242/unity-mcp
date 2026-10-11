"""Strict pixel comparison of bounded local PNG artifacts under an explicit policy."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
import warnings

from PIL import Image, ImageChops, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from cli.utils.play_scenario_analysis_io import (
    AnalysisError,
    read_bytes,
    read_json,
    safe_local_path,
    write_new,
)

Dimension = Annotated[int, Field(strict=True, ge=1, le=4096)]
Channel = Annotated[int, Field(strict=True, ge=0, le=255)]
PNG_LIMIT = 4 * 1024 * 1024
PIXEL_LIMIT = 8 * 1024 * 1024


class Rectangle(BaseModel):
    """Ignored half-open rectangle in fixed image pixel coordinates."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    x: Annotated[int, Field(strict=True, ge=0)]
    y: Annotated[int, Field(strict=True, ge=0)]
    width: Dimension
    height: Dimension


class ChannelThreshold(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    red: Channel
    green: Channel
    blue: Channel
    alpha: Channel


class VisualPolicy(BaseModel):
    """No resizing, tolerance defaults or silently accepted policy fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    width: Dimension
    height: Dimension
    channel_threshold: ChannelThreshold
    max_changed_pixel_ratio: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    ignored_rectangles: list[Rectangle] = Field(max_length=64)

    @model_validator(mode="after")
    def bounded_geometry(self) -> "VisualPolicy":
        if self.width * self.height > PIXEL_LIMIT:
            raise AnalysisError("Policy exceeds 8 Mi pixels")
        if any(
            rect.x + rect.width > self.width or rect.y + rect.height > self.height
            for rect in self.ignored_rectangles
        ):
            raise AnalysisError("Ignored rectangle leaves the fixed image bounds")
        return self


@dataclass(frozen=True, slots=True)
class VisualPair:
    """Explicit images and optional expected native artifact fingerprints."""

    baseline: Path
    candidate: Path
    baseline_sha256: str | None = None
    candidate_sha256: str | None = None


class VisualComparison(BaseModel):
    """Stable JSON contract for CLI gates and HTML attachments."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["within_budget", "exceeded"]
    compared: Literal[True] = True
    changed_pixels: int
    compared_pixels: int
    ignored_pixels: int
    changed_pixel_ratio: float
    baseline_sha256: str
    candidate_sha256: str
    policy: VisualPolicy
    heatmap_path: str | None = None
    diff_path: str | None = None
    artifacts: list[dict[str, JsonValue]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")


def load_visual_policy(source: Path) -> VisualPolicy:
    return VisualPolicy.model_validate(read_json(source))


def _load_png(source: Path, expected_hash: str | None) -> tuple[Image.Image, str]:
    """Verify bytes, header bounds and CRC before decoding a single-frame PNG."""
    content = read_bytes(source, PNG_LIMIT)
    fingerprint = sha256(content).hexdigest()
    if expected_hash is not None and fingerprint != expected_hash:
        raise AnalysisError("Screenshot SHA256 differs from its report")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=("PNG",)) as image:
                width, height = image.size
                if (
                    not 1 <= width <= 4096
                    or not 1 <= height <= 4096
                    or width * height > PIXEL_LIMIT
                ):
                    raise AnalysisError("PNG dimensions exceed screenshot bounds")
                if getattr(image, "n_frames", 1) != 1:
                    raise AnalysisError("Animated PNG is unsupported")
                image.verify()
            with Image.open(BytesIO(content), formats=("PNG",)) as image:
                image.load()
                return image.convert("RGBA"), fingerprint
    except AnalysisError:
        raise
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise AnalysisError(f"PNG validation failed: {exc}") from exc


def compare_visual(
    pair: VisualPair, policy: VisualPolicy, output_dir: Path | None = None
) -> VisualComparison:
    """Compare RGBA channels; delta equal to threshold remains within tolerance.

    A pixel changes if any channel delta exceeds its threshold. Ignored pixels
    are excluded from the denominator, with overlapping rectangles counted once.
    PNG bytes <=4 MiB, decoded pixels <=8 Mi and dimensions <=4096 each.
    Optional diff/heatmap outputs are PNGs <=32 MiB each, created without overwrite.
    """
    left, left_hash = _load_png(pair.baseline, pair.baseline_sha256)
    right, right_hash = _load_png(pair.candidate, pair.candidate_sha256)
    expected = (policy.width, policy.height)
    if left.size != expected or right.size != expected:
        raise AnalysisError("Both PNG dimensions must match the explicit policy")
    included = Image.new("L", expected, 255)
    painter = ImageDraw.Draw(included)
    for rect in policy.ignored_rectangles:
        painter.rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1), fill=0
        )
    compared_pixels = included.histogram()[255]
    if compared_pixels == 0:
        raise AnalysisError("Ignored rectangles cover every pixel")
    difference = ImageChops.difference(left, right)
    changed = Image.new("L", expected, 0)
    thresholds = policy.channel_threshold.model_dump().values()
    for channel, threshold in zip(difference.split(), thresholds):
        over = channel.point([255 if value > threshold else 0 for value in range(256)])
        changed = ImageChops.lighter(changed, over)
    changed = ImageChops.multiply(changed, included)
    changed_pixels = changed.histogram()[255]
    ratio = changed_pixels / compared_pixels
    heatmap_path = diff_path = None
    artifacts: list[dict[str, JsonValue]] = []
    if output_dir is not None:
        for name in ("heatmap.png", "diff.png"):
            destination = safe_local_path(output_dir / name)
            if destination.exists():
                raise FileExistsError(f"Output already exists: {destination}")
        heatmap = Image.new("RGB", expected, (0, 0, 0))
        heatmap.paste((255, 0, 0), mask=changed)
        heatmap.paste((96, 96, 96), mask=ImageChops.invert(included))
        diff_image = Image.merge(
            "RGB",
            (
                ImageChops.lighter(difference.getchannel("R"), difference.getchannel("A")),
                difference.getchannel("G"),
                difference.getchannel("B"),
            ),
        )
        diff_image.paste((0, 0, 0), mask=ImageChops.invert(included))
        encoded: list[bytes] = []
        for output_image in (heatmap, diff_image):
            stream = BytesIO()
            output_image.save(stream, format="PNG")
            content = stream.getvalue()
            if len(content) > 32 * 1024 * 1024:
                raise AnalysisError("Diff output exceeds 32 MiB")
            encoded.append(content)
        heatmap_path = str(write_new(output_dir / "heatmap.png", encoded[0]))
        diff_path = str(write_new(output_dir / "diff.png", encoded[1]))
        artifacts = [
            {
                "name": name,
                "path": path,
                "sha256": sha256(content).hexdigest(),
                "width": policy.width,
                "height": policy.height,
            }
            for name, path, content in zip(("heatmap", "diff"), (heatmap_path, diff_path), encoded)
        ]
    return VisualComparison(
        status="within_budget" if ratio <= policy.max_changed_pixel_ratio else "exceeded",
        changed_pixels=changed_pixels,
        compared_pixels=compared_pixels,
        ignored_pixels=policy.width * policy.height - compared_pixels,
        changed_pixel_ratio=ratio,
        baseline_sha256=left_hash,
        candidate_sha256=right_hash,
        policy=policy,
        heatmap_path=heatmap_path,
        diff_path=diff_path,
        artifacts=artifacts,
    )
