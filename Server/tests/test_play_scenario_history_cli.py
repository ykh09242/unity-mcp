"""Public offline history CLI preserves outcomes in explicit diagnostic exports."""

import json

from click.testing import CliRunner

from cli.main import cli
from .play_scenario_history_fixtures import _session, offline_only
from .play_scenario_soak_compare_fixtures import _evidence


def test_history_cli_counts_evicted_soak_successes(tmp_path):
    # Given a complete three-iteration soak retaining only its last full report.
    directory = _evidence(tmp_path / "soak", [10, 20, 30])
    # When the explicit saved summary is analyzed through the CLI.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "analyze-history",
            str(directory / "soak.json"),
        ],
    )
    # Then every journal outcome is counted without launching a Player.
    assert result.exit_code == 0, result.output
    analysis = json.loads(result.output)
    assert analysis["groups"][0]["counts"]["passed"] == 3
    assert len(analysis["groups"][0]["outcomes"]) == 3


def test_public_cli_exports_failed_history_without_treating_analysis_as_execution(tmp_path):
    # Given a complete failing saved session and a new explicit export destination.
    directory = _session(tmp_path / "session")
    output = tmp_path / "history-analysis.json"
    # When the public root CLI analyzes the selected history offline.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "analyze-history",
            str(directory / "session.json"),
            "--output",
            str(output),
        ],
    )
    # Then successful analysis preserves the observed failed parent in a reviewable export.
    assert result.exit_code == 0, result.output
    exported = json.loads(output.read_text())
    assert exported["diagnostic_only"] is True
    assert exported["groups"][0]["parent_status"] == "failed"
    assert exported["groups"][0]["counts"]["failed"] == 2
