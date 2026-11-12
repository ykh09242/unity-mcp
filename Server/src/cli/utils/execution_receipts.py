"""Explicit CLI receipt protection with no automatic mutation replay."""

from functools import partial

import anyio

from cli.utils.config import CLIConfig
from cli.utils.connection import UnityCommandError, UnityConnectionError, send_command
from cli.utils.input_errors import CLIInputError
from cli.utils.project_context import exact_hash
from services.execution_receipt_identity import HEX_ID, JsonValue, ReceiptInvalid, ReceiptOptions


def execution_options(
    config: CLIConfig, store_id: str, key: str, retain_result: bool
) -> ReceiptOptions:
    """Bind protection to the user's complete explicit target and request key."""
    project = exact_hash(config.unity_instance)
    if project is None:
        raise CLIInputError(
            "invalid_execution_options",
            "Receipt execution requires --instance with a full project hash.",
        )
    options = ReceiptOptions(store_id, project, key, retain_result)
    try:
        options.validate()
    except ReceiptInvalid:
        raise CLIInputError(
            "invalid_execution_options",
            "Use a 32-character lowercase hexadecimal store ID and a 16..128 character ASCII request key (letters, digits, _ . : -).",
        ) from None
    return options


def lookup_parameters(
    store_id: str,
    project_hash: str,
    receipt_id: str | None,
    key: str | None,
    include_result: bool,
) -> dict[str, JsonValue]:
    """Validate historical lookup without requiring a connected Editor."""
    try:
        ReceiptOptions(store_id, project_hash, key).validate()
    except ReceiptInvalid:
        raise CLIInputError(
            "invalid_receipt_lookup", "Invalid receipt store, project hash or request key."
        ) from None
    if (receipt_id is None) == (key is None) or (
        receipt_id is not None and HEX_ID.fullmatch(receipt_id) is None
    ):
        raise CLIInputError(
            "invalid_receipt_lookup", "Supply exactly one valid --receipt-id or --idempotency-key."
        )
    params: dict[str, JsonValue] = {
        "action": "get",
        "store_id": store_id,
        "project_hash": project_hash,
        "include_result": include_result,
    }
    if receipt_id is not None:
        params["receipt_id"] = receipt_id
    else:
        params["idempotency_key"] = key
    return params


def _unavailable() -> UnityCommandError:
    return UnityCommandError(
        {
            "success": False,
            "code": "execution_receipts_unavailable",
            "error": "The selected server did not confirm this execution receipt store. No command was dispatched.",
            "data": {"dispatch": "not_dispatched"},
        }
    )


def _unknown(options: ReceiptOptions) -> UnityCommandError:
    return UnityCommandError(
        {
            "success": False,
            "code": "execution_receipt_outcome_unknown",
            "error": "The protected command outcome is unknown. Inspect its receipt before retrying; no command was resent.",
            "data": {
                "lookup": {
                    "store_id": options.store_id,
                    "project_hash": options.project_hash,
                    "idempotency_key": options.idempotency_key,
                }
            },
        }
    )


async def _execute(
    command: str, params: dict[str, JsonValue], config: CLIConfig, options: ReceiptOptions
) -> dict[str, JsonValue]:
    dispatched = False
    try:
        # Discovery and the single protected invocation share one total deadline.
        with anyio.fail_after(config.timeout):
            description = await send_command(
                "get_execution_receipt", {"action": "describe"}, config
            )
            match description:
                case {"structuredContent": dict() as metadata} if (
                    description.get("isError") is not True
                    and metadata.get("version") == 1
                    and metadata.get("available") is True
                    and metadata.get("store_id") == options.store_id
                ):
                    pass
                case _:
                    raise _unavailable()
            dispatched = True
            result = await send_command(command, params, config, execution=options)
    except (UnityConnectionError, TimeoutError):
        raise (_unknown(options) if dispatched else _unavailable()) from None
    match result:
        case {"execution_receipt": dict() as metadata} if (
            metadata.get("version") == 1
            and metadata.get("store_id") == options.store_id
            and metadata.get("project_hash") == options.project_hash
            and type(metadata.get("receipt_id")) is str
            and HEX_ID.fullmatch(metadata["receipt_id"]) is not None
        ):
            if result.get("success") is False:
                raise UnityCommandError(result)
            return result
        case _:
            raise _unknown(options)


def run_receipted_command(
    command: str, params: dict[str, JsonValue], config: CLIConfig, options: ReceiptOptions
) -> dict[str, JsonValue]:
    """Check support, then send once; protection never silently falls back."""
    if not command.strip() or len(command) > 256 or command.lower() == "get_execution_receipt":
        raise CLIInputError(
            "invalid_execution_options", "Use a native command, not a receipt control."
        )
    if (
        command.lower() == "run_tests"
        and not params.get("clear_stuck")
        and "source_token" not in params
    ):
        raise CLIInputError(
            "invalid_execution_options",
            "Protected run_tests requires an explicit source_token; prepare the source before using its request key.",
        )
    return anyio.run(partial(_execute, command, params, config, options))
