"""Clone invariant tool descriptors while each control-plane test builds its own server."""

from collections.abc import Callable
from copy import deepcopy
import inspect
from types import FunctionType
from typing import get_args, get_origin

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import FunctionTool
from pydantic import Field
from pydantic.fields import FieldInfo

from services.tools import register_all_tools


# Field's general metadata uses identity equality, unlike its frozen constraints.
# Obtain the type through the public constructor without importing SDK internals.
_GENERAL_METADATA_TYPE = type(Field(pattern="").metadata[0])


def _annotation_state(value) -> tuple:
    """Snapshot mutable Field metadata without comparing copied typing aliases by identity."""
    if isinstance(value, FieldInfo):
        data = value.asdict()
        data["metadata"] = [
            (type(item), vars(item).copy()) if type(item) is _GENERAL_METADATA_TYPE else item
            for item in data["metadata"]
        ]
        return (data,)
    origin = get_origin(value)
    if origin is not None:
        return origin, tuple(_annotation_state(argument) for argument in get_args(value))
    return (value,)


def _wrapper_state(fn: FunctionType, depth: int = 0) -> tuple | None:
    """Recognize only the static function/string closures of production decorators."""
    if not isinstance(fn, FunctionType) or depth >= 4:
        return None
    closure = []
    for cell in fn.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, FunctionType):
            nested = _wrapper_state(value, depth + 1)
            if nested is None:
                return None
            closure.append(nested)
        elif isinstance(value, str):
            closure.append(value)
        else:
            # A new decorator with other captured state must use the actual SDK parser.
            return None
    signature = inspect.signature(fn, follow_wrapped=False)
    return (
        fn.__code__,
        id(fn.__globals__),
        fn.__defaults__,
        fn.__kwdefaults__,
        tuple((name, _annotation_state(value)) for name, value in fn.__annotations__.items()),
        tuple(
            (name, parameter.kind, parameter.default, _annotation_state(parameter.annotation))
            for name, parameter in signature.parameters.items()
        ),
        _annotation_state(signature.return_annotation),
        fn.__doc__,
        tuple(closure),
    )


@pytest.fixture(scope="session")
def prepared_tool_factory() -> Callable[..., FunctionTool]:
    """Keep private templates out of apps and fall back for changed declarations/options."""
    original = Tool.from_function
    captured = []

    def capture(fn: FunctionType, **kwargs) -> FunctionTool:
        state = deepcopy(_wrapper_state(fn))
        tool = original(fn, **kwargs)
        captured.append(
            (
                inspect.unwrap(fn),
                deepcopy(kwargs),
                state,
                tool.model_copy(deep=True),
            )
        )
        return tool

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", capture)
        register_all_tools(
            FastMCP("control-plane-registration-templates"), project_scoped_tools=False
        )
    templates = tuple(captured)

    def clone(fn: FunctionType, **kwargs) -> FunctionTool:
        # Production logging/telemetry and SDK DI closures capture only static functions,
        # names and signatures. Request routing/state is resolved from the current context.
        declaration = inspect.unwrap(fn)
        state = _wrapper_state(fn)
        for source, options, prior_state, template in templates:
            if (
                declaration is source
                and kwargs == options
                and state is not None
                and state == prior_state
            ):
                return template.model_copy(deep=True)
        return original(fn, **kwargs)

    return clone
