# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Decode the native PLAY19 fixture's actual MP4 and verify geometry and timestamps.

How to run: python tools/experiments/validate_recording_aspect_mp4.py path/to/fit.mp4 path/to/crop.mp4
Requires the existing ffmpeg and ffprobe executables on PATH; installs nothing.
"""

import argparse
from enum import StrEnum
import json
from pathlib import Path
import subprocess
from typing import TypedDict, assert_never


class AspectMode(StrEnum):
    FIT = "fit"
    CROP = "crop"


class ValidationReceipt(TypedDict):
    path: str
    mode: str
    frames: int
    timestamps: list[float]
    circle_width: int
    circle_height: int


def validate(path: Path) -> ValidationReceipt:
    """Validate a single retained MP4 emitted by RecordingAspectModeTests."""
    mode = AspectMode(path.stem)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    metadata = json.loads(probe.stdout)
    stream = metadata["streams"][0]
    if (stream["width"], stream["height"]) != (320, 180):
        raise AssertionError(f"Unexpected encoded resolution: {stream}")
    timestamps = [float(frame["best_effort_timestamp_time"]) for frame in metadata["frames"]]
    if len(timestamps) != 4 or any(
        abs(actual - expected) > 0.015
        for actual, expected in zip(timestamps, (0, 0.12, 0.37, 0.61))
    ):
        raise AssertionError(f"Capture timestamps were changed: {timestamps}")
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    if len(decoded) != 320 * 180 * 3:
        raise AssertionError("The actual MP4 did not decode one complete RGB frame.")
    red_pixels = [
        (index % 320, index // 320)
        for index in range(320 * 180)
        if decoded[index * 3] > 180 and decoded[index * 3 + 1] < 55 and decoded[index * 3 + 2] < 55
    ]
    if not red_pixels:
        raise AssertionError("The decoded MP4 lost the fixture circle.")
    diameter_x = max(x for x, _ in red_pixels) - min(x for x, _ in red_pixels) + 1
    diameter_y = max(y for _, y in red_pixels) - min(y for _, y in red_pixels) + 1
    if diameter_x < 25 or abs(diameter_x - diameter_y) > 4:
        raise AssertionError(f"Decoded circle distorted: {diameter_x}x{diameter_y}")
    edge = decoded[(90 * 320 + 10) * 3 : (90 * 320 + 10) * 3 + 3]
    # Blue bands at both ends also make this independent of codec image orientation.
    band = decoded[(4 * 320 + 160) * 3 : (4 * 320 + 160) * 3 + 3]
    match mode:
        case AspectMode.FIT:
            if max(edge) > 15 or band[2] < 180 or band[0] > 50:
                raise AssertionError(
                    "Fit MP4 did not retain blue source bands and black side padding."
                )
        case AspectMode.CROP:
            if max(edge) < 25 or abs(band[2] - band[0]) > 25:
                raise AssertionError("Crop MP4 did not fill the frame and remove outer blue bands.")
        case unreachable:
            assert_never(unreachable)
    return {
        "path": str(path.resolve()),
        "mode": mode,
        "frames": len(timestamps),
        "timestamps": timestamps,
        "circle_width": diameter_x,
        "circle_height": diameter_y,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="+", type=Path)
    arguments = parser.parse_args()
    for video in arguments.videos:
        print(json.dumps(validate(video)))


if __name__ == "__main__":
    main()
