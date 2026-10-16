"""Unity API reflection CLI commands."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors


@click.group()
def reflect():
    """Inspect Unity C# APIs via reflection."""
    pass


@reflect.command("type")
@click.argument("class_name")
@handle_unity_errors
def get_type(class_name: str):
    """Get member summary for a Unity type.

    \b
    Examples:
        unity-mcp reflect type NavMeshAgent
        unity-mcp reflect type UnityEngine.Physics
    """
    config = get_config()
    result = run_command("unity_reflect", {"action": "get_type", "class_name": class_name}, config)
    click.echo(format_output(result, config.format))


@reflect.command("member")
@click.argument("class_name")
@click.argument("member_name")
@handle_unity_errors
def get_member(class_name: str, member_name: str):
    """Get detailed info for a specific member.

    \b
    Examples:
        unity-mcp reflect member Physics Raycast
        unity-mcp reflect member NavMeshAgent SetDestination
    """
    config = get_config()
    result = run_command(
        "unity_reflect",
        {
            "action": "get_member",
            "class_name": class_name,
            "member_name": member_name,
        },
        config,
    )
    click.echo(format_output(result, config.format))


@reflect.command("search")
@click.argument("query")
@click.option(
    "--scope",
    "-s",
    default="unity",
    type=click.Choice(["unity", "packages", "project", "all"]),
    help="Assembly scope to search.",
)
@handle_unity_errors
@click.option(
    "--page-size",
    type=click.IntRange(1, 100),
    default=None,
    help="Page size (default 25). Repeat it with --cursor.",
)
@click.option(
    "--cursor",
    default=None,
    help="Continuation token; repeat the same inputs and exact assembly spelling within five minutes.",
)
@click.option(
    "--assembly",
    default=None,
    help="Case-insensitive simple/full assembly filter; repeat exact spelling with --cursor.",
)
@click.option(
    "--type-kind",
    type=click.Choice(["class", "interface", "struct", "enum", "delegate"]),
    default=None,
)
def search(
    query: str,
    scope: str,
    page_size: int | None,
    cursor: str | None,
    assembly: str | None,
    type_kind: str | None,
):
    """Search for Unity types by name.

    \b
    Examples:
        unity-mcp reflect search NavMesh
        unity-mcp reflect search Camera --scope all
    """
    config = get_config()
    if not query.strip() or len(query) > 256:
        raise click.BadParameter("must contain 1..256 characters", param_hint="query")
    if assembly is not None and (not assembly.strip() or len(assembly) > 256):
        raise click.BadParameter("must contain 1..256 characters", param_hint="--assembly")
    if cursor is not None and (not cursor or len(cursor) > 1024 or not cursor.isascii()):
        raise click.BadParameter("must contain 1..1024 ASCII characters", param_hint="--cursor")
    params = {"action": "search", "query": query, "scope": scope}
    params.update(
        {
            key: value
            for key, value in {
                "page_size": page_size,
                "cursor": cursor,
                "assembly": assembly,
                "type_kind": type_kind,
            }.items()
            if value is not None
        }
    )
    result = run_command(
        "unity_reflect",
        params,
        config,
    )
    click.echo(format_output(result, config.format))
