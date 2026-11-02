"""Project Auditor CLI: explicit analysis starts and bounded observation."""

from typing import Any
from uuid import uuid4

import click

from cli.utils.config import get_config
from cli.utils.connection import (
    UnityCommandError,
    UnityConnectionError,
    handle_unity_errors,
    run_command,
)
from cli.utils.output import format_output

AUDIT_ID = click.STRING


@click.group("project-auditor")
def project_auditor() -> None:
    """Optional Project Auditor 1.0.1 analysis; requires the installed package and rules."""


def _send(params: dict[str, Any]) -> None:
    """Send exactly one command and retain its structured outcome."""
    config = get_config()
    audit_id = params.get("audit_id") if params.get("action") == "start" else None
    try:
        result = run_command("manage_project_auditor", params, config)
    except UnityCommandError as error:
        if audit_id is None:
            raise
        raise UnityCommandError(_with_identity(error.response, audit_id)) from error
    except (UnityConnectionError, TimeoutError, ConnectionError) as error:
        if audit_id is None:
            raise
        raise UnityCommandError(
            _with_identity(
                {
                    "success": False,
                    "code": "project_auditor_transport_unknown",
                    "error": "The native audit outcome is unknown after transport interruption.",
                    "data": {"state": "unknown"},
                },
                audit_id,
            )
        ) from error
    if audit_id is not None and not result.get("success"):
        result = _with_identity(result, audit_id)
    click.echo(format_output(result, config.format))


def _with_identity(response: dict[str, Any], audit_id: str) -> dict[str, Any]:
    """Preserve the original command evidence and the identity required to recover it."""
    return {
        **response,
        "audit_id": audit_id,
        "observation_hint": "Query status with this audit_id before requesting another audit.",
    }


@project_auditor.command("availability")
@handle_unity_errors
def availability() -> None:
    """Check installed package/API/rules without starting analysis."""
    _send({"action": "availability"})


@project_auditor.command("start")
@click.option(
    "--audit-id", type=AUDIT_ID, help="Reuse an existing audit identity after uncertainty."
)
@handle_unity_errors
def start(audit_id: str | None) -> None:
    """Start one audit of the current build target; may compile Player code."""
    audit_id = audit_id or uuid4().hex
    _validate_id(audit_id)
    _send({"action": "start", "audit_id": audit_id})


def _validate_id(audit_id: str) -> None:
    """Reject malformed identities before native dispatch."""
    if len(audit_id) != 32 or any(character not in "0123456789abcdef" for character in audit_id):
        raise click.BadParameter(
            "Expected 32 lower-case hexadecimal characters.", param_hint="audit_id"
        )


@project_auditor.command("status")
@click.argument("audit_id", type=AUDIT_ID)
@handle_unity_errors
def status(audit_id: str) -> None:
    """Observe one audit without starting or cancelling work."""
    _validate_id(audit_id)
    _send({"action": "status", "audit_id": audit_id})


@project_auditor.command("issues")
@click.argument("audit_id", type=AUDIT_ID)
@click.option("--page-size", type=click.IntRange(1, 100), default=50, show_default=True)
@click.option("--cursor", type=click.STRING, help="Opaque cursor returned by the previous page.")
@handle_unity_errors
def issues(audit_id: str, page_size: int, cursor: str | None) -> None:
    """Read one bounded page of captured diagnostics after native completion."""
    _validate_id(audit_id)
    params: dict[str, Any] = {"action": "issues", "audit_id": audit_id, "page_size": page_size}
    if cursor is not None:
        if not cursor or len(cursor) > 128:
            raise click.BadParameter("Expected a nonempty cursor of at most 128 characters.")
        params["cursor"] = cursor
    _send(params)
