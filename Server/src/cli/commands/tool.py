"""Tool listing and inspection of actual server-advertised schemas."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error
from cli.utils.connection import run_list_custom_tools, handle_unity_errors


def _list_custom_tools() -> None:
    config = get_config()
    result = run_list_custom_tools(config)
    if config.format != "text":
        click.echo(format_output(result, config.format))
        return

    if not isinstance(result, dict) or not result.get("success", True):
        click.echo(format_output(result, config.format))
        return

    tools = result.get("tools")
    if tools is None:
        data = result.get("data", {})
        tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        click.echo(format_output(result, config.format))
        return

    click.echo(f"Custom tools ({len(tools)}):")
    for i, t in enumerate(tools):
        name = t.get("name") if isinstance(t, dict) else str(t)
        click.echo(f"  [{i}] {name}")


@click.group("tool")
def tool():
    """List project tools or inspect a named server-advertised tool."""
    pass


@tool.command("list")
@handle_unity_errors
def list_tools():
    """List custom tools registered for the active Unity project."""
    _list_custom_tools()


def _validate_name(_ctx, _param, value):
    if not 1 <= len(value) <= 128 or not all(
        character.isalnum() or character in "_.-" for character in value
    ):
        raise click.BadParameter("use 1..128 letters, digits, underscores, dots or hyphens")
    return value


def _inspect(name, protocol, *, schema_only=False):
    import json
    from functools import partial

    import anyio
    from cli.utils.tool_description import inspect_tool

    config = get_config()
    receipt = anyio.run(partial(inspect_tool, config, name, protocol=protocol))
    payload = receipt.model_dump(mode="json")
    if schema_only:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(format_output(payload, config.format))
    raise click.exceptions.Exit(receipt.exit_code)


@tool.command("describe")
@click.argument("name", callback=_validate_name)
@click.option(
    "--protocol", type=click.Choice(["auto", "legacy"]), default="auto", show_default=True
)
def describe(name, protocol):
    """Describe NAME for this CLI connection; hidden-tool causes may be unknown."""
    _inspect(name, protocol)


@tool.command("schema")
@click.argument("name", callback=_validate_name)
@click.option(
    "--protocol", type=click.Choice(["auto", "legacy"]), default="auto", show_default=True
)
def schema(name, protocol):
    """Print NAME's complete schema receipt as JSON, regardless of --format.

    Project-declared metadata is distinct from an advertised callable schema.
    """
    _inspect(name, protocol, schema_only=True)


@click.group("custom_tool")
def custom_tool():
    """Alias for tool management (custom tools)."""
    pass


@custom_tool.command("list")
@handle_unity_errors
def list_custom_tools():
    """List custom tools registered for the active Unity project."""
    _list_custom_tools()
