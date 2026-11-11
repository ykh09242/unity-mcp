"""Fixed local-server diagnostics that are safe to preserve at the CLI boundary."""

from typing import Literal, TypedDict


class NoUnityInstances(TypedDict):
    success: Literal[False]
    code: Literal["no_unity_instances"]
    dispatch: Literal["not_dispatched"]
    error: str


def no_unity_instances_response() -> NoUnityInstances:
    """Describe the known no-session refusal without including request or transport data."""
    return {
        "success": False,
        "code": "no_unity_instances",
        "dispatch": "not_dispatched",
        "error": "No Unity instances connected. Make sure Unity is running with MCP plugin.",
    }
