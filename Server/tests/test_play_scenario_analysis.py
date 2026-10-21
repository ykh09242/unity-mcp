"""Offline analysis preserves failures and rejects ambiguous or unsafe visual evidence."""

import json
from hashlib import sha256
from pathlib import Path
import socket
import subprocess

import click
from click.testing import CliRunner
from PIL import Image
import pytest

from cli.commands.play_scenario_analysis import register_analysis_commands
from cli.utils.play_scenario_analysis_io import AnalysisError
from cli.utils.play_scenario_visual_compare import VisualPair, VisualPolicy, compare_visual


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline analysis attempted network or subprocess access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(
        "cli.commands.play_scenario_analysis.get_config",
        lambda: type("Config", (), {"format": "json"})(),
    )


@pytest.fixture
def command_group():
    group = click.Group()
    register_analysis_commands(group)
    return group


def write_json(path, content):
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def policy(**changes):
    value = {
        "schema_version": 1,
        "width": 2,
        "height": 2,
        "channel_threshold": {"red": 0, "green": 0, "blue": 0, "alpha": 0},
        "max_changed_pixel_ratio": 0.0,
        "ignored_rectangles": [],
    }
    value.update(changes)
    return value


def png(path, color=(0, 0, 0, 255), size=(2, 2)):
    Image.new("RGBA", size, color).save(path)
    return path


def test_eight_k_edge_png_above_old_encoded_limit_is_compared(tmp_path):
    import random

    content = random.Random(17).randbytes(8192 * 129 * 4)
    left = tmp_path / "large.png"
    with Image.frombytes("RGBA", (8192, 129), content) as image:
        image.save(left)
    assert left.stat().st_size > 4 * 1024 * 1024
    result = compare_visual(
        VisualPair(left, left), VisualPolicy.model_validate(policy(width=8192, height=129))
    )
    assert result.compared_pixels == 8192 * 129
    assert result.changed_pixels == 0


def test_small_artifact_read_does_not_allocate_its_large_admission_limit(tmp_path):
    import tracemalloc
    from cli.utils.play_scenario_analysis_io import read_bytes

    source = tmp_path / "tiny.bin"
    source.write_bytes(b"small")
    tracemalloc.start()
    try:
        assert read_bytes(source, 320 * 1024 * 1024) == b"small"
        assert tracemalloc.get_traced_memory()[1] < 1024 * 1024
    finally:
        tracemalloc.stop()


def test_strip_edges_and_ignored_rectangles_preserve_exact_pixel_counts(tmp_path):
    left = png(tmp_path / "left.png", size=(7, 513))
    right = png(tmp_path / "right.png", size=(7, 513))
    with Image.open(right) as image:
        for y in (0, 255, 256, 511, 512):
            image.putpixel((3, y), (1, 2, 3, 254))
        image.save(right)
    selected = VisualPolicy.model_validate(
        policy(
            width=7, height=513, ignored_rectangles=[{"x": 3, "y": 255, "width": 1, "height": 2}]
        )
    )
    result = compare_visual(VisualPair(left, right), selected, tmp_path / "output")
    assert result.changed_pixels == 3
    assert result.compared_pixels == 7 * 513 - 2
    with Image.open(result.heatmap_path) as image:
        assert image.getpixel((3, 512)) == (255, 0, 0)
        assert image.getpixel((3, 255)) == (96, 96, 96)


def test_failed_candidate_decode_releases_already_loaded_image(tmp_path, monkeypatch):
    from cli.utils import play_scenario_visual_compare as visual

    first = Image.new("RGBA", (2, 2))
    calls = 0

    def load(_path, _hash):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first, "a" * 64
        raise AnalysisError("candidate invalid")

    monkeypatch.setattr(visual, "_load_png", load)
    with pytest.raises(AnalysisError, match="candidate invalid"):
        compare_visual(
            VisualPair(tmp_path / "a.png", tmp_path / "b.png"),
            VisualPolicy.model_validate(policy()),
        )
    with pytest.raises(ValueError, match="closed"):
        first.getpixel((0, 0))


def test_encoder_failure_removes_only_outputs_created_by_this_comparison(tmp_path, monkeypatch):
    from cli.utils import play_scenario_visual_compare as visual

    left = png(tmp_path / "a.png")
    right = png(tmp_path / "b.png")
    output = tmp_path / "output"
    output.mkdir()
    preserved = output / "notes.txt"
    preserved.write_text("keep")
    original = Image.Image.save
    writes = 0

    def save(image, target, *args, **kwargs):
        nonlocal writes
        if isinstance(target, visual._PngWriter):
            writes += 1
            if writes == 2:
                target.write(b"partial")
                raise OSError("encoder failed")
        return original(image, target, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    with pytest.raises(OSError, match="encoder failed"):
        compare_visual(VisualPair(left, right), VisualPolicy.model_validate(policy()), output)
    assert sorted(path.name for path in output.iterdir()) == ["notes.txt"]
    assert preserved.read_text() == "keep"


def test_channel_threshold_and_ratio_are_inclusive(tmp_path):
    left = png(tmp_path / "left.png")
    right = png(tmp_path / "right.png")
    with Image.open(right) as image:
        image.putpixel((0, 0), (1, 0, 0, 255))
        image.save(right)
    comparison = compare_visual(
        VisualPair(left, right), VisualPolicy.model_validate(policy(max_changed_pixel_ratio=0.25))
    )
    assert comparison.status == "within_budget"
    assert comparison.changed_pixel_ratio == 0.25
    comparison = compare_visual(
        VisualPair(left, right),
        VisualPolicy.model_validate(
            policy(channel_threshold={"red": 1, "green": 0, "blue": 0, "alpha": 0})
        ),
    )
    assert comparison.changed_pixels == 0


def test_overlapping_ignore_rectangles_count_once_and_alpha_matters(tmp_path):
    left = png(tmp_path / "left.png")
    right = png(tmp_path / "right.png", color=(0, 0, 0, 254))
    rectangles = [{"x": 0, "y": 0, "width": 1, "height": 1}] * 2
    result = compare_visual(
        VisualPair(left, right),
        VisualPolicy.model_validate(policy(ignored_rectangles=rectangles)),
        tmp_path / "output",
    )
    assert result.compared_pixels == 3
    assert result.changed_pixels == 3
    assert result.status == "exceeded"
    assert len(result.artifacts) == 2
    for artifact in result.artifacts:
        assert sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]


@pytest.mark.parametrize(
    "changes",
    [
        {"ignored_rectangles": [{"x": 2, "y": 0, "width": 1, "height": 1}]},
        {"width": True},
        {"width": 8193, "height": 8192},
        {"max_changed_pixel_ratio": float("nan")},
        {"unexpected": "<script>"},
    ],
)
def test_policy_rejects_invalid_explicit_controls(changes):
    with pytest.raises(ValueError):
        VisualPolicy.model_validate(policy(**changes))


def test_every_pixel_ignored_and_dimension_mismatch_are_not_comparable(tmp_path):
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png", size=(1, 2))
    with pytest.raises(AnalysisError, match="dimensions"):
        compare_visual(VisualPair(left, right), VisualPolicy.model_validate(policy()))
    png(right)
    with pytest.raises(AnalysisError, match="every pixel"):
        compare_visual(
            VisualPair(left, right),
            VisualPolicy.model_validate(
                policy(ignored_rectangles=[{"x": 0, "y": 0, "width": 2, "height": 2}])
            ),
        )


@pytest.mark.parametrize(
    "case, expected", [("equal", 0), ("different", 1), ("missing", 2), ("malformed", 2)]
)
def test_real_visual_click_exit_semantics(command_group, tmp_path, case, expected):
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png")
    if case == "different":
        png(right, color=(255, 0, 0, 255))
    elif case == "missing":
        right.unlink()
    elif case == "malformed":
        right.write_bytes(b"not a PNG")
    policy_file = write_json(tmp_path / "policy.json", policy())
    result = CliRunner().invoke(
        command_group, ["compare-visual", str(left), str(right), "--policy", str(policy_file)]
    )
    assert result.exit_code == expected, result.output
    assert (
        json.loads(result.output)["status"]
        == {0: "within_budget", 1: "exceeded", 2: "not_comparable"}[expected]
    )


def test_bounds_and_existing_outputs_preserve_files(tmp_path):
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png")
    output = tmp_path / "output"
    output.mkdir()
    (output / "heatmap.png").write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        compare_visual(VisualPair(left, right), VisualPolicy.model_validate(policy()), output)
    assert (output / "heatmap.png").read_bytes() == b"preserved"
    oversized = tmp_path / "oversized.png"
    with oversized.open("wb") as stream:
        stream.truncate(320 * 1024 * 1024 + 1)
    with pytest.raises(AnalysisError):
        compare_visual(VisualPair(left, oversized), VisualPolicy.model_validate(policy()))


def test_png_crc_error_is_structured_cli_failure(command_group, tmp_path):
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png")
    content = bytearray(right.read_bytes())
    index = content.index(b"IDAT") + 4
    content[index] ^= 255
    right.write_bytes(content)
    policy_file = write_json(tmp_path / "policy.json", policy())
    result = CliRunner().invoke(
        command_group, ["compare-visual", str(left), str(right), "--policy", str(policy_file)]
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "not_comparable"


@pytest.mark.parametrize(
    "spelling",
    [
        "//server/share/capture.png",
        "NUL",
        ".env.png",
        "Assets/Resources/GameData/capture.png",
        "capture.png:stream",
    ],
)
def test_unsafe_image_paths_are_rejected_before_file_access(monkeypatch, spelling):
    from cli.utils.play_scenario_analysis_io import read_bytes

    def forbidden(_path):
        pytest.fail("Unsafe path reached filesystem metadata")

    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(AnalysisError):
        read_bytes(Path(spelling), 10)


def test_dimension_bomb_is_rejected_before_png_decode(tmp_path):
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png", size=(8193, 1))
    with pytest.raises(AnalysisError, match="dimensions"):
        compare_visual(VisualPair(left, right), VisualPolicy.model_validate(policy()))
