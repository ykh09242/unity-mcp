"""Offline named regression baseline commands for the play-scenario group."""

from pathlib import Path

import click
from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_soak_baselines import (
    read_registry,
    register_baseline,
    select_baseline,
    set_baseline_pin,
    validate_entry,
)
from cli.utils.play_scenario_soak_budget import check_soak_budget
from cli.utils.play_scenario_soak_compare import compare_soak_metrics
from pydantic import JsonValue


def _emit(value: JsonValue) -> None:
    click.echo(format_output(value, get_config().format))


def _selected(ctx: click.Context) -> Path:
    return Path(ctx.find_object(str))


@click.group("baseline")
@click.option(
    "--registry",
    required=True,
    type=click.Path(path_type=Path),
    help="Explicit local registry file or existing directory.",
)
@click.pass_context
def baseline(ctx: click.Context, registry: Path) -> None:
    """Register immutable successful soaks and audit explicit pin selections."""
    ctx.obj = str(registry)


@baseline.command("register")
@click.argument("name")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.pass_context
def register(ctx: click.Context, name: str, directory: Path) -> None:
    """Admit one saved soak under a new immutable name."""
    try:
        result = register_baseline(_selected(ctx), name, directory)
    except (OSError, ValueError, RecursionError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(result.model_dump(mode="json"))


@baseline.command("list")
@click.pass_context
def list_baselines(ctx: click.Context) -> None:
    """List and revalidate only explicitly registered saved evidence."""
    try:
        result = read_registry(_selected(ctx))
        for entry in result.entries:
            validate_entry(entry)
    except (OSError, ValueError, RecursionError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(result.model_dump(mode="json"))


@baseline.command("pin")
@click.argument("name")
@click.option("--reason", required=True)
@click.pass_context
def pin(ctx: click.Context, name: str, reason: str) -> None:
    """Pin a registered baseline without changing its evidence or other pins."""
    _change_pin(ctx, name, (True, reason))


@baseline.command("unpin")
@click.argument("name")
@click.option("--reason", required=True)
@click.pass_context
def unpin(ctx: click.Context, name: str, reason: str) -> None:
    """Unpin a baseline while preserving its registration and audit trail."""
    _change_pin(ctx, name, (False, reason))


def _change_pin(ctx: click.Context, name: str, change: tuple[bool, str]) -> None:
    try:
        result = set_baseline_pin(_selected(ctx), name, change)
    except (OSError, ValueError, RecursionError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(result.model_dump(mode="json"))


@baseline.command("history")
@click.pass_context
def history(ctx: click.Context) -> None:
    """Show the complete bounded registration and pin provenance."""
    try:
        result = read_registry(_selected(ctx))
    except (OSError, ValueError, RecursionError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        {
            "schema_version": 1,
            "revision": result.revision,
            "history": [event.model_dump(mode="json") for event in result.history],
        }
    )


@baseline.command("compare")
@click.argument("candidate", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--name", help="Require this compatible pinned name; required if several match.")
@click.pass_context
def compare(ctx: click.Context, candidate: Path, name: str | None) -> None:
    """Compare a candidate against one compatible, revalidated pinned baseline."""
    try:
        entry = select_baseline(_selected(ctx), candidate, name)
        result = compare_soak_metrics(Path(entry.directory), candidate)
        validate_entry(entry)
    except (OSError, ValueError, RecursionError) as exc:
        _emit({"status": "not_comparable", "error": str(exc)[:512]})
        raise click.exceptions.Exit(2) from exc
    _emit(
        {
            **result.to_dict(),
            "baseline_name": entry.name,
            "baseline_evidence_sha256": entry.evidence_sha256,
        }
    )
    if result.status != "compared":
        raise click.exceptions.Exit(2)


@baseline.command("check")
@click.argument("candidate", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--name", help="Require this compatible pinned name; required if several match.")
@click.option(
    "--policy", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_context
def check(ctx: click.Context, candidate: Path, name: str | None, policy: Path) -> None:
    """Apply the existing exact budget gate to a revalidated named baseline."""
    try:
        entry = select_baseline(_selected(ctx), candidate, name)
        result = check_soak_budget(Path(entry.directory), candidate, policy)
        validate_entry(entry)
    except (OSError, ValueError, RecursionError) as exc:
        _emit({"status": "not_comparable", "error": str(exc)[:512]})
        raise click.exceptions.Exit(2) from exc
    _emit(
        {
            **result.to_dict(),
            "baseline_name": entry.name,
            "baseline_evidence_sha256": entry.evidence_sha256,
        }
    )
    if result.status != "within_budget":
        raise click.exceptions.Exit(1 if result.status == "budget_exceeded" else 2)


def register_baseline_commands(group: click.Group) -> None:
    """Attach the offline baseline subgroup without querying or launching Unity."""
    group.add_command(baseline)
