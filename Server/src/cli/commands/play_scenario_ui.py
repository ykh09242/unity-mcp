"""Capture and compare explicit semantic UI snapshot artifacts."""

import json
from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from cli.utils.play_scenario_analysis_io import AnalysisError, safe_local_path, write_new
from cli.utils.play_scenario_ui import UiComparisonPolicy, compare_ui_snapshots
from models.ui_snapshots import UiSnapshot


@click.command("capture-ui-snapshot")
@click.option("--framework", required=True, type=click.Choice(["ugui", "uitoolkit"]))
@click.option("--target", required=True, help="Unique scene hierarchy path or instance ID.")
@click.option("--max-nodes", default=200, type=click.IntRange(1, 1000))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@handle_unity_errors
def capture_ui_snapshot(framework: str, target: str, max_nodes: int, output: Path) -> None:
    """Save a complete bounded UI tree to a new file; missing evidence fails capture."""
    config = get_config()
    try:
        path = safe_local_path(output)
        if path.exists():
            raise AnalysisError("Snapshot output already exists")
        response = run_command(
            "capture_ui_snapshot",
            {"framework": framework, "target": target, "max_nodes": max_nodes},
            config,
        )
        result = response.get("result", response)
        if result.get("success") is not True:
            raise AnalysisError(
                str(result.get("error") or result.get("message") or "Capture failed")
            )
        snapshot = UiSnapshot.model_validate_json(json.dumps(result.get("data"), allow_nan=False))
        content = snapshot.model_dump_json(indent=2).encode("utf-8")
        if len(content) > 2 * 1024 * 1024:
            raise AnalysisError("Snapshot exceeds the 2 MiB saved artifact bound")
        write_new(path, content)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(format_output({"success": True, "path": str(path)}, config.format))


@click.command("compare-ui-snapshots")
@click.argument("baseline", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("candidate", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--geometry-tolerance", default=0.0, type=click.FloatRange(0, 10000))
@click.option(
    "--require-occlusion", is_flag=True, help="Require actual occlusion proof (unavailable)."
)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def compare_saved_ui_snapshots(
    baseline: Path,
    candidate: Path,
    geometry_tolerance: float,
    require_occlusion: bool,
    output: Path | None,
) -> None:
    """Compare saved JSONs offline: exit 0 same, 1 changed, 2 not comparable."""
    try:
        result = compare_ui_snapshots(
            baseline, candidate, UiComparisonPolicy(geometry_tolerance, require_occlusion)
        )
        exit_code = 0 if result["status"] == "same" else 1
        if output is not None:
            write_new(output, json.dumps(result, indent=2).encode("utf-8"))
    except (OSError, ValueError) as exc:
        result = {
            "schema_version": 1,
            "status": "not_comparable",
            "compared": False,
            "error": str(exc)[:2048],
        }
        exit_code = 2
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit(exit_code)


def register_ui_commands(group: click.Group) -> None:
    """Attach UI capture and offline comparison to play-scenario."""
    group.add_command(capture_ui_snapshot)
    group.add_command(compare_saved_ui_snapshots)
