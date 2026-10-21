"""Strict pixel comparison of bounded local PNG artifacts under an explicit policy."""

from contextlib import ExitStack, closing
from dataclasses import dataclass
import os
import stat
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
)

Dimension = Annotated[int, Field(strict=True, ge=1, le=8192)]
Channel = Annotated[int, Field(strict=True, ge=0, le=255)]
PNG_LIMIT = 320 * 1024 * 1024
PIXEL_LIMIT = 64 * 1024 * 1024


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
            raise AnalysisError("Policy exceeds 64 Mi pixels")
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
            with closing(Image.open(BytesIO(content), formats=("PNG",))) as image:
                width, height = image.size
                if (
                    not 1 <= width <= 8192
                    or not 1 <= height <= 8192
                    or width * height > PIXEL_LIMIT
                ):
                    raise AnalysisError("PNG dimensions exceed screenshot bounds")
                if getattr(image, "n_frames", 1) != 1:
                    raise AnalysisError("Animated PNG is unsupported")
                image.verify()
            with closing(Image.open(BytesIO(content), formats=("PNG",))) as image:
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


class _PngWriter:
    """Bound encoder output while hashing the exact bytes written to an owned file."""

    def __init__(self, stream):
        self.stream = stream
        self.count = 0
        self.digest = sha256()

    def write(self, content: bytes) -> int:
        if self.count + len(content) > PNG_LIMIT:
            raise AnalysisError("Diff output exceeds 320 MiB")
        written = self.stream.write(content)
        if written != len(content):
            raise OSError("Incomplete PNG write")
        self.digest.update(content)
        self.count += written
        return written

    def tell(self) -> int:
        return self.count

    def flush(self) -> None:
        self.stream.flush()


def _save_visual_outputs(images, output_dir: Path, policy: VisualPolicy):
    """Stream both outputs; remove only files created by this invocation on failure."""
    destinations = [safe_local_path(output_dir / name) for name in ("heatmap.png", "diff.png")]
    for path in destinations:
        if path.exists():
            raise FileExistsError(f"Output already exists: {path}")
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    owned = []
    artifacts = []
    try:
        for name, path, image in zip(("heatmap", "diff"), destinations, images):
            safe_local_path(path)
            with path.open("xb") as stream:
                identity = os.fstat(stream.fileno())
                owned.append((path, identity.st_dev, identity.st_ino))
                writer = _PngWriter(stream)
                image.save(writer, format="PNG")
                artifacts.append(
                    {
                        "name": name,
                        "path": str(path),
                        "sha256": writer.digest.hexdigest(),
                        "width": policy.width,
                        "height": policy.height,
                    }
                )
    except Exception:
        for path, device, inode in owned:
            try:
                safe_local_path(path)
                current = path.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (
                    device,
                    inode,
                ):
                    path.unlink()
            except (OSError, AnalysisError):
                pass
        raise
    return artifacts


def _compare_strip(
    left, right, top: int, policy: VisualPolicy, heatmap, diff_image
) -> tuple[int, int]:
    """Keep intermediate masks and channel differences within 256 rows."""
    box = (0, top, policy.width, min(policy.height, top + 256))
    size = (policy.width, box[3] - top)
    with ExitStack() as stack:

        def own(image):
            return stack.enter_context(closing(image))

        difference = own(ImageChops.difference(own(left.crop(box)), own(right.crop(box))))
        included = own(Image.new("L", size, 255))
        painter = ImageDraw.Draw(included)
        for rect in policy.ignored_rectangles:
            if rect.y < box[3] and rect.y + rect.height > top:
                painter.rectangle(
                    (
                        rect.x,
                        max(0, rect.y - top),
                        rect.x + rect.width - 1,
                        min(size[1], rect.y + rect.height - top) - 1,
                    ),
                    fill=0,
                )
        changed = own(Image.new("L", size, 0))
        for channel, threshold in zip("RGBA", policy.channel_threshold.model_dump().values()):
            over = own(
                own(difference.getchannel(channel)).point(
                    [255 if value > threshold else 0 for value in range(256)]
                )
            )
            changed = own(ImageChops.lighter(changed, over))
        changed = own(ImageChops.multiply(changed, included))
        if heatmap is not None:
            inverse = own(ImageChops.invert(included))
            heat = own(Image.new("RGB", size, (0, 0, 0)))
            heat.paste((255, 0, 0), mask=changed)
            heat.paste((96, 96, 96), mask=inverse)
            heatmap.paste(heat, box)
            red = own(
                ImageChops.lighter(own(difference.getchannel("R")), own(difference.getchannel("A")))
            )
            diff = own(
                Image.merge(
                    "RGB", (red, own(difference.getchannel("G")), own(difference.getchannel("B")))
                )
            )
            diff.paste((0, 0, 0), mask=inverse)
            diff_image.paste(diff, box)
        return included.histogram()[255], changed.histogram()[255]


def compare_visual(
    pair: VisualPair, policy: VisualPolicy, output_dir: Path | None = None
) -> VisualComparison:
    """Compare exact RGBA pixels in bounded strips, with optional streamed PNG outputs.

    Any channel delta above its threshold marks a changed pixel. Overlapping
    ignored rectangles count once. Images admit 8192 edges and 64 Mi pixels;
    encoded input/output is bounded to 320 MiB each. Source images and every
    temporary image are explicitly released on success and failure.
    """
    if output_dir is not None:
        for name in ("heatmap.png", "diff.png"):
            destination = safe_local_path(output_dir / name)
            if destination.exists():
                raise FileExistsError(f"Output already exists: {destination}")
    with ExitStack() as outputs:
        heatmap = diff_image = None
        with ExitStack() as sources:
            left, left_hash = _load_png(pair.baseline, pair.baseline_sha256)
            sources.enter_context(closing(left))
            right, right_hash = _load_png(pair.candidate, pair.candidate_sha256)
            sources.enter_context(closing(right))
            if left.size != (policy.width, policy.height) or right.size != left.size:
                raise AnalysisError("Both PNG dimensions must match the explicit policy")
            if output_dir is not None:
                heatmap = outputs.enter_context(closing(Image.new("RGB", left.size)))
                diff_image = outputs.enter_context(closing(Image.new("RGB", left.size)))
            compared_pixels = changed_pixels = 0
            for top in range(0, policy.height, 256):
                compared, changed = _compare_strip(left, right, top, policy, heatmap, diff_image)
                compared_pixels += compared
                changed_pixels += changed
            if compared_pixels == 0:
                raise AnalysisError("Ignored rectangles cover every pixel")
        # Release full source RGBA images before encoding any optional output.
        artifacts = (
            _save_visual_outputs((heatmap, diff_image), output_dir, policy)
            if output_dir is not None
            else []
        )
    ratio = changed_pixels / compared_pixels
    return VisualComparison(
        status="within_budget" if ratio <= policy.max_changed_pixel_ratio else "exceeded",
        changed_pixels=changed_pixels,
        compared_pixels=compared_pixels,
        ignored_pixels=policy.width * policy.height - compared_pixels,
        changed_pixel_ratio=ratio,
        baseline_sha256=left_hash,
        candidate_sha256=right_hash,
        policy=policy,
        heatmap_path=artifacts[0]["path"] if artifacts else None,
        diff_path=artifacts[1]["path"] if artifacts else None,
        artifacts=artifacts,
    )
