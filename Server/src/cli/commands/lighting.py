"""Lighting CLI commands."""

import math
import struct

import click
from typing import Optional, Tuple

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import run_command, handle_unity_errors, UnityCommandError


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

    # Step 1: Create empty GameObject with position
    create_result = run_command(
        "manage_gameobject",
        {
            "action": "create",
            "name": name,
            "position": list(position),
        },
        config,
    )

    if not (create_result.get("success")):
        click.echo(format_output(create_result, config.format))
        return

    created_data = create_result.get("data")
    target = created_data.get("instanceID") if isinstance(created_data, dict) else None
    if target is None:
        raise UnityCommandError(
            {
                "success": False,
                "error": "Light creation response is missing data.instanceID; cannot configure the created object.",
            }
        )

    # Step 2: Add Light component using manage_components
    add_result = run_command(
        "manage_components",
        {
            "action": "add",
            "target": target,
            "search_method": "by_id",
            "componentType": "Light",
        },
        config,
    )

    if not add_result.get("success"):
        click.echo(format_output(add_result, config.format))
        return

    # Step 3: Set light type using manage_components set_property
    type_result = run_command(
        "manage_components",
        {
            "action": "set_property",
            "target": target,
            "search_method": "by_id",
            "componentType": "Light",
            "property": "type",
            "value": light_type,
        },
        config,
    )

    if not type_result.get("success"):
        click.echo(format_output(type_result, config.format))
        return

    # Step 4: Set color if provided
    if color:
        color_result = run_command(
            "manage_components",
            {
                "action": "set_property",
                "target": target,
                "search_method": "by_id",
                "componentType": "Light",
                "property": "color",
                "value": {"r": color[0], "g": color[1], "b": color[2], "a": 1},
            },
            config,
        )

        if not color_result.get("success"):
            click.echo(format_output(color_result, config.format))
            return

    # Step 5: Set intensity if provided
    if intensity is not None:
        intensity_result = run_command(
            "manage_components",
            {
                "action": "set_property",
                "target": target,
                "search_method": "by_id",
                "componentType": "Light",
                "property": "intensity",
                "value": intensity,
            },
            config,
        )

        if not intensity_result.get("success"):
            click.echo(format_output(intensity_result, config.format))
            return

    # Output the result
    click.echo(format_output(create_result, config.format))
    if config.format != "json":
        print_success(f"Created {light_type} light: {name}")
