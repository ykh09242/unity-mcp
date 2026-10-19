"""UI CLI commands - placeholder for future implementation."""

import json
import math
import click
from typing import Optional

from cli.utils.config import get_config
from cli.utils.output import format_output, print_success
from cli.utils.connection import run_command, handle_unity_errors, UnityCommandError


@click.group()
def ui():
    """UI operations - create and modify UI elements."""
    pass


def _created_instance_id(result: dict) -> int:
    """Require the native transaction's created identity before printing success."""
    data = result.get("data")
    value = data.get("instanceID") if isinstance(data, dict) else None
    if type(value) in (int, str):
        try:
            instance_id = int(value)
            if instance_id != 0 and -(2**31) <= instance_id < 2**31:
                return instance_id
        except ValueError:
            pass
    raise UnityCommandError(
        {"success": False, "error": "Creation returned no valid instanceID.", "data": data}
    )


@ui.command("create-canvas")
@click.argument("name")
@click.option(
    "--render-mode",
    type=click.Choice(["ScreenSpaceOverlay", "ScreenSpaceCamera", "WorldSpace"]),
    default="ScreenSpaceOverlay",
    help="Canvas render mode.",
)
@handle_unity_errors
def create_canvas(name: str, render_mode: str):
    """Create a new Canvas.

    \b
    Examples:
        unity-mcp ui create-canvas "MainUI"
        unity-mcp ui create-canvas "WorldUI" --render-mode WorldSpace
    """
    config = get_config()
    result = run_command(
        "manage_ugui",
        {
            "action": "create_cli",
            "element_type": "canvas",
            "name": name,
            "render_mode": render_mode,
        },
        config,
    )
    _created_instance_id(result)
    click.echo(format_output(result, config.format))
    if config.format != "json":
        print_success(f"Created Canvas: {name}")


@ui.command("create-text")
@click.argument("name")
@click.option("--parent", "-p", required=True, help="Parent Canvas or UI element.")
@click.option("--text", "-t", default="New Text", help="Initial text content.")
@click.option("--position", nargs=2, type=float, default=None, help="Anchored position X Y.")
@handle_unity_errors
def create_text(name: str, parent: str, text: str, position: tuple[float, float] | None):
    """Create a UI Text element (TextMeshPro).

    \b
    Examples:
        unity-mcp ui create-text "TitleText" --parent "MainUI" --text "Hello World"
    """
    if position is not None and not all(
        math.isfinite(value) and abs(value) <= 3.4028234663852886e38 for value in position
    ):
        raise click.BadParameter(
            "Coordinates must be finite Unity floats.", param_hint="--position"
        )
    config = get_config()
    result = run_command(
        "manage_ugui",
        {
            "action": "create_cli",
            "element_type": "text",
            "name": name,
            "parent": parent,
            "text": text,
            "position": list(position if position is not None else (0, 0)),
        },
        config,
    )
    _created_instance_id(result)
    click.echo(format_output(result, config.format))
    if config.format != "json":
        print_success(f"Created Text: {name}")


@ui.command("create-button")
@click.argument("name")
@click.option("--parent", "-p", required=True, help="Parent Canvas or UI element.")
@click.option("--text", "-t", default="Button", help="Button label text.")
@handle_unity_errors
def create_button(name: str, parent: str, text: str):
    """Create a UI Button.

    \b
    Examples:
        unity-mcp ui create-button "StartButton" --parent "MainUI" --text "Start Game"
    """
    config = get_config()
    result = run_command(
        "manage_ugui",
        {
            "action": "create_cli",
            "element_type": "button",
            "name": name,
            "parent": parent,
            "text": text,
        },
        config,
    )
    _created_instance_id(result)
    click.echo(format_output(result, config.format))
    if config.format != "json":
        print_success(f"Created Button: {name} (with label '{text}')")


@ui.command("create-image")
@click.argument("name")
@click.option("--parent", "-p", required=True, help="Parent Canvas or UI element.")
@click.option("--sprite", "-s", default=None, help="Sprite asset path.")
@handle_unity_errors
def create_image(name: str, parent: str, sprite: Optional[str]):
    """Create a UI Image.

    \b
    Examples:
        unity-mcp ui create-image "Background" --parent "MainUI"
        unity-mcp ui create-image "Icon" --parent "MainUI" --sprite "Assets/Sprites/icon.png"
    """
    config = get_config()
    params = {"action": "create_cli", "element_type": "image", "name": name, "parent": parent}
    if sprite:
        params["sprite"] = sprite
    result = run_command("manage_ugui", params, config)
    _created_instance_id(result)
    click.echo(format_output(result, config.format))
    if config.format != "json":
        print_success(f"Created Image: {name}")


@ui.command("set-image")
@click.argument("target")
@click.option(
    "--properties",
    required=True,
    help="Image JSON properties; sprite accepts null, asset path, or {path,name}.",
)
@handle_unity_errors
def set_image(target: str, properties: str) -> None:
    """Edit one existing uGUI Image through a single native Undo transaction."""

    def reject_nonfinite(_value: str) -> None:
        raise click.BadParameter("Finite JSON numbers are required.", param_hint="--properties")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            reject_nonfinite(value)
        return parsed

    try:
        parsed = json.loads(
            properties, parse_constant=reject_nonfinite, parse_float=parse_finite_float
        )
    except json.JSONDecodeError as error:
        raise click.BadParameter("Expected a JSON object.", param_hint="--properties") from error
    if not isinstance(parsed, dict) or not parsed:
        raise click.BadParameter("Expected a non-empty JSON object.", param_hint="--properties")
    config = get_config()
    result = run_command(
        "manage_ugui", {"action": "set_image", "target": target, "properties": parsed}, config
    )
    click.echo(format_output(result, config.format))
