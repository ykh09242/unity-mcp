"""CPU-only command serialization and bounded result decoding.

The hub owns admission, capacity and final publication. These functions only
touch their captured payloads and run through its bounded response workers.
"""

import json
from typing import Any

from models.response_limits import bounded_json_text
from transport.json_decoder import decode_json
from transport.large_result_assembler import CompletedLargeResult, LargeResultProtocolError
from transport.models import CommandResultMessage, ExecuteCommandMessage


def command_envelope(
    command_id: str, name: str, params: dict[str, Any], timeout: float
) -> dict[str, Any]:
    # Omit the optional envelope field, preserving nested null parameters.
    return ExecuteCommandMessage(
        id=command_id, name=name, params=params, timeout=timeout
    ).model_dump(exclude={"progress"})


def encode_command(payload: dict[str, Any]) -> str:
    # Match Starlette's send_json wire representation without doing its encoding
    # after the final socket-write admission fence.
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decode_large_result(
    completed: CompletedLargeResult,
    max_retained_bytes: int,
    *,
    max_bytes: int,
    max_depth: int,
    max_nodes: int,
    max_retained: int,
) -> CommandResultMessage:
    text = bounded_json_text(
        completed.payload,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=min(max_nodes, max_retained_bytes // 512),
    )
    if text is None:
        reason = "result_capacity" if max_retained_bytes < max_retained else "result_payload_limit"
        raise LargeResultProtocolError(reason, completed.command_id)
    message = decode_json(completed.payload, fallback_text=text)
    if (
        not isinstance(message, dict)
        or message.get("type") != "command_result"
        or message.get("id") != completed.command_id
    ):
        raise LargeResultProtocolError("invalid_result_envelope", completed.command_id)
    return CommandResultMessage(**message)
