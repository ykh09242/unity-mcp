"""Lighting CLI commands."""

import math
import struct

import click
from typing import Optional, Tuple

from cli.utils.config import get_config
from cli.utils.output import format_output, print_success
from cli.utils.connection import (
    run_command,
    handle_unity_errors,
    UnityCommandError,
)


def _validate_light_number(value: float, parameter: str) -> None:
    """Require numbers that the Unity float receiver can represent before creating a light."""
    if not math.isfinite(value):
        raise click.BadParameter("Expected a finite number.", param_hint=f"--{parameter}")
    try:
        if not math.isfinite(struct.unpack("f", struct.pack("f", value))[0]):
            raise OverflowError
    except OverflowError as exc:
        raise click.BadParameter(
            "Value exceeds the Unity float range.", param_hint=f"--{parameter}"
        ) from exc


@click.group()
def lighting():
    """Lighting operations - create, modify lights and lighting settings."""
    pass


@lighting.command("create")
@click.argument("name")
@click.option(
    "--type",
    "-t",
    "light_type",
    type=click.Choice(["Directional", "Point", "Spot", "Area"]),
    default="Point",
    help="Type of light to create.",
)
@click.option(
    "--position", "-pos", nargs=3, type=float, default=(0, 3, 0), help="Position as X Y Z."
)
@click.option("--color", "-c", nargs=3, type=float, default=None, help="Color as R G B (0-1).")
@click.option("--intensity", "-i", default=None, type=float, help="Light intensity.")
@handle_unity_errors
def create(
    name: str,
    light_type: str,
    position: Tuple[float, float, float],
    color: Optional[Tuple[float, float, float]],
    intensity: Optional[float],
):
    """Create a new light.

    The Editor creates the GameObject and configures its Light in one command.
    If the response is lost, inspect the scene before retrying.

    \b
    Examples:
        unity-mcp lighting create "MainLight" --type Directional
        unity-mcp lighting create "PointLight1" --position 0 5 0 --intensity 2
        unity-mcp lighting create "RedLight" --type Spot --color 1 0 0
    """
    for value in position:
        _validate_light_number(value, "position")
    if color is not None:
        for value in color:
            _validate_light_number(value, "color")
    if intensity is not None:
        _validate_light_number(intensity, "intensity")

    config = get_config()

    properties: dict[str, object] = {"type": light_type}
    if color is not None:
        properties["color"] = {"r": color[0], "g": color[1], "b": color[2], "a": 1}
    if intensity is not None:
        properties["intensity"] = intensity

    create_result = run_command(
        "manage_gameobject",
        {
            "action": "create",
            "name": name,
            "position": list(position),
            "componentsToAdd": [{"typeName": "Light", "properties": properties}],
        },
        config,
    )
    if not create_result.get("success"):
        raise UnityCommandError(create_result)
    data = create_result.get("data")
    if not isinstance(data, dict) or data.get("instanceID") is None:
        raise UnityCommandError(
            {
                "success": False,
                "error": "Light creation response is missing data.instanceID. Inspect the scene before retrying.",
                "data": data,
            }
        )
    click.echo(format_output(create_result, config.format))
    if config.format != "json":
        print_success(f"Created {light_type} light: {name}")
