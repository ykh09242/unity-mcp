"""Asset CLI commands."""

import sys
import json
import re
import click
from cli.utils.parsers import admit_json_inputs
from typing import Optional, Any

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import run_command, handle_unity_errors
from cli.utils.parsers import parse_json_dict_or_exit
from cli.utils.confirmation import confirm_destructive_action


@click.group()
def asset():
    """Asset operations - search, import, create, delete assets."""
    pass


@asset.group("labels")
def labels_group():
    """Read or change asset labels using observed revisions; batches are sequential."""


def _labels_command(
    action: str,
    path: str | None,
    targets_json: str | None,
    labels_json: str | None = None,
    revision: str | None = None,
    force: bool = False,
) -> None:
    """Validate the closed label wire before configuration, confirmation or transport."""
    from services.tools.manage_asset import asset_label_params

    try:
        # Inline JSON only: bounded options do not expand files or stdin implicitly.
        if targets_json is not None and len(targets_json.encode("utf-8")) > 131072:
            raise ValueError("targets JSON exceeds 128 KiB.")
        if labels_json is not None and len(labels_json.encode("utf-8")) > 131072:
            raise ValueError("labels JSON exceeds 128 KiB.")
        targets = json.loads(targets_json) if targets_json is not None else None
        requested = json.loads(labels_json) if labels_json is not None else None
        if targets_json is not None and type(targets) is not list:
            raise ValueError("--targets-json must supply an array, not null or another JSON type.")
        if labels_json is not None and type(requested) is not list:
            raise ValueError("--labels-json must supply an array, not null or another JSON type.")
        params = asset_label_params(action, path, requested, targets, revision)
    except (ValueError, TypeError) as exc:
        raise click.BadParameter(str(exc)[:512]) from exc
    if action != "get_labels":
        confirm_destructive_action(
            "Change labels on", "asset target", path or f"{len(targets)} targets", force
        )
    config = get_config()
    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))


@labels_group.command("get")
@click.argument("path", required=False)
@click.option("--targets-json", default=None, help='1-32 rows: [{"path":"Assets/a.txt"}].')
@handle_unity_errors
def labels_get(path: str | None, targets_json: str | None) -> None:
    """Read labels and revisions; package targets are read-only."""
    _labels_command("get_labels", path, targets_json)


def _label_mutation_options(function):
    """The three mutation verbs share exactly the same bounded input surface."""
    function = click.option(
        "--force", "-f", is_flag=True, help="Skip confirmation, not revision validation."
    )(function)
    function = click.option(
        "--revision", default=None, help="Observed single-target label revision."
    )(function)
    function = click.option(
        "--labels-json", required=True, help="Exact label array; [] clears with set."
    )(function)
    function = click.option(
        "--targets-json", default=None, help="1-32 rows with path and observed revision."
    )(function)
    return click.argument("path", required=False)(function)


@labels_group.command("set")
@_label_mutation_options
@handle_unity_errors
def labels_set(
    path: str | None, targets_json: str | None, labels_json: str, revision: str | None, force: bool
) -> None:
    """Replace the complete label set; an empty array clears labels."""
    _labels_command("set_labels", path, targets_json, labels_json, revision, force)


@labels_group.command("add")
@_label_mutation_options
@handle_unity_errors
def labels_add(
    path: str | None, targets_json: str | None, labels_json: str, revision: str | None, force: bool
) -> None:
    """Add exact labels while preserving unrelated labels."""
    _labels_command("add_labels", path, targets_json, labels_json, revision, force)


@labels_group.command("remove")
@_label_mutation_options
@handle_unity_errors
def labels_remove(
    path: str | None, targets_json: str | None, labels_json: str, revision: str | None, force: bool
) -> None:
    """Remove exact labels while preserving unrelated labels."""
    _labels_command("remove_labels", path, targets_json, labels_json, revision, force)


@asset.command("search")
@click.argument("pattern", default="*")
@click.option("--path", "-p", default="Assets", help="Folder path to search in.")
@click.option(
    "--type",
    "-t",
    "filter_type",
    default=None,
    help="Filter by asset type (e.g., Material, Prefab, MonoScript).",
)
@click.option("--limit", "-l", default=25, type=int, help="Maximum results per page.")
@click.option("--page", default=1, type=int, help="Page number (1-based).")
@click.option(
    "--metadata-only",
    is_flag=True,
    help="Search metadata without explicit object/preview loads; return a short-lived reusable snapshot.",
)
@click.option(
    "--search-snapshot",
    default=None,
    help="Continue metadata pages with the same query, scope, type and limit.",
)
@handle_unity_errors
def search(
    pattern: str,
    path: str,
    filter_type: Optional[str],
    limit: int,
    page: int,
    metadata_only: bool,
    search_snapshot: str | None,
):
    """Search for assets.

    \b
    Examples:
        unity-mcp asset search "*.prefab"
        unity-mcp asset search "Player*" --path "Assets/Characters"
        unity-mcp asset search "*" --type Material
        unity-mcp asset search "t:MonoScript" --path "Assets/Scripts"
    """
    if search_snapshot is not None and (
        not metadata_only
        or len(search_snapshot) != 75
        or re.fullmatch(r"mcp-as-v1_[0-9a-f]{32}_[0-9a-f]{32}", search_snapshot) is None
    ):
        raise click.BadParameter(
            "A valid token requires --metadata-only.", param_hint="--search-snapshot"
        )
    config = get_config()

    params: dict[str, Any] = {
        "action": "search_metadata_v1" if metadata_only else "search",
        "path": path,
        "searchPattern": pattern,
        "pageSize": limit,
        "pageNumber": page,
    }

    if filter_type:
        params["filterType"] = filter_type
    if search_snapshot is not None:
        params["searchSnapshot"] = search_snapshot

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))


@asset.command("info")
@click.argument("path")
@click.option("--preview", is_flag=True, help="Generate preview thumbnail (may be large).")
@handle_unity_errors
def info(path: str, preview: bool):
    """Get detailed information about an asset.

    \b
    Examples:
        unity-mcp asset info "Assets/Materials/Red.mat"
        unity-mcp asset info "Assets/Prefabs/Player.prefab" --preview
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "get_info",
        "path": path,
        "generatePreview": preview,
    }

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))


@asset.command("create")
@click.argument("path")
@click.argument("asset_type")
@click.option(
    "--properties",
    "-p",
    default=None,
    help="Initial properties as JSON. Accepts @file or -/@- stdin.",
)
@handle_unity_errors
def create(path: str, asset_type: str, properties: Optional[str]):
    """Create a new asset.

    \b
    Examples:
        unity-mcp asset create "Assets/Materials/Blue.mat" Material
        unity-mcp asset create "Assets/NewFolder" Folder
        unity-mcp asset create "Assets/Materials/Custom.mat" Material --properties '{"color": [0,0,1,1]}'
    """
    properties = admit_json_inputs(properties=properties)["properties"]

    config = get_config()

    params: dict[str, Any] = {
        "action": "create",
        "path": path,
        "assetType": asset_type,
    }

    if properties is not None:
        params["properties"] = parse_json_dict_or_exit(properties, "properties")

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Created {asset_type}: {path}")


@asset.command("delete")
@click.argument("path")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
@handle_unity_errors
def delete(path: str, force: bool):
    """Delete an asset.

    \b
    Examples:
        unity-mcp asset delete "Assets/OldMaterial.mat"
        unity-mcp asset delete "Assets/Unused" --force
    """
    config = get_config()

    confirm_destructive_action("Delete", "asset", path, force)

    result = run_command("manage_asset", {"action": "delete", "path": path}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Deleted: {path}")


@asset.command("duplicate")
@click.argument("source")
@click.argument("destination")
@handle_unity_errors
def duplicate(source: str, destination: str):
    """Duplicate an asset.

    \b
    Examples:
        unity-mcp asset duplicate "Assets/Materials/Red.mat" "Assets/Materials/RedCopy.mat"
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "duplicate",
        "path": source,
        "destination": destination,
    }

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Duplicated to: {destination}")


@asset.command("move")
@click.argument("source")
@click.argument("destination")
@handle_unity_errors
def move(source: str, destination: str):
    """Move an asset to a new location.

    \b
    Examples:
        unity-mcp asset move "Assets/Old/Material.mat" "Assets/New/Material.mat"
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "move",
        "path": source,
        "destination": destination,
    }

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Moved to: {destination}")


@asset.command("rename")
@click.argument("path")
@click.argument("new_name")
@handle_unity_errors
def rename(path: str, new_name: str):
    """Rename an asset.

    \b
    Examples:
        unity-mcp asset rename "Assets/Materials/Old.mat" "New.mat"
    """
    config = get_config()

    # Construct destination path
    import os

    dir_path = os.path.dirname(path)
    destination = os.path.join(dir_path, new_name).replace("\\", "/")

    params: dict[str, Any] = {
        "action": "rename",
        "path": path,
        "destination": destination,
    }

    result = run_command("manage_asset", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Renamed to: {new_name}")


@asset.command("import")
@click.argument("path")
@handle_unity_errors
def import_asset(path: str):
    """Import/reimport an asset.

    \b
    Examples:
        unity-mcp asset import "Assets/Textures/NewTexture.png"
    """
    config = get_config()

    result = run_command("manage_asset", {"action": "import", "path": path}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Imported: {path}")


@asset.command("mkdir")
@click.argument("path")
@handle_unity_errors
def mkdir(path: str):
    """Create a folder.

    \b
    Examples:
        unity-mcp asset mkdir "Assets/NewFolder"
        unity-mcp asset mkdir "Assets/Levels/Chapter1"
    """
    config = get_config()

    result = run_command("manage_asset", {"action": "create_folder", "path": path}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Created folder: {path}")
