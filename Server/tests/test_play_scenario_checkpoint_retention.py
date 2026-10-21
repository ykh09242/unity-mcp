"""Owned session eviction accepts verified references and preserves unsafe inventories."""

from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
from click.testing import CliRunner
from cli.main import cli
from cli.utils.play_scenario_analysis_io import AnalysisError
from .play_scenario_soak_budget_fixtures import _known_evidence

from cli.utils import play_scenario_player_session as sessions
from cli.utils.play_scenario_checkpoint_artifacts import admit_checkpoint_artifacts
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle


def child_artifacts(root, job_id="a" * 32):
    directory = root / job_id
    directory.mkdir(parents=True)
    image = directory / "checkpoint.png"
    Image.new("RGBA", (2, 2), (0, 0, 0, 255)).save(image)
    capture = {
        "name": "menu",
        "status": "captured",
        "path": image.name,
        "sha256": sha256(image.read_bytes()).hexdigest(),
        "requested_width": 2,
        "requested_height": 2,
        "width": 2,
        "height": 2,
    }
    report = {"steps": [{"screenshot": capture}]}
    (directory / "run.json").write_text(json.dumps(report), encoding="utf-8")
    (directory / "process.json").write_text('{"process_ended":true}', encoding="utf-8")
    return directory, capture, report


def test_referenced_checkpoint_is_retained_then_evicted_with_owned_child(tmp_path):
    first, _capture, _report = child_artifacts(tmp_path)
    second, _capture, _report = child_artifacts(tmp_path, "b" * 32)
    kept = deque()
    sessions._retain(first, kept, None, 1)
    assert (first / "checkpoint.png").is_file()
    sessions._retain(second, kept, None, 1)
    assert not first.exists()
    assert tuple(kept) == (second,)
    assert (second / "checkpoint.png").is_file()


@pytest.mark.parametrize("mutation", ["unknown", "hash", "traversal", "dimension", "unavailable"])
def test_invalid_checkpoint_inventory_is_rejected_without_deletion(tmp_path, mutation):
    directory, capture, report = child_artifacts(tmp_path)
    if mutation == "unknown":
        (directory / "unknown.png").write_bytes(b"unreferenced")
    elif mutation == "hash":
        capture["sha256"] = "f" * 64
    elif mutation == "traversal":
        capture["path"] = "../checkpoint.png"
    elif mutation == "dimension":
        capture["width"] = 3
    elif mutation == "unavailable":
        capture["status"] = "unavailable"
    (directory / "run.json").write_text(json.dumps(report), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises((ValueError, sessions.PlayerLaunchError)):
        sessions._retain(directory, deque(), None, 1)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_eviction_revalidates_referenced_hash_before_any_file_deletion(tmp_path):
    first, _capture, _report = child_artifacts(tmp_path)
    second, _capture, _report = child_artifacts(tmp_path, "b" * 32)
    kept = deque()
    sessions._retain(first, kept, None, 1)
    (first / "checkpoint.png").write_bytes(b"changed after admission")
    with pytest.raises(ValueError):
        sessions._retain(second, kept, None, 1)
    assert (first / "run.json").exists()
    assert (first / "process.json").exists()
    assert (first / "checkpoint.png").exists()


def test_soak_retained_report_checkpoint_hashes_cannot_disagree(tmp_path):
    directory, capture, report = child_artifacts(tmp_path)
    capture["sha256"] = "b" * 64
    retained = directory / "iteration-0001.json"
    retained.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        admit_checkpoint_artifacts(directory, (directory / "run.json", retained))


def test_checkpoint_aggregate_budget_is_checked_before_png_decode(tmp_path, monkeypatch):
    directory, _capture, _report = child_artifacts(tmp_path)
    real_lstat = Path.lstat

    def oversized(path):
        actual = real_lstat(path)
        return (
            SimpleNamespace(
                st_size=8 * 1024**3 + 1,
                st_mode=actual.st_mode,
                st_file_attributes=getattr(actual, "st_file_attributes", 0),
            )
            if path.name == "checkpoint.png"
            else actual
        )

    monkeypatch.setattr(Path, "lstat", oversized)
    with pytest.raises(ValueError, match="aggregate"):
        admit_checkpoint_artifacts(directory, (directory / "run.json",))


def test_graphical_owned_session_preserves_and_evicts_actual_png_artifacts(
    verified_bundle, child, monkeypatch
):
    build, output, _manifest = verified_bundle
    launch = sessions.run_player

    def with_capture(options):
        outcome = launch(options)
        image = outcome.directory / "checkpoint.png"
        Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(image)
        capture = {
            "name": "menu",
            "status": "captured",
            "path": image.name,
            "sha256": sha256(image.read_bytes()).hexdigest(),
            "requested_width": 2,
            "requested_height": 2,
            "width": 2,
            "height": 2,
        }
        outcome.report["steps"] = [{"screenshot": capture}]
        (outcome.directory / "run.json").write_text(json.dumps(outcome.report), encoding="utf-8")
        return outcome

    monkeypatch.setattr(sessions, "run_player", with_capture)
    summary, directory, code = sessions.run_player_session(
        sessions.PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            iterations=2,
            mode="fresh-process",
            graphical=True,
            interval_seconds=0.0,
            retain_reports=1,
        )
    )
    assert code == 0
    assert summary["children_started"] == 2
    assert len(list((directory / "runs").iterdir())) == 1
    assert len(list((directory / "runs").glob("*/checkpoint.png"))) == 1
    assert all(process.poll() == 0 for process in child[1])


def test_crc_corruption_with_matching_report_hash_prevents_all_eviction(tmp_path):
    first, capture, report = child_artifacts(tmp_path)
    second, _capture, _report = child_artifacts(tmp_path, "b" * 32)
    kept = deque()
    sessions._retain(first, kept, None, 1)
    image = first / "checkpoint.png"
    content = bytearray(image.read_bytes())
    content[content.index(b"IDAT") + 4] ^= 255
    image.write_bytes(content)
    capture["sha256"] = sha256(content).hexdigest()
    (first / "run.json").write_text(json.dumps(report), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in first.iterdir()}
    with pytest.raises(AnalysisError, match="PNG validation") as error:
        sessions._retain(second, kept, None, 1)
    assert isinstance(error.value.__cause__, SyntaxError)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == before


def test_crc_corruption_in_saved_soak_is_structured_registry_failure(tmp_path):
    directory = _known_evidence(tmp_path / "soak", [0, 1, 2])
    image = directory / "checkpoint.png"
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(image)
    content = bytearray(image.read_bytes())
    content[content.index(b"IDAT") + 4] ^= 255
    image.write_bytes(content)
    capture = {
        "name": "menu",
        "status": "captured",
        "path": image.name,
        "sha256": sha256(content).hexdigest(),
        "requested_width": 2,
        "requested_height": 2,
        "width": 2,
        "height": 2,
    }
    raw = json.loads((directory / "run.json").read_bytes())
    raw["steps"] = [{"screenshot": capture}]
    (directory / "run.json").write_text(json.dumps(raw), encoding="utf-8")
    for filename in raw["soak"]["retained_report_files"]:
        report = json.loads((directory / filename).read_bytes())
        report["steps"] = raw["steps"]
        (directory / filename).write_text(json.dumps(report), encoding="utf-8")
    registry = tmp_path / "registry.json"
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "baseline",
            "--registry",
            str(registry),
            "register",
            "corrupt",
            str(directory),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "PNG validation" in result.output
    assert not registry.exists()
    comparison = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-soak-metrics",
            str(directory),
            str(directory),
        ],
    )
    assert comparison.exit_code == 2, comparison.output
    assert json.loads(comparison.output)["status"] == "not_comparable"
    assert "PNG validation" in comparison.output
