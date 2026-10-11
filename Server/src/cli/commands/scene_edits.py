"""Explicit scene-edit previews and grouped Undo applications."""

import json
from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from services.tools.manage_scene_edits import SceneEdit
from pydantic import TypeAdapter, ValidationError


def _read_edits(path: str, require_expected: bool) -> list[dict]:
    source = Path(path)
    try:
        if source.stat().st_size > 65536:
            raise click.ClickException("Edit file exceeds 65536 bytes.")
        payload = json.loads(source.read_text(encoding="utf-8"))
        edits = TypeAdapter(list[SceneEdit]).validate_python(payload)
        if not 1 <= len(edits) <= 64:
            raise click.ClickException("Edit file must contain 1-64 entries.")
        if require_expected and any("expected_old" not in edit.model_fields_set for edit in edits):
            raise click.ClickException("Direct apply requires expected_old for every edit.")
        result = [edit.model_dump(exclude_unset=True) for edit in edits]
        json.dumps(result, allow_nan=False)
        return result
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        raise click.ClickException(f"Invalid edit file: {error}") from error


def _dispatch(params: dict) -> None:
    config = get_config()
    result = run_command("manage_scene_edits", params, config)
    click.echo(format_output(result, config.format))
    if isinstance(result, dict) and result.get("success") is False:
        raise click.exceptions.Exit(1)


@click.group("scene-edit")
def scene_edit() -> None:
    """Preview and apply allowlisted scene edits with one named Undo group."""


@scene_edit.command("preview")
@click.argument("edits_file", type=click.Path(exists=True, dir_okay=False))
@handle_unity_errors
def preview(edits_file: str) -> None:
    """Preview the JSON array in EDITS_FILE without mutating scene values."""
    _dispatch({"action": "preview", "edits": _read_edits(edits_file, False)})


@scene_edit.command("apply")
@click.option(
    "--preview-id", type=click.STRING, help="Use a one-attempt token returned by preview."
)
@click.option(
    "--edits-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Direct apply JSON; each entry requires expected_old.",
)
@click.option("--undo-name", default="MCP Scene Edits", help="Undo group label (1-80 characters).")
@handle_unity_errors
def apply(preview_id: str | None, edits_file: str | None, undo_name: str) -> None:
    """Apply exactly one of --preview-id or --edits-file."""
    if (preview_id is None) == (edits_file is None):
        raise click.UsageError("Specify exactly one of --preview-id or --edits-file.")
    if not undo_name.strip() or len(undo_name) > 80:
        raise click.UsageError("undo_name must contain 1-80 characters.")
    params = {"action": "apply", "undo_name": undo_name}
    if preview_id is not None:
        if not preview_id.strip() or len(preview_id) > 64:
            raise click.UsageError("preview_id must contain 1-64 characters.")
        params["preview_id"] = preview_id
    else:
        params["edits"] = _read_edits(edits_file, True)
    _dispatch(params)
