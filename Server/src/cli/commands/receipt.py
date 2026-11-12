"""Optional execution receipt commands for local HTTP native operations."""

import click

from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.execution_receipts import execution_options, lookup_parameters, run_receipted_command
from cli.utils.input_errors import CLIInputError
from cli.utils.output import format_output
from cli.utils.parsers import admit_raw_json_args, load_finite_json
from services.execution_receipt_identity import JsonValue


@click.group("receipt")
def receipt() -> None:
    """Inspect execution records or explicitly protect one native command."""


def _render_control(result: dict[str, JsonValue]) -> None:
    match result:
        case {"isError": True} | {"structuredContent": dict()}:
            pass
        case _:
            raise CLIInputError(
                "invalid_receipt_response",
                "The server did not return an execution receipt response.",
            )
    click.echo(format_output(result, get_config().format, detailed_lists=True))
    if result.get("isError") is True:
        raise click.exceptions.Exit(1)


@receipt.command("describe")
@handle_unity_errors
def describe() -> None:
    """Inspect authenticated store availability without a connected Editor."""
    _render_control(run_command("get_execution_receipt", {"action": "describe"}, get_config()))


@receipt.command("get")
@click.option("--store-id", required=True, help="Store ID returned by receipt describe.")
@click.option("--project-hash", required=True, help="Original full 16-character project hash.")
@click.option("--receipt-id", default=None, help="The original 32-character receipt ID.")
@click.option("--idempotency-key", default=None, help="The original case-sensitive request key.")
@click.option("--include-result", is_flag=True, help="Request retained result data when available.")
@handle_unity_errors
def get_receipt(
    store_id: str,
    project_hash: str,
    receipt_id: str | None,
    idempotency_key: str | None,
    include_result: bool,
) -> None:
    """Read observed status; unknown never means a command can be replayed."""
    params = lookup_parameters(store_id, project_hash, receipt_id, idempotency_key, include_result)
    _render_control(run_command("get_execution_receipt", params, get_config()))


@receipt.command("execute")
@click.option("--store-id", required=True, help="Store ID returned by receipt describe.")
@click.option(
    "--idempotency-key", required=True, help="Unique stable 16..128-character request key."
)
@click.option("--retain-result", is_flag=True, help="Explicitly retain bounded public result data.")
@click.argument("command")
@click.argument("params", nargs=-1)
@handle_unity_errors
def execute(
    store_id: str, idempotency_key: str, retain_result: bool, command: str, params: tuple[str, ...]
) -> None:
    """Protect one native COMMAND, using raw JSON, @file, or -/@- stdin.

    Requires a full --instance hash and an explicitly enabled server store.
    It checks store availability first, never resends, and never falls back.
    """
    config = get_config()
    options = execution_options(config, store_id, idempotency_key, retain_result)
    try:
        parsed = load_finite_json(admit_raw_json_args(params))
    except (ValueError, RecursionError):
        raise CLIInputError(
            "invalid_json_params", "Receipt command params must be finite JSON."
        ) from None
    match parsed:
        case dict():
            result = run_receipted_command(command, parsed, config, options)
        case _:
            raise CLIInputError(
                "invalid_json_params", "Receipt command params must be a JSON object."
            )
    click.echo(format_output(result, config.format, show_success_message=True))
    if config.format == "text":
        click.echo(format_output({"execution_receipt": result["execution_receipt"]}))
