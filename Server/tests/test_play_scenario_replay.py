"""Semantic controls for reducing a replay without substituting an unrelated failure."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario import play_scenario
from cli.utils import play_scenario_replay as replay
from cli.utils.play_scenario_player import PlayerOutcome
from models.play_scenarios import PlayScenario
from .test_play_scenario_player import bundle
from .test_play_scenario_player_assurance import verified_bundle


def definition():
    return {
        "name": "weather",
        "steps": [
            {
                "name": "Load observatory",
                "action": "load_scene",
                "scene": "Assets/Observatory.unity",
            },
            {"name": "Read sky", "action": "wait_scene", "scene": "Assets/Observatory.unity"},
            {
                "name": "Read phase",
                "action": "wait_state",
                "state_id": "weather.phase",
                "state_equals": "snow",
            },
        ],
    }


def observe(scenario, broken):
    rows = []
    for index, step in enumerate(scenario["steps"]):
        row = {
            "stage": "main",
            "iteration": 1,
            "step_index": index,
            "name": step["name"],
            "action": step["action"],
            "status": "passed",
        }
        if step["action"] == "wait_state":
            row["detail"] = 'State weather.phase: "snow"; expected: "snow".'
            if broken:
                row["status"] = "timed_out"
        rows.append(row)
    return {
        "scenario": scenario,
        "status": "timed_out" if broken else "succeeded",
        "steps": rows,
        "exit_code": 1 if broken else 0,
        "failure": {
            "target": "weather.phase",
            "code": "condition_unmet",
            "stage": "main",
            "iteration": 1,
            "step_index": len(rows) - 1,
            "expected": '"snow"',
            "actual": '"rain"',
        }
        if broken
        else None,
    }


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_failure",
        "unregistered",
        "contradiction",
        "wrong_step",
        "command_pass",
        "repair_wrong_detail",
    ],
)
def test_nonmatching_control_cannot_qualify_a_deletion(damage):
    value = definition()
    broken, repaired = observe(value, True), observe(value, False)
    if damage == "wrong_failure":
        broken["failure"]["code"] = "run_timeout"
    elif damage == "unregistered":
        broken["failure"]["actual"] = "provider not registered"
    elif damage == "contradiction":
        broken["failure"]["actual"] = '"snow"'
    elif damage == "wrong_step":
        broken["steps"][0]["status"] = "failed"
    elif damage == "command_pass":
        repaired["steps"] = []
    else:
        repaired["steps"][-1]["detail"] = 'State weather.phase: "rain"; expected: "snow".'
    assert replay.state_oracle(PlayScenario.model_validate(value), broken, repaired) is None


@pytest.mark.parametrize(
    "candidate",
    ["matching", "builder", "missing", "changed_source", "different_observation", "limit"],
)
def test_actual_cli_generates_subsequence_and_requires_both_controls(
    tmp_path, monkeypatch, verified_bundle, candidate
):
    _, _, template = verified_bundle
    args = []
    full = definition()
    reduced = {**full, "steps": [full["steps"][0], full["steps"][-1]]}
    manifests = {}
    for i, scenario in enumerate([full, reduced]):
        if i and candidate == "missing":
            break
        paths = []
        for phase in ["broken", "repaired"]:
            path = tmp_path / f"{phase}-{i}"
            path.mkdir()
            manifest = deepcopy(template)
            raw = json.dumps(scenario)
            source = phase + ("-drift" if i and candidate == "changed_source" else "")
            manifest.update(
                scenario_name=scenario["name"],
                definition=scenario,
                definition_json=raw,
                definition_hash=sha256(raw.encode()).hexdigest(),
                scene_paths=["Assets/Observatory.unity"],
                build_id=uuid4().hex,
                build_source_revision=source,
            )
            (path / "scenario-bundle.json").write_text(json.dumps(manifest))
            manifests[path] = manifest
            paths.append(str(path))
        args += (
            ["--candidate-pair", *paths] if i else ["--broken", paths[0], "--repaired", paths[1]]
        )
    launched = []

    def launch(options):
        m = manifests[options.build_directory]
        assert options.require_verified_payload and options.expected_build_id == m["build_id"]
        report = observe(m["definition"], m["build_source_revision"] == "broken")
        if candidate == "different_observation" and len(report["steps"]) == 2 and report["failure"]:
            report["failure"]["actual"] = '"hail"'
        directory = options.output_directory / uuid4().hex
        directory.mkdir(parents=True)
        report.update(
            job_id=directory.name,
            reproduction={
                k: m[k]
                for k in ("definition_hash", "build_id", "build_source_revision", "payload_hash")
            },
        )
        report["reproduction"]["environment"] = dict.fromkeys(replay.ENVIRONMENT_FIELDS, "fixture")
        (directory / "run.json").write_text(json.dumps(report))
        launched.append(options)
        return PlayerOutcome(report, directory, report["exit_code"])

    monkeypatch.setattr(replay, "run_player", launch)
    if candidate == "builder":
        del args[args.index("--candidate-pair") :]
        stub = tmp_path / "prepare"
        stub.touch()
        args.extend(["--builder", str(stub)])

        def prepare(argv, **options):
            _, request, *destinations = map(Path, argv)
            generated = PlayScenario.model_validate_json(request.read_bytes())
            assert generated == PlayScenario.model_validate(reduced)
            assert options == {"check": True, "timeout": 1800, "shell": False}
            for phase, destination in zip(("broken", "repaired"), destinations, strict=True):
                m = manifests[tmp_path / f"{phase}-1"]
                destination.mkdir()
                (destination / "scenario-bundle.json").write_text(json.dumps(m))
                manifests[destination] = m

        monkeypatch.setattr(replay.subprocess, "run", prepare)
    output = tmp_path / "out"
    command = CliRunner().invoke(
        play_scenario,
        [
            "reduce-replay",
            *args,
            "--output",
            str(output),
            "--max-trials",
            "1" if candidate == "limit" else "8",
        ],
    )
    if candidate == "changed_source":
        assert command.exit_code and not launched
        return
    assert command.exit_code == (2 if candidate in ("limit", "missing") else 0), (
        command.output,
        command.exception,
    )
    report = json.loads((output / "reduction.json").read_bytes())
    assert len(launched) == (2 if candidate in ("limit", "missing") else 4)
    assert report["before"]["kept_indices"] == [0, 1, 2]
    assert report["after"]["kept_indices"] == (
        [0, 2] if candidate in ("matching", "builder") else [0, 1, 2]
    )
    assert (
        report["after"]["checkpoint"]["repair_observation"]
        == 'State weather.phase: "snow"; expected: "snow".'
    )
    assert (output / report["after"]["broken"]["report"]).with_name("junit.xml").exists()
