"""Offline custom-tool scaffold CLI; no connection or authentication decorator."""

from dataclasses import asdict
from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.custom_tool_scaffold import ScaffoldError, ScaffoldOptions, scaffold
from cli.utils.output import format_output


@click.command("scaffold")
@click.argument("class_name")
@click.option(
    "--namespace", required=True, help="Explicit C# namespace; pass an empty value for global."
)
@click.option(
    "--tool-name", required=True, help="Explicit unchanged wire name; uniqueness is unverified."
)
@click.option(
    "--project-root", type=click.Path(path_type=Path), default=Path("."), show_default=True
)
@click.option("--output", help="New relative .cs path under an Assets/Editor folder.")
@click.option(
    "--template", type=click.Choice(["sync", "async-polling"]), default="sync", show_default=True
)
@click.option(
    "--dry-run", is_flag=True, help="Validate/render without creating files or reserving a path."
)
def scaffold_command(
    class_name: str,
    namespace: str,
    tool_name: str,
    project_root: Path,
    output: str | None,
    template: str,
    dry_run: bool,
) -> None:
    """Create a new CLASS_NAME custom tool without contacting an Editor.

    Existing source or observed metadata refuses; compilation/availability remain unverified.
    Predefined Editor assemblies only; custom asmdef/asmref boundaries are unsupported.
    """
    try:
        receipt = scaffold(
            ScaffoldOptions(
                class_name, namespace, tool_name, project_root, output, template, dry_run
            )
        )
    except ScaffoldError as error:
        payload = {
            "reason": error.reason,
            "created": False,
            "cleanup_complete": not error.residual_paths,
            "residual_paths": error.residual_paths,
            "exit_code": error.exit_code,
        }
        code = error.exit_code
    else:
        payload = asdict(receipt)
        code = receipt.exit_code
    click.echo(format_output(payload, get_config().format))
    raise click.exceptions.Exit(code)
