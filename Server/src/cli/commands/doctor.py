"""Read-only SDK diagnostics for an explicitly selected server and Unity target."""

import re
from typing import Literal

import anyio
import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from models.doctor import EXACT_TARGET_PATTERN, DoctorCLIReceipt


@click.command()
@click.option(
    "--protocol", type=click.Choice(["auto", "legacy"]), default="auto", show_default=True
)
@click.option(
    "--config",
    "show_config",
    is_flag=True,
    help="Read local CLI effective configuration offline; does not observe a running server.",
)
def doctor(protocol: Literal["auto", "legacy"], show_config: bool = False):
    """Verify this diagnostic SDK connection; does not attest another desktop client's session."""
    config = get_config()
    if show_config:
        from cli.utils.config_diagnostics import local_configuration

        click.echo(
            format_output(local_configuration(config).model_dump(mode="json"), config.format)
        )
        return
    if not re.fullmatch(EXACT_TARGET_PATTERN, config.unity_instance or ""):
        receipt = DoctorCLIReceipt(
            requested_protocol=protocol,
            overall="failed",
            exit_code=2,
            reason="invalid_target",
            next_action="select_exact_instance",
        )
    else:
        try:
            from cli.utils.doctor_connection import diagnose
        except ModuleNotFoundError as error:
            reason = (
                "dependency_missing"
                if error.name in ("httpx2", "fastmcp", "mcp", "mcp_types")
                else "internal_dependency_error"
            )
            receipt = DoctorCLIReceipt(
                requested_target=config.unity_instance,
                requested_protocol=protocol,
                overall="failed",
                exit_code=4,
                reason=reason,
                next_action="install_declared_dependencies",
            )
        else:
            receipt = anyio.run(diagnose, config, protocol)
    click.echo(format_output(receipt.model_dump(mode="json"), config.format))
    raise click.exceptions.Exit(receipt.exit_code)
