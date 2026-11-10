"""Utilities for normalizing Unity transport responses."""

from __future__ import annotations

from typing import Any, Type

from models.models import MCPResponse
from models.response_limits import bound_response


def extract_unity_result(response: Any) -> Any:
    """Extract bounded native JSON for callers that own response adaptation."""
    response = bound_response(response)
    if isinstance(response, dict) and response.get("status") == "success" and "result" in response:
        return response["result"]
    return normalize_unity_response(response)


def normalize_unity_response(response: Any) -> Any:
    """Normalize Unity's {status,result} payloads into MCPResponse shape."""
    response = bound_response(response)
    if not isinstance(response, dict):
        return response

    status = response.get("status")
    result = response.get("result")

    # Already MCPResponse-shaped
    if "success" in response:
        return response
    if isinstance(result, dict) and (
        "success" in result or result.get("_mcp_status") in ("pending", "complete", "error")
    ):
        return result

    if status is None:
        return response

    # Only legacy response-shaped dictionaries use implicit metadata. Business
    # objects keep every field together, including a field named "data".
    payload = (
        result
        if isinstance(result, dict)
        and result.keys() <= MCPResponse.model_fields.keys() | {"status"}
        and any(key in result for key in ("message", "error", "data"))
        else {}
    )
    success = status == "success"
    message = payload.get("message") or response.get("message")
    error = payload.get("error") or response.get("error")

    data = payload.get("data") if payload else result

    normalized: dict[str, Any] = {
        "success": success,
        "message": message,
        "error": error if not success else None,
        "data": data,
    }

    code = payload.get("code", response.get("code"))
    if code is not None:
        normalized["code"] = code

    if not success and not normalized["error"]:
        normalized["error"] = message or "Unity command failed"

    return normalized


def parse_resource_response(response: Any, typed_cls: Type[MCPResponse]) -> MCPResponse:
    """Parse a Unity response into a typed response class.

    Returns a base ``MCPResponse`` for error responses so that typed subclasses
    with strict ``data`` fields (e.g. ``list[str]``) don't raise Pydantic
    validation errors when ``data`` is ``None``.
    """
    response = bound_response(response)
    if not isinstance(response, dict):
        return response

    # Detect errors from both normalized (success=False) and raw (status="error") shapes.
    if response.get("success") is False or response.get("status") == "error":
        return MCPResponse(
            success=False,
            code=response.get("code"),
            error=response.get("error"),
            message=response.get("message"),
            data=response.get("data"),
            hint=response.get("hint"),
        )

    return typed_cls(**response)
