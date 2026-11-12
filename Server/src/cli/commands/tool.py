"""Tool listing and inspection of actual server-advertised schemas."""

import click

from cli.commands.custom_tool_scaffold import scaffold_command

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


def _validate_query(_ctx, _param, value):
    if value is None:
        return None
    if not value.strip() or len(value) > 512:
        raise click.BadParameter("use 1..512 characters including a non-whitespace character")
    return value.strip()


@tool.command("search")
@click.argument("query", callback=_validate_query)
@click.option("--limit", type=click.IntRange(1, 20), default=5, show_default=True)
@click.option(
    "--catalog-query",
    callback=_validate_query,
    help="Task intent translated into English catalog terms. Preserve exact tool/API/package identifiers.",
)
def search(query: str, limit: int, catalog_query: str | None) -> None:
    """Search the offline local catalog by task purpose, without a Unity connection.

    Advertisement and project readiness remain unobserved in this offline scope.
    """
    from pathlib import Path

    import services.tools as tools_package
    from services.registry import get_registered_tools
    from services.registry.task_search import SearchRequest, search_catalog
    from utils.module_discovery import discover_modules

    list(discover_modules(Path(tools_package.__file__).parent, tools_package.__name__))
    result = search_catalog(
        SearchRequest(query=query, catalog_query=catalog_query, limit=limit), get_registered_tools()
    )
    config = get_config()
    if config.format == "text":
        from cli.utils.task_search_output import format_task_search

        click.echo(format_task_search(result, config))
    else:
        click.echo(format_output(result, config.format))


@tool.command("catalog")
@click.option("--group", help="List tools in one catalog group.")
@click.option("--tool", "tool_name", help="List actions of one exact tool.")
@click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--limit", type=click.IntRange(1, 20), default=20, show_default=True)
def catalog(group: str | None, tool_name: str | None, offset: int, limit: int) -> None:
    """Browse the complete offline catalog for agent semantic selection.

    Start with the group index, then compare tool purposes and actions. Use
    tool describe NAME for the live schema before execution.
    """
    import json
    from pathlib import Path

    import services.tools as tools_package
    from services.registry import get_registered_tools
    from services.registry.action_catalog import CatalogRequest, browse_catalog
    from utils.module_discovery import discover_modules

    try:
        request = CatalogRequest(group=group, tool=tool_name, offset=offset, limit=limit)
        list(discover_modules(Path(tools_package.__file__).parent, tools_package.__name__))
        result = browse_catalog(request, get_registered_tools())
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    # Catalog nesting and exact continuation offsets must survive text/table modes.
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


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


tool.add_command(scaffold_command)
custom_tool.add_command(scaffold_command)
