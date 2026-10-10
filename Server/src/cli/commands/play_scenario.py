"""CLI commands for saved Unity-owned Play Mode scenarios."""

from typing import TextIO

import click
from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from models.play_scenarios import PlayScenario, PlayScenarioCommand
from pydantic import ValidationError


def _dispatch(**arguments) -> None:
    """Validate locally before one REST command, preserving structured Unity output."""
    try:
        command = PlayScenarioCommand.model_validate(arguments)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    config = get_config()
    result = run_command("manage_play_scenario", command.wire_parameters(), config)
    click.echo(format_output(result, config.format))


@click.group("play-scenario")
def play_scenario():
    """Save and run repeatable Play Mode scenarios; status never waits or polls."""


@play_scenario.command("save")
@click.argument("definition", type=click.File("r", encoding="utf-8"))
@handle_unity_errors
def save(definition: TextIO):
    """Save a whole scenario from a UTF-8 JSON file (or - for stdin)."""
    raw = definition.read(65537)
    if len(raw.encode("utf-8")) > 65536:
        raise click.BadParameter("Scenario definition exceeds 64 KiB", param_hint="definition")
    try:
        scenario = PlayScenario.model_validate_json(raw)
    except ValidationError as exc:
        raise click.BadParameter(str(exc), param_hint="definition") from exc
    _dispatch(action="save", scenario=scenario)


@play_scenario.command("get")
@click.argument("name")
@handle_unity_errors
def get(name: str):
    """Get a saved scenario definition."""
    _dispatch(action="get", name=name)


@play_scenario.command("list")
@handle_unity_errors
def list_scenarios():
    """List saved scenario definitions."""
    _dispatch(action="list")


@play_scenario.command("delete")
@click.argument("name")
@handle_unity_errors
def delete(name: str):
    """Delete a saved scenario definition."""
    _dispatch(action="delete", name=name)


@play_scenario.command("run")
@click.argument("name")
@click.option("--job-id", default=None, help="Optional 32-character lowercase hex request key.")
@click.option("--repeat-count", type=click.IntRange(1, 10), default=1, show_default=True)
@click.option("--timeout-seconds", type=click.IntRange(1, 1800), default=300, show_default=True)
@handle_unity_errors
def run(name: str, job_id: str | None, repeat_count: int, timeout_seconds: int):
    """Start a Unity job and return immediately; use status for subsequent observations."""
    _dispatch(
        action="run",
        name=name,
        job_id=job_id,
        repeat_count=repeat_count,
        timeout_seconds=timeout_seconds,
    )


@play_scenario.command("status")
@click.argument("job_id")
@handle_unity_errors
def status(job_id: str):
    """Read the job report once, including per-step status and bounded failure logs."""
    _dispatch(action="status", job_id=job_id)


@play_scenario.command("cancel")
@click.argument("job_id")
@handle_unity_errors
def cancel(job_id: str):
    """Request cooperative cancellation; an in-flight native scene load cannot be cancelled."""
    _dispatch(action="cancel", job_id=job_id)
