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
        {"width": 4096, "height": 4096},
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
    oversized.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
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
    left, right = png(tmp_path / "left.png"), png(tmp_path / "right.png", size=(4097, 1))
    with pytest.raises(AnalysisError, match="dimensions"):
        compare_visual(VisualPair(left, right), VisualPolicy.model_validate(policy()))
