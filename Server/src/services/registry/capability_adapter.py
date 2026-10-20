"""Constrained adapters around the existing validated tool implementations."""

from contextvars import ContextVar
import inspect
from typing import Annotated, Callable, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from services.registry.capability_catalog import Capability
from services.registry.tool_registry import mcp_for_unity_tool

_command_route: ContextVar[tuple[str, str] | None] = ContextVar(
    "unity_capability_command", default=None
)


def capability_command(source: str) -> str:
    """Resolve only the current adapter's source; unrelated commands stay unchanged."""
    route = _command_route.get()
    return route[1] if route is not None and route[0] == source else source


def register_capability(capability: Capability, implementation: Callable) -> Callable:
    """Keep original defaults/types while admitting only this feature's inputs."""
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
        token = _command_route.set((capability.source, capability.name))
        try:
            return await implementation(**bound.arguments)
        finally:
            _command_route.reset(token)

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
        annotations=ToolAnnotations(title=capability.title, destructiveHint=True),
    )(invoke)
