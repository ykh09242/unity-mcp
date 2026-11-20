"""Visual profiles admit usable reference pixels before starting a Player."""

from hashlib import sha256
import json
import socket
import subprocess
from xml.etree import ElementTree

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_regression import regression_run
from cli.utils import play_scenario_regression as regression
from cli.utils.play_scenario_player import PlayerOutcome
from .test_play_scenario_regression import profile_value
from .test_play_scenario_visual_reports import checkpoint_report, png, policy, write_json


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Visual profile tests must not launch a process or access the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def visual_profile(tmp_path):
    image = png(tmp_path / "capture.png")
    reference = checkpoint_report(image)
    reference["execution_environment"] = "player"
    reference["reproduction"]["environment"].update(
        platform="WindowsPlayer", build_mode="development"
    )
    write_json(tmp_path / "baseline.json", reference)
    write_json(tmp_path / "policy.json", policy())
    value = profile_value(graphical=True)
    value.update(
        analyze=False,
        visual_checks=[
            {
                "baseline_report": "baseline.json",
                "policy": "policy.json",
                "checkpoint": {"name": "menu", "stage": "main", "step_index": 0, "iteration": 1},
            }
        ],
    )
    return write_json(tmp_path / "profile.json", value), image, reference


@pytest.mark.parametrize("damage", ["missing", "corrupt", "hash", "dimensions", "fully_masked"])
def test_unusable_visual_reference_prevents_player_launch(tmp_path, monkeypatch, damage):
    source, image, reference = visual_profile(tmp_path)
    if damage == "missing":
        image.unlink()
    elif damage == "corrupt":
        image.write_bytes(b"not a PNG")
        reference["steps"][0]["screenshot"]["sha256"] = sha256(image.read_bytes()).hexdigest()
    elif damage == "hash":
        png(image, color=(255, 0, 0, 255))
    elif damage == "dimensions":
        png(image, size=(3, 2))
        reference["steps"][0]["screenshot"]["sha256"] = sha256(image.read_bytes()).hexdigest()
    else:
        write_json(
            tmp_path / "policy.json",
            policy(ignored_rectangles=[{"x": 0, "y": 0, "width": 2, "height": 2}]),
        )
    write_json(tmp_path / "baseline.json", reference)
    inputs = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}

    def forbidden(_options):
        pytest.fail("Unusable reference evidence reached the Player launcher")

    monkeypatch.setattr(regression, "run_player", forbidden)
    output = tmp_path / "result"
    command = CliRunner().invoke(regression_run, [str(source), "--output-dir", str(output)])

    assert command.exit_code == 2, command.output
    result = json.loads((output / "regression.json").read_text())
    assert result["execution_exit_code"] is None
    assert result["execution_directory"] is None
    assert all(gate["status"] == "unavailable" for gate in result["gates"])
    assert result["gates"][0]["detail"]["error"]
    assert ElementTree.parse(output / "junit.xml").getroot().get("errors") == "2"
    assert (output / "report.html").is_file()
    assert not (output / "execution").exists()
    assert all(path.read_bytes() == content for path, content in inputs.items())


@pytest.mark.parametrize("changed", [False, True])
def test_admitted_visual_reference_reaches_real_comparison(tmp_path, monkeypatch, changed):
    source, _image, _reference = visual_profile(tmp_path)
    inputs = {path: path.read_bytes() for path in tmp_path.iterdir()}
    launches = []

    def player(options):
        launches.append(options)
        directory = options.output_directory / "child"
        directory.mkdir(parents=True)
        capture = png(directory / "capture.png", color=(255 if changed else 0, 0, 0, 255))
        report = checkpoint_report(capture)
        report.update(scenario={"name": "Smoke"}, started_unix_ms=100, finished_unix_ms=120)
        report["execution_environment"] = "player"
        report["reproduction"]["environment"].update(
            platform="WindowsPlayer", build_mode="development"
        )
        write_json(directory / "run.json", report)
        return PlayerOutcome(report, directory, 0)

    monkeypatch.setattr(regression, "run_player", player)
    result = regression.run_regression(source, tmp_path / "result")

    assert len(launches) == 1
    assert launches[0].graphical is True
    assert launches[0].require_verified_payload is True
    assert result.exit_code == int(changed), result
    assert next(gate for gate in result.gates if gate.name == "native-report").status == "passed"
    gate = next(gate for gate in result.gates if gate.name == "visual-0")
    assert gate.status == ("failed" if changed else "passed")
    assert gate.detail["changed_pixels"] == (4 if changed else 0)
    assert {a["name"] for a in gate.detail["artifacts"]} == {
        "baseline",
        "candidate",
        "diff",
        "heatmap",
    }
    portable = (tmp_path / "result" / "report.html").read_text()
    assert portable.count("data:image/png;base64,") == 4
    junit = (tmp_path / "result" / "junit.xml").read_text()
    assert '"checkpoint"' in junit and '"sha256"' in junit
    assert all(path.read_bytes() == content for path, content in inputs.items())


@pytest.mark.parametrize("damage", ["replaced_report", "missing_png", "wrong_requested_resolution"])
def test_candidate_capture_failure_stays_visible_alongside_semantic_checkpoint(
    tmp_path, monkeypatch, damage
):
    source, _, _ = visual_profile(tmp_path)

    def player(options):
        directory = options.output_directory / "new-job"
        directory.mkdir(parents=True)
        capture = png(directory / "capture.png")
        report = checkpoint_report(capture)
        report.update(
            job_id="new-job",
            scenario={"name": "observe"},
            status="timed_out",
            execution_environment="player",
        )
        report["reproduction"]["environment"].update(
            platform="WindowsPlayer", build_mode="development"
        )
        row = {
            "stage": "main",
            "iteration": 1,
            "step_index": 1,
            "name": "Daylight",
            "action": "wait_state",
            "status": "timed_out",
            "detail": "phase still night",
        }
        report["steps"].append(row)
        report["failure"] = {
            "code": "condition_unmet",
            "stage": "main",
            "iteration": 1,
            "step_index": 1,
            "target": "world.phase",
            "expected": "day",
            "actual": "night",
        }
        if damage == "missing_png":
            capture.unlink()
        if damage == "wrong_requested_resolution":
            report["steps"][0]["screenshot"]["requested_height"] = 20
        saved = {**report, "job_id": "prior-job"} if damage == "replaced_report" else report
        write_json(directory / "run.json", saved)
        return PlayerOutcome(report, directory, 1)

    monkeypatch.setattr(regression, "run_player", player)
    output = tmp_path / "result"
    result = regression.run_regression(source, output)
    visual = next(g for g in result.gates if g.name == "visual-0")
    assert visual.status == "unavailable"
    if damage == "replaced_report":
        assert "differs from the launched Player" in visual.detail["error"]
    for name in ("regression.json", "report.html", "junit.xml"):
        text = (output / name).read_text()
        assert all(
            term in text
            for term in ("Daylight", "first_failed_step", "world.phase", "day", "night")
        )
