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


@pytest.fixture
def replay_controls(tmp_path, monkeypatch, verified_bundle):
    """Use real bundle admission, CLI and reports with deterministic simulated process outcomes."""
    _, _, template = verified_bundle
    manifests = {}
    launches = []

    def pair(label, scenario):
        paths = []
        for source in ("broken", "repaired"):
            path = tmp_path / f"{label}-{source}"
            path.mkdir()
            raw = json.dumps(scenario)
            manifest = deepcopy(template)
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
        return paths

    def simulate(observation):
        def launch(options):
            manifest = manifests[options.build_directory]
            report = observation(manifest, options)
            directory = options.output_directory / uuid4().hex
            directory.mkdir(parents=True)
            reproduction = {
                key: manifest[key]
                for key in ("build_id", "build_source_revision", "payload_hash", "definition_hash")
            }
            reproduction.update(
                environment=dict.fromkeys(replay.ENVIRONMENT_FIELDS, "fixture"),
                replay_seed=options.replay_seed,
                start_delay_frames=options.start_delay_frames,
            )
            report.update(job_id=directory.name, reproduction=reproduction)
            (directory / "run.json").write_text(json.dumps(report))
            launches.append(options)
            return PlayerOutcome(report, directory, report["exit_code"])

        monkeypatch.setattr(replay, "run_player", launch)

    return pair, simulate, launches


def test_event_dependencies_cross_pinned_steps_and_respect_original_indices():
    scenario = definition()
    scenario["steps"][1:1] = [
        {"name": "Mark", "action": "mark_events", "event_id": "weather", "event_mark": "begin"},
        {
            **scenario["steps"][1],
            "name": "Capture",
            "screenshot": {"name": "sky", "width": 16, "height": 16},
        },
        {
            "name": "Read events",
            "action": "wait_events",
            "event_since": "begin",
            "event_name": "snow",
            "event_count": 1,
        },
    ]
    parsed = PlayScenario.model_validate(scenario)
    candidates = list(replay.deletion_candidates(parsed, [0, 1, 2, 3, 4, 5], {0, 2, 5}, [(1, 4)]))
    assert candidates[:2] == [(0, 2, 3, 5), (0, 2, 4, 5)]
    assert all({0, 2, 5}.issubset(kept) for kept in candidates)


def test_removing_a_dependent_pair_can_preserve_failure_when_either_single_deletion_loses_it(
    tmp_path, replay_controls
):
    pair, simulate, launches = replay_controls
    full = definition()
    full["steps"].insert(2, {**full["steps"][1], "name": "Read sky again"})
    broken, repaired = pair("full", full)
    arguments = ["reduce-replay", "--broken", broken, "--repaired", repaired]
    for indices in ([0, 1, 3], [0, 2, 3], [0, 3]):
        value = {**full, "steps": [full["steps"][i] for i in indices]}
        arguments.extend(["--candidate-pair", *pair("".join(map(str, indices)), value)])
    simulate(
        lambda m, _: observe(
            m["definition"],
            m["build_source_revision"] == "broken" and len(m["definition"]["steps"]) != 3,
        )
    )
    for mode, extra, kept in (("single", [], [0, 1, 2, 3]), ("group", ["--grouped"], [0, 3])):
        output = tmp_path / mode
        cli = CliRunner().invoke(play_scenario, [*arguments, *extra, "--output", str(output)])
        assert cli.exit_code == 0, (cli.output, cli.exception)
        report = json.loads((output / "reduction.json").read_bytes())
        assert report["after"]["kept_indices"] == kept
        assert report["after"]["checkpoint"]["original_index"] == 3
    assert len(launches) == 10
