"""Constrained adapters around the existing validated tool implementations."""

from contextvars import ContextVar
import inspect
from types import CodeType
from typing import Annotated, Callable, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from models.capability_results import BUNDLE_RESULT, validate_bundle_result
from services.registry.action_contracts import action_input_error
from services.registry.capability_catalog import Capability, CAPABILITIES
from services.registry.tool_registry import mcp_for_unity_tool

_command_route: ContextVar[tuple[str, str] | None] = ContextVar(
    "unity_capability_command", default=None
)
_adapter_code: CodeType | None = None


def get_capability_implementation(function: Callable, name: str) -> Callable | None:
    """Read the real adapter closure, never mutable catalog/name-only metadata."""
    if _adapter_code is None or getattr(function, "__code__", None) is not _adapter_code:
        return None
    captured = inspect.getclosurevars(function).nonlocals
    capability = captured.get("capability")
    if not any(item is capability and item.name == name for item in CAPABILITIES):
        return None
    implementation = captured.get("implementation")
    return implementation if callable(implementation) else None


def capability_command(source: str) -> str:
    """Resolve only the current adapter's source; unrelated commands stay unchanged."""
    route = _command_route.get()
    return route[1] if route is not None and route[0] == source else source


def register_capability(capability: Capability, implementation: Callable) -> Callable:
    """Keep original defaults/types while admitting only this feature's inputs."""
    global _adapter_code
    signature = inspect.signature(implementation)
    admitted = {"ctx", "action", *capability.parameters}
    unknown = admitted - signature.parameters.keys()
    if unknown:
        raise ValueError(f"{capability.name}: unknown parameters {sorted(unknown)}")
    parameters = []
    for name, parameter in signature.parameters.items():
        if name not in admitted:
            continue
        if name == "ctx":
            # SDK registration can attach an injection default to the source.
            # This wrapper receives its own Context injection before invocation.
            parameter = parameter.replace(default=inspect.Parameter.empty)
        if name == "action":
            parameter = parameter.replace(
                annotation=Annotated[
                    Literal[capability.actions], Field(description="Feature action to perform.")
                ],
                default=inspect.Parameter.empty,
            )
        parameters.append(parameter)
    narrowed = signature.replace(parameters=parameters)

    async def invoke(*args, **kwargs):
        bound = narrowed.bind(*args, **kwargs)
        action = bound.arguments["action"]
        if action not in capability.actions:
            raise ValueError(f"Action is outside {capability.name}")
        error = action_input_error(capability.name, bound.arguments)
        if error is not None:
            return {"success": False, "code": "invalid_action_parameters", "message": error}
        token = _command_route.set((capability.source, capability.name))
        try:
            result = await implementation(**bound.arguments)
            if capability.name == "asset_bundles":
                validate_bundle_result(result)
            return result
        finally:
            _command_route.reset(token)

    _adapter_code = invoke.__code__
    invoke.__name__ = capability.name
    invoke.__qualname__ = capability.name
    invoke.__doc__ = capability.description
    invoke.__annotations__ = {parameter.name: parameter.annotation for parameter in parameters}
    invoke.__annotations__["return"] = signature.return_annotation
    invoke.__signature__ = narrowed
    return mcp_for_unity_tool(
        name=capability.name,
        description=capability.description,
        group=capability.group,
        **(
            {"output_schema": {"type": "object", **BUNDLE_RESULT.json_schema()}}
            if capability.name == "asset_bundles"
            else {}
        ),
        annotations=ToolAnnotations(
            title=capability.title,
            readOnlyHint=capability.effects.read_only,
            destructiveHint=capability.effects.destructive,
            idempotentHint=capability.effects.idempotent,
            openWorldHint=capability.effects.open_world,
        ),
    )(invoke)
