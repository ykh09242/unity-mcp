"""Explicit graphical Player selection preserves owned process and payload guarantees."""

import json

from click.testing import CliRunner

from pydantic import ValidationError
import pytest

from cli.utils import play_scenario_player as player
from cli.main import cli
from cli.utils import play_scenario_player_session as session_module
from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player
from cli.utils.play_scenario_player_session import PlayerSessionOptions, run_player_session
from cli.utils.play_scenario_player_payload import PlayerPayloadError
from cli.utils.play_scenario_soak import SoakRequest
from cli.utils.play_scenario_soak_reports import run_player_soak

from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_play_scenario_soak import soak_child


@pytest.mark.parametrize("graphical", [False, True])
def test_graphical_player_only_changes_batchmode_argument(
    verified_bundle, child, monkeypatch, graphical
):
    build, output, _manifest = verified_bundle
    captured = []
    launch = player.subprocess.Popen

    def start(arguments, **keywords):
        captured.append((arguments, keywords))
        return launch(arguments, **keywords)

    monkeypatch.setattr(player.subprocess, "Popen", start)
    outcome = run_player(
        PlayerRunOptions(build, output, graphical=graphical, require_verified_payload=True)
    )
    arguments, keywords = captured[0]
    expected = [
        str(build.resolve() / "MCPScenarioPlayer.exe"),
        "--mcp-scenario-request",
        str(outcome.directory / "request.json"),
    ]
    if not graphical:
        expected.append("-batchmode")
    expected.extend(["-logFile", "-"])
    assert arguments == expected
    assert "-nographics" not in arguments
    assert keywords["shell"] is False
    assert keywords["cwd"] == build.resolve()
    assert outcome.exit_code == 0
    assert outcome.diagnostics["payload_verification"] == "launcher_admission"
    receipt = json.loads((outcome.directory / "process.json").read_bytes())
    assert receipt["process_ended"] and receipt["actual_exit_code"] == 0
    assert b"owned output" in (outcome.directory / "player.log").read_bytes()


@pytest.mark.parametrize("graphical", [False, True])
def test_session_forwards_graphical_to_every_owned_batch(
    verified_bundle, child, monkeypatch, graphical
):
    build, output, _manifest = verified_bundle
    observed = []
    launch = session_module.run_player

    def run(options):
        observed.append(options)
        return launch(options)

    monkeypatch.setattr(session_module, "run_player", run)
    summary, _directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="compare",
            iterations=2,
            batch_size=2,
            interval_seconds=0.0,
            graphical=graphical,
        )
    )
    assert code == 0
    assert summary["children_started"] == 3
    assert len(observed) == 3
    assert all(options.graphical is graphical for options in observed)
    assert all(options.require_verified_payload for options in observed)
    assert all(options.deadline_monotonic is not None for options in observed)
    assert all(process.poll() == 0 for process in child[1])


@pytest.mark.parametrize("graphical", [False, True])
def test_soak_preserves_graphical_through_owned_job_assignment(
    verified_bundle, soak_child, monkeypatch, graphical
):
    build, output, _manifest = verified_bundle
    observed = []
    launch = player.subprocess.Popen

    def start(arguments, **keywords):
        observed.append(arguments)
        return launch(arguments, **keywords)

    monkeypatch.setattr(player.subprocess, "Popen", start)
    outcome = run_player_soak(
        PlayerRunOptions(build, output, graphical=graphical, soak=SoakRequest(iterations=2))
    )
    assert outcome.exit_code == 0
    assert len(observed) == 1
    assert ("-batchmode" in observed[0]) is (not graphical)
    assert "-nographics" not in observed[0]
    assert outcome.summary.attempted_iterations == 2
    assert soak_child[1][0].poll() == 0


def test_graphical_selection_preserves_verified_payload_admission(bundle, monkeypatch):
    build, output, _manifest = bundle

    def forbidden(*_arguments, **_keywords):
        pytest.fail("Unverified payload was launched")

    monkeypatch.setattr(player.subprocess, "Popen", forbidden)
    with pytest.raises(PlayerLaunchError, match="verified v2"):
        run_player(PlayerRunOptions(build, output, graphical=True, require_verified_payload=True))


@pytest.mark.parametrize("value", [1, "true", None])
def test_graphical_options_require_explicit_boolean(tmp_path, value):
    with pytest.raises(PlayerLaunchError, match="boolean"):
        PlayerRunOptions(tmp_path, tmp_path / "output", graphical=value)
    with pytest.raises(ValidationError):
        PlayerSessionOptions(
            build_directory=tmp_path, output_directory=tmp_path / "output", graphical=value
        )


def test_existing_callers_keep_batchmode_default(tmp_path):
    assert PlayerRunOptions(tmp_path, tmp_path / "output").graphical is False
    assert (
        PlayerSessionOptions(
            build_directory=tmp_path, output_directory=tmp_path / "output"
        ).graphical
        is False
    )


def test_graphical_player_rejects_changed_verified_payload_before_launch(
    verified_bundle, monkeypatch
):
    build, output, manifest = verified_bundle
    (build / manifest["executable"]).write_bytes(b"changed immutable Player")

    def forbidden(*_arguments, **_keywords):
        pytest.fail("Changed verified payload was launched")

    monkeypatch.setattr(player.subprocess, "Popen", forbidden)
    with pytest.raises(PlayerPayloadError):
        run_player(PlayerRunOptions(build, output, graphical=True))


def test_graphical_player_deadline_still_cancels_and_finalizes_owned_child(verified_bundle, child):
    build, output, _manifest = verified_bundle
    child[0][0] = "cancel"
    outcome = run_player(
        PlayerRunOptions(build, output, graphical=True, timeout_seconds=1, cleanup_wait_seconds=1)
    )
    assert outcome.exit_code == 1
    assert outcome.diagnostics["cancellation_sent"]
    assert outcome.diagnostics["cleanup_state"] == "native_finalized"
    assert outcome.report["status"] == "cancelled"
    assert child[1][0].poll() == 1


@pytest.mark.parametrize("command", ["player-run", "player-soak", "player-session"])
@pytest.mark.parametrize("graphical", [False, True])
def test_public_player_commands_forward_graphical_to_owned_launch(
    verified_bundle, request, monkeypatch, command, graphical
):
    build, output, _manifest = verified_bundle
    request.getfixturevalue("soak_child" if command == "player-soak" else "child")
    launch = player.subprocess.Popen
    captured = []

    def start(arguments, **keywords):
        captured.append(arguments)
        return launch(arguments, **keywords)

    monkeypatch.setattr(player.subprocess, "Popen", start)
    arguments = [
        "--format",
        "json",
        "play-scenario",
        command,
        str(build),
        "--output-dir",
        str(output),
    ]
    if graphical:
        arguments.append("--graphical")
    if command in {"player-session", "player-soak"}:
        arguments.extend(["--iterations", "2"])
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert captured
    assert all(("-batchmode" in arguments) is (not graphical) for arguments in captured)
    assert all("-nographics" not in arguments for arguments in captured)
