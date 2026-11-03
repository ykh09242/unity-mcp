"""Lighting CLI commands."""

import math
import struct

import click
from typing import Optional, Tuple

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import (
    run_command,
    handle_unity_errors,
    UnityCommandError,
    UnityConnectionError,
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

    Configuration failures retain the created object and confirmed progress in
    lightingCreation. Inspect that object before retrying to avoid duplicates.
    A lost response leaves the failed step's outcome unknown.

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
        raise UnityCommandError(create_result)

    created_data = create_result.get("data")
    completed_steps = ["create_gameobject"]

    def configuration_error(
        error: UnityCommandError | UnityConnectionError, step: str
    ) -> UnityCommandError:
        response = (
            error.response
            if isinstance(error, UnityCommandError)
            else {"success": False, "error": str(error)}
        )
        failure_data = response.get("data")
        outcome_unknown = (
            isinstance(error, UnityConnectionError)
            or response.get("code") == "command_outcome_unknown"
            or (isinstance(failure_data, dict) and failure_data.get("reason") == "outcome_unknown")
        )
        return UnityCommandError(
            {
                **response,
                "lightingCreation": {
                    "partialExecution": True,
                    "createdObject": created_data,
                    "requestedName": name,
                    "completedSteps": list(completed_steps),
                    "failedStep": step,
                    "failedStepOutcome": "unknown" if outcome_unknown else "failed",
                    "hint": (
                        "Inspect the existing object before retrying. Repeating lighting create "
                        "can create a duplicate."
                    ),
                },
            }
        )

    target = created_data.get("instanceID") if isinstance(created_data, dict) else None
    if target is None:
        raise configuration_error(
            UnityCommandError(
                {
                    "success": False,
                    "error": "Light creation response is missing data.instanceID; cannot configure the created object.",
                }
            ),
            "resolve_created_identity",
        )

    def configure(step: str, params: dict[str, object]) -> None:
        try:
            result = run_command("manage_components", params, config)
            if not result.get("success"):
                raise UnityCommandError(result)
        except (UnityCommandError, UnityConnectionError) as error:
            raise configuration_error(error, step) from error
        completed_steps.append(step)

    # Step 2: Add Light component using manage_components
    configure(
        "add_light_component",
        {
            "action": "add",
            "target": target,
            "search_method": "by_id",
            "componentType": "Light",
        },
    )

    # Step 3: Set light type using manage_components set_property
    configure(
        "set_type",
        {
            "action": "set_property",
            "target": target,
            "search_method": "by_id",
            "componentType": "Light",
            "property": "type",
            "value": light_type,
        },
    )

    # Step 4: Set color if provided
    if color is not None:
        configure(
            "set_color",
            {
                "action": "set_property",
                "target": target,
                "search_method": "by_id",
                "componentType": "Light",
                "property": "color",
                "value": {"r": color[0], "g": color[1], "b": color[2], "a": 1},
            },
        )

    # Step 5: Set intensity if provided
    if intensity is not None:
        configure(
            "set_intensity",
            {
                "action": "set_property",
                "target": target,
                "search_method": "by_id",
                "componentType": "Light",
                "property": "intensity",
                "value": intensity,
            },
        )

    # Output the result
    click.echo(format_output(create_result, config.format))
    if config.format != "json":
        print_success(f"Created {light_type} light: {name}")
