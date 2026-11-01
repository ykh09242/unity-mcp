"""Clone invariant tool descriptors while each control-plane test builds its own server."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import fields, is_dataclass
import inspect
from types import FunctionType
from typing import get_args, get_origin

import pytest
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic.fields import FieldInfo

from services.tools import register_all_tools
from services.registry.capability_catalog import Capability, CapabilityEffects


# Field's general metadata uses identity equality, unlike its frozen constraints.
# Obtain the type through the public constructor without importing SDK internals.
_GENERAL_METADATA_TYPE = type(Field(pattern="").metadata[0])
_CURRENT_CONTEXT_TYPE = type(CurrentContext())
_UNDEFINED = Field().default


def _typed_state(value) -> tuple | None:
    """Preserve JSON-visible scalar distinctions within supported metadata trees."""
    if type(value) is float:
        return float, value.hex()
    if value is None or value is Ellipsis or type(value) in (str, bytes, bool, int):
        return type(value), value
    if isinstance(value, type) or value is _UNDEFINED:
        return type(value), value
    if type(value) is ToolAnnotations:
        state = _typed_state(value.model_dump(mode="python"))
        return None if state is None else (ToolAnnotations, state)
    if type(value) in (set, frozenset) and all(type(item) is str for item in value):
        return type(value), tuple(sorted(value))
    if type(value) is dict:
        values = tuple((_typed_state(key), _typed_state(item)) for key, item in value.items())
        return None if any(None in pair for pair in values) else (dict, values)
    if type(value) in (tuple, list):
        values = tuple(_typed_state(item) for item in value)
    elif is_dataclass(value):
        values = tuple(
            (field.name, _typed_state(getattr(value, field.name))) for field in fields(value)
        )
        return None if any(state is None for _, state in values) else (type(value), values)
    else:
        return None
    return None if None in values else (type(value), values)


def _annotation_state(value, memo=None) -> tuple | None:
    """Snapshot mutable Field metadata without comparing copied typing aliases by identity."""
    if memo is None:
        memo = {}
    key = id(value)
    if key in memo:
        return memo[key][1]
    if isinstance(value, FieldInfo):
        data = value.asdict()
        data["metadata"] = [
            (type(item), vars(item).copy()) if type(item) is _GENERAL_METADATA_TYPE else item
            for item in data["metadata"]
        ]
        state = _typed_state(data)
    elif (origin := get_origin(value)) is not None:
        arguments = tuple(_annotation_state(argument, memo) for argument in get_args(value))
        state = None if None in arguments else (origin, arguments)
    else:
        state = _typed_state(value)
    memo[key] = value, state
    return state


def _static_state(value) -> tuple | None:
    """Snapshot explicit immutable closure shapes, never arbitrary application objects."""
    if value is None or type(value) in (str, bytes, bool, int, float):
        return _typed_state(value)
    if type(value) is tuple:
        values = tuple(_static_state(item) for item in value)
        return None if None in values else (tuple, values)
    elif type(value) in (Capability, CapabilityEffects):
        values = tuple(
            (field.name, _static_state(getattr(value, field.name))) for field in fields(value)
        )
        return None if any(state is None for _, state in values) else (type(value), values)
    else:
        return None


def _default_state(value, memo=None) -> tuple | None:
    """Snapshot only known declaration defaults; new application objects must reparse."""
    if value is inspect.Parameter.empty:
        return (value,)
    if isinstance(value, FieldInfo):
        return _annotation_state(value, memo)
    # The public marker is a stateless factory; attributes would be a new unsupported shape.
    if type(value) is _CURRENT_CONTEXT_TYPE and not vars(value):
        return (_CURRENT_CONTEXT_TYPE,)
    return _static_state(value)


def _signature_state(signature: inspect.Signature, memo) -> tuple | None:
    parameters = []
    for name, parameter in signature.parameters.items():
        default = _default_state(parameter.default, memo)
        if default is None:
            return None
        annotation = _annotation_state(parameter.annotation, memo)
        if annotation is None:
            return None
        parameters.append((name, parameter.kind, default, annotation))
    result = _annotation_state(signature.return_annotation, memo)
    return None if result is None else (tuple(parameters), result)


def _wrapper_state(fn: FunctionType, depth: int = 0, memo=None) -> tuple | None:
    """Recognize static production decorators, including their admission signatures."""
    if not isinstance(fn, FunctionType) or depth >= 6:
        return None
    # Decorators repeat annotations; reuse snapshots only within this synchronous
    # inspection, never across registrations that may mutate declarations.
    if memo is None:
        memo = {}
    closure = []
    for cell in fn.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, FunctionType):
            nested = _wrapper_state(value, depth + 1, memo)
            if nested is None:
                return None
            closure.append(nested)
        elif isinstance(value, inspect.Signature):
            signature_state = _signature_state(value, memo)
            if signature_state is None:
                return None
            closure.append(signature_state)
        else:
            static = _static_state(value)
            if static is None:
                # A new decorator with other captured state must use the actual SDK parser.
                return None
            closure.append(static)
    signature = inspect.signature(fn, follow_wrapped=False)
    signature_state = _signature_state(signature, memo)
    defaults = tuple(_default_state(value, memo) for value in fn.__defaults__ or ())
    kwdefaults = tuple(
        (name, _default_state(value, memo)) for name, value in (fn.__kwdefaults__ or {}).items()
    )
    annotations = tuple(
        (name, _annotation_state(value, memo)) for name, value in fn.__annotations__.items()
    )
    if (
        signature_state is None
        or None in defaults
        or any(value is None for _, value in kwdefaults)
        or any(value is None for _, value in annotations)
    ):
        return None
    return (
        fn.__code__,
        id(fn.__globals__),
        defaults,
        kwdefaults,
        annotations,
        signature_state,
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
                deepcopy(_typed_state(kwargs)),
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
        options = _typed_state(kwargs)
        if options is None:
            return original(fn, **kwargs)
        declaration = inspect.unwrap(fn)
        state = _wrapper_state(fn)
        for source, prior_options, prior_state, template in templates:
            if (
                declaration is source
                and options == prior_options
                and state is not None
                and state == prior_state
            ):
                return template.model_copy(deep=True)
        return original(fn, **kwargs)

    return clone
