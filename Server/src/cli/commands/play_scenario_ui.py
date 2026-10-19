"""Capture and compare explicit semantic UI snapshot artifacts."""

import json
from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from cli.utils.play_scenario_analysis_io import (
    AnalysisError,
    read_bytes,
    safe_local_path,
    write_new,
)
from cli.utils.play_scenario_ui import UiComparisonPolicy, compare_ui_snapshots
from models.response_limits import bounded_json_text
from models.ui_snapshots import validate_ui_snapshot
from services.tools.capture_ui_snapshot import UiSnapshotRequest


def _read_identity_map(source: Path):
    """Read only the closed bounded wrapper; never echo an invalid ID/path payload."""
    try:

        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value

        text = read_bytes(source, 256 * 1024).decode("utf-8-sig")
        if bounded_json_text(text, max_bytes=256 * 1024, max_depth=8, max_nodes=10000) is None:
            raise ValueError()
        value = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) != {"identity_map"}:
            raise ValueError()
        return value["identity_map"]
    except (OSError, ValueError, TypeError):
        raise AnalysisError("identity_map file is invalid or exceeds its 256 KiB bound") from None


@click.command("capture-ui-snapshot")
@click.option("--framework", required=True, type=click.Choice(["ugui", "uitoolkit"]))
@click.option("--target", required=True, help="Unique scene hierarchy path or instance ID.")
@click.option("--max-nodes", default=200, type=click.IntRange(1, 1000))
@click.option(
    "--component-type",
    type=click.Choice(["auto", "ui_document", "panel_renderer"]),
    help="UI Toolkit component to capture; explicit selection records the actual component kind.",
)
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--identity-map-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional bounded JSON object containing identity_map; emits v3 comparison IDs, not mutation handles.",
)
@handle_unity_errors
def capture_ui_snapshot(
    framework: str,
    target: str,
    max_nodes: int,
    output: Path,
    identity_map_file: Path | None,
    component_type: str | None,
) -> None:
    """Save a complete bounded UI tree to a new file; missing evidence fails capture."""
    config = get_config()
    try:
        arguments = {"framework": framework, "target": target, "max_nodes": max_nodes}
        if component_type is not None:
            arguments["component_type"] = component_type
        try:
            request = UiSnapshotRequest(**arguments)
        except ValueError:
            raise AnalysisError("Capture parameters or component selection are invalid") from None
        path = safe_local_path(output)
        if path.exists():
            raise AnalysisError("Snapshot output already exists")
        if identity_map_file is not None:
            arguments["identity_map"] = _read_identity_map(identity_map_file)
            try:
                arguments = UiSnapshotRequest(**arguments).model_dump(
                    mode="json", exclude_none=True
                )
            except ValueError:
                raise AnalysisError(
                    "identity_map file or capture parameters are invalid or exceed supported bounds"
                ) from None
        response = run_command("capture_ui_snapshot", arguments, config)
        result = response.get("result", response)
        if result.get("success") is not True:
            raise AnalysisError("UI snapshot capture failed; native evidence is unavailable.")
        snapshot = validate_ui_snapshot(
            result.get("data"),
            expected_version=3 if identity_map_file is not None else 2,
            expected_component_type=request.component_type,
        )
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
