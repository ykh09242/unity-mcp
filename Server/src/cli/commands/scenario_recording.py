"""Explicitly polled Editor interaction recordings, saved as reviewable drafts."""

import click
from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from models.scenario_recording import ScenarioRecordingCommand
from pydantic import ValidationError


def _dispatch(**arguments) -> None:
    try:
        command = ScenarioRecordingCommand.model_validate(arguments)
    except ValidationError as error:
        raise click.UsageError(str(error)) from error
    config = get_config()
    result = run_command("manage_scenario_recording", command.wire_parameters(), config)
    click.echo(format_output(result, config.format))


def register_recording_commands(group: click.Group) -> None:
    """Attach standalone recording commands to an explicitly selected CLI group."""

    @group.command("start")
    @click.argument("name")
    @click.option("--max-events", type=click.IntRange(1, 127), default=31, show_default=True)
    @click.option(
        "--duration-seconds", type=click.IntRange(1, 3600), default=300, show_default=True
    )
    @click.option(
        "--scene-scope",
        type=click.Choice(["active", "all_loaded"]),
        default="active",
        show_default=True,
    )
    @handle_unity_errors
    def start(name: str, max_events: int, duration_seconds: int, scene_scope: str) -> None:
        """Observe supported Buttons in current Play Mode; return immediately."""
        _dispatch(
            action="start",
            name=name,
            max_events=max_events,
            duration_seconds=duration_seconds,
            **({"scene_scope": scene_scope} if scene_scope != "active" else {}),
        )

    @group.command("status")
    @click.argument("recording_id", required=False)
    @handle_unity_errors
    def status(recording_id: str | None) -> None:
        """Read the current or last session once, including draft and diagnostics."""
        _dispatch(action="status", recording_id=recording_id)

    @group.command("stop")
    @click.argument("recording_id")
    @handle_unity_errors
    def stop(recording_id: str) -> None:
        """Stop and save a NEW scenario with draft=true; review before running."""
        _dispatch(action="stop", recording_id=recording_id)

    @group.command("cancel")
    @click.argument("recording_id")
    @handle_unity_errors
    def cancel(recording_id: str) -> None:
        """Stop observation and discard the session without saving."""
        _dispatch(action="cancel", recording_id=recording_id)

    @group.command("checkpoint")
    @click.argument("recording_id")
    @click.argument("checkpoint_name")
    @handle_unity_errors
    def checkpoint(recording_id: str, checkpoint_name: str) -> None:
        """Add an explicitly named future screenshot, with no image assertion."""
        _dispatch(action="checkpoint", recording_id=recording_id, checkpoint_name=checkpoint_name)


@click.group("scenario-recording")
def scenario_recording() -> None:
    """Record Play Mode Button interactions and observed scene changes into a draft."""


register_recording_commands(scenario_recording)
