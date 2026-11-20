"""Paired one-factor replay through the installed CLI and Player launch boundary."""

from collections import Counter
from copy import deepcopy
import json

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario import play_scenario
from cli.utils.play_scenario_player import PlayerRunOptions, PlayerLaunchError, run_player
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_play_scenario_replay import definition, observe, replay_controls


@pytest.mark.parametrize("missing", [True, False])
def test_one_factor_trials_use_the_same_checkpoint_before_and_after_reduction(
    tmp_path, replay_controls, missing
):
    pair, simulate, launches = replay_controls
    full = definition()
    full["steps"].insert(
        2, {"name": "Send key", "action": "input", "input": {"action": "key", "key": "a"}}
    )
    reduced = {**full, "steps": [full["steps"][i] for i in (0, 2, 3)]}
    broken, repaired = pair("full", full)
    output = tmp_path / "results"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "checkpoint_index": 3,
                "seed": 7,
                "attempts": 2,
                "kept_indices": [0, 2, 3],
                "variants": [
                    {"axis": "seed", "value": 8},
                    {"axis": "start_delay_frames", "value": 3},
                    {"axis": "input", "step_index": 2, "field": "key", "value": "b"},
                ],
            }
        )
    )
    args = [
        "trial-replay",
        "--plan",
        str(plan),
        "--broken",
        broken,
        "--repaired",
        repaired,
        "--reduced-pair",
        *pair("short", reduced),
        "--output",
        str(output),
    ]
    for label, scenario in (("full", full), ("short", reduced)):
        variant = deepcopy(scenario)
        variant["steps"][-2]["input"]["key"] = "b"
        if label == "full" or not missing:
            args.extend(["--candidate-pair", *pair(label + "-input", variant)])
    counts = Counter()

    def observation(manifest, options):
        scenario = manifest["definition"]
        source = manifest["build_source_revision"]
        key = (manifest["build_id"], options.replay_seed, options.start_delay_frames)
        counts[key] += 1
        fail = counts[key] % 2 == 1 and source == "broken"
        if options.start_delay_frames == 3:
            fail = True
        result = observe(scenario, fail)
        if fail and options.replay_seed == 8:
            result["failure"]["actual"] = '"hail"'
        return result

    simulate(observation)
    runner = CliRunner()
    refused = runner.invoke(play_scenario, [*args, "--max-trials", "15"])
    assert refused.exit_code and not launches and not output.exists()
    cli = runner.invoke(play_scenario, args)
    assert cli.exit_code == (2 if missing else 0), (cli.output, cli.exception)
    report = json.loads((output / "replay-trials.json").read_bytes())
    assert report["post_reduction"] == "reproduced"
    assert report["reproduced_condition_indices"] == ([0] if missing else [0, 3])
    for phase, cells in report["measurements"].items():
        baseline, seed, delay, input_cell = cells
        assert (
            baseline["denominator"] == 2
            and baseline["matching_rate"] == 0.5
            and baseline["intermittent"]
        )
        assert seed["matching_failures"] == 0 and seed["counts"]["other_outcome"] == 1
        assert delay["counts"]["repair_control_failed"] == 2
        if phase == "after" and missing:
            assert input_cell["denominator"] == 0 and input_cell["matching_rate"] is None
        else:
            assert input_cell["matching_failures"] == 1 and input_cell["denominator"] == 2
    assert len(launches) == (28 if missing else 32)


def test_seeded_player_requires_observed_native_settings(verified_bundle, child):
    build, output, _ = verified_bundle
    options = PlayerRunOptions(build, output, replay_seed=-9, start_delay_frames=2)
    accepted = run_player(options)
    assert accepted.report["reproduction"]["replay_seed"] == -9
    assert accepted.report["reproduction"]["start_delay_frames"] == 2
    child[0][0] = "ignore-replay-settings"
    with pytest.raises(PlayerLaunchError, match="player_report_invalid") as invalid:
        run_player(options)
    request = json.loads((invalid.value.directory / "request.json").read_bytes())
    assert request["replay_seed"] == -9 and request["start_delay_frames"] == 2
