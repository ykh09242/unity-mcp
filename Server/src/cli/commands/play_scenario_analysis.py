"""Register offline scenario outcome and screenshot analysis commands."""

import json
from pathlib import Path

import click
from PIL import Image
from pydantic import ValidationError

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_analysis_io import write_new
from cli.utils.play_scenario_flakiness import analyze_flakiness
from cli.utils.play_scenario_visual_compare import VisualPair, compare_visual, load_visual_policy
from cli.utils.play_scenario_visual_reports import CheckpointSelection, pair_visual_reports


@click.command("analyze-flakiness")
@click.argument("reports", nargs=-1, type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def analyze_saved_flakiness(reports: tuple[Path, ...], output: Path | None) -> None:
    """Analyze <=64 explicit run.json files offline. Exit 0 analyzed, 2 incomplete.

    No execution, retry or history discovery occurs. Session JSONL and soak parent reports
    are unsupported; an ordinary selected iteration cannot establish a whole soak outcome.
    Only compatible known definition/environment groups establish cross-run history.
    """
    try:
        analysis = analyze_flakiness(reports)
        result = analysis.to_dict()
        exit_code = 0 if analysis.status == "analyzed" else 2
        if output is not None:
            write_new(output, json.dumps(result, indent=2).encode("utf-8"))
    except (OSError, ValueError) as exc:
        result = {"schema_version": 1, "status": "not_comparable", "error": str(exc)}
        exit_code = 2
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit(exit_code)


@click.command("compare-visual")
@click.argument("baseline", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("candidate", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--policy", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
def compare_saved_visual(
    baseline: Path, candidate: Path, policy: Path, output_dir: Path | None
) -> None:
    """Compare explicit PNGs offline: exit 0 within budget, 1 exceeded, 2 not comparable.

    Strict policy declares fixed dimensions, RGBA channel thresholds, maximum changed
    pixel ratio and ignored rectangles. No resizing or implicit image matching occurs.
    Output directory optionally receives new bounded heatmap.png and diff.png.
    """
    try:
        result = compare_visual(
            VisualPair(baseline, candidate), load_visual_policy(policy), output_dir
        ).to_dict()
        exit_code = 0 if result["status"] == "within_budget" else 1
    except (
        OSError,
        SyntaxError,
        ValueError,
        ValidationError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        result = {
            "schema_version": 1,
            "status": "not_comparable",
            "compared": False,
            "error": str(exc)[:2048],
        }
        exit_code = 2
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit(exit_code)


@click.command("compare-visual-reports")
@click.argument("baseline", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("candidate", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--policy", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--checkpoint", required=True)
@click.option("--stage", required=True, type=click.Choice(["setup", "main", "cleanup"]))
@click.option("--step-index", required=True, type=click.IntRange(0, 63))
@click.option("--iteration", required=True, type=click.IntRange(1, 10))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
def compare_saved_visual_reports(
    baseline: Path,
    candidate: Path,
    policy: Path,
    checkpoint: str,
    stage: str,
    step_index: int,
    iteration: int,
    output_dir: Path | None,
) -> None:
    """Compare one named native capture offline: exit 0 within, 1 exceeded, 2 invalid.

    Capture paths must remain under each explicit report directory. Captured status,
    dimensions, SHA256 and exact stage/step/iteration pairing are required.
    """
    try:
        parsed_policy = load_visual_policy(policy)
        selection = CheckpointSelection.model_validate(
            {"name": checkpoint, "stage": stage, "step_index": step_index, "iteration": iteration}
        )
        pair = pair_visual_reports((baseline, candidate), selection, parsed_policy)
        result = compare_visual(pair, parsed_policy, output_dir).to_dict()
        result["checkpoint"] = selection.model_dump(mode="json")
        exit_code = 0 if result["status"] == "within_budget" else 1
    except (
        OSError,
        SyntaxError,
        ValueError,
        ValidationError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        result = {
            "schema_version": 1,
            "status": "not_comparable",
            "compared": False,
            "error": str(exc)[:2048],
        }
        exit_code = 2
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit(exit_code)


def register_analysis_commands(group: click.Group) -> None:
    """Attach offline commands without changing native execution behavior."""
    group.add_command(analyze_saved_flakiness)
    group.add_command(compare_saved_visual)
    group.add_command(compare_saved_visual_reports)
