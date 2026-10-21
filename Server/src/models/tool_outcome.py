"""Classify explicit response envelopes without inspecting text or business data."""

from pydantic import JsonValue


def is_execution_failure(
    structured: dict[str, JsonValue] | None, meta: dict[str, JsonValue] | None
) -> bool:
    """Recognize only a boolean failure or the SDK's declared result envelope."""
    if type(structured) is not dict:
        return False
    if structured.get("success") is False:
        return True
    if type(meta) is not dict or len(structured) != 1 or "result" not in structured:
        return False
    sdk = meta.get("fastmcp")
    if type(sdk) is not dict or sdk.get("wrap_result") is not True:
        return False
    envelope = structured["result"]
    return type(envelope) is dict and envelope.get("success") is False
