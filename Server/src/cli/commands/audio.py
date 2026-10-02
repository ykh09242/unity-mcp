"""AudioSource playback controls and volume configuration."""

import math
import click
from typing import Optional, Any

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors
from cli.utils.constants import SEARCH_METHOD_CHOICE_BASIC


@click.group()
def audio():
    """AudioSource playback in Play mode and volume settings."""
    pass


@audio.command("play")
@click.argument("target")
@click.option(
    "--clip", "-c",
    default=None,
    help="Audio clip path to play."
)
@click.option(
    "--search-method",
    type=SEARCH_METHOD_CHOICE_BASIC,
    default=None,
    help="How to find the target."
)
@handle_unity_errors
def play(target: str, clip: Optional[str], search_method: Optional[str]):
    """Request playback on an existing AudioSource in Play mode.

    \b
    Examples:
        unity-mcp audio play "MusicPlayer"
        unity-mcp audio play "SFXSource" --clip "Assets/Audio/explosion.wav"
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "play",
        "target": target,
    }

    if clip is not None:
        params["clip"] = clip

    if search_method:
        params["searchMethod"] = search_method

    result = run_command("manage_audio", params, config)
    click.echo(format_output(result, config.format))


@audio.command("stop")
@click.argument("target")
@click.option(
    "--search-method",
    type=SEARCH_METHOD_CHOICE_BASIC,
    default=None,
    help="How to find the target."
)
@handle_unity_errors
def stop(target: str, search_method: Optional[str]):
    """Request stopping an existing AudioSource in Play mode.

    \b
    Examples:
        unity-mcp audio stop "MusicPlayer"
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "stop",
        "target": target,
    }

    if search_method:
        params["searchMethod"] = search_method

    result = run_command("manage_audio", params, config)
    click.echo(format_output(result, config.format))


@audio.command("volume")
@click.argument("target")
@click.argument("level", type=float)
@click.option(
    "--search-method",
    type=SEARCH_METHOD_CHOICE_BASIC,
    default=None,
    help="How to find the target."
)
@handle_unity_errors
def volume(target: str, level: float, search_method: Optional[str]):
    """Set audio volume on a target's AudioSource.

    \b
    Examples:
        unity-mcp audio volume "MusicPlayer" 0.5
    """
    if not math.isfinite(level) or not 0 <= level <= 1:
        raise click.BadParameter("must be a finite number between 0 and 1", param_hint="level")

    config = get_config()

    params: dict[str, Any] = {
        "action": "set_property",
        "target": target,
        "componentType": "AudioSource",
        "property": "volume",
        "value": level,
    }

    if search_method:
        params["searchMethod"] = search_method

    result = run_command("manage_components", params, config)
    click.echo(format_output(result, config.format))
