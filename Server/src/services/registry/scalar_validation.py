"""Preserve declared scalar types before FastMCP can coerce tool arguments."""
from copy import copy
import inspect
from types import ModuleType, UnionType
from typing import Annotated, Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import AllowInfNan, Field, Strict
from pydantic.fields import FieldInfo


def _scalar_kind(annotation: Any) -> type | None:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _scalar_kind(get_args(annotation)[0])
    if origin in (Union, UnionType):
        branches = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _scalar_kind(branches[0]) if len(branches) == 1 else None
    return annotation if annotation in (bool, int, float) else None


def _description(annotation: Any) -> str | None:
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *metadata = get_args(annotation)
        for value in metadata:
            if isinstance(value, str):
                return value
        for value in metadata:
            if isinstance(value, FieldInfo) and value.description is not None:
                return value.description
        return _description(base)
    if origin in (Union, UnionType):
        return next((desc for arg in get_args(annotation) if (desc := _description(arg))), None)
    return None


def strict_scalar_annotation(annotation: Any) -> Any:
    """Make boolean/number leaves strict and floats finite; preserve metadata.

    Explicit string alternatives remain valid. Bare containers, Any, and JSON
    aliases stay opaque because they do not declare a scalar contract.
    """
    if annotation in (bool, int, float):
        if annotation is float:
            return Annotated[float, Field(strict=True, allow_inf_nan=False)]
        return Annotated[annotation, Strict()]
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        base, *metadata = arguments
        strict_base = strict_scalar_annotation(base)
        scalar = _scalar_kind(base)
        if scalar is not None:
            preserved = []
            for value in metadata:
                if isinstance(value, Strict):
                    value = Strict()
                elif isinstance(value, AllowInfNan) and scalar is float:
                    value = AllowInfNan(False)
                elif isinstance(value, FieldInfo) and any(isinstance(item, (Strict, AllowInfNan)) for item in value.metadata):
                    value = copy(value)
                    value.metadata = [
                        Strict() if isinstance(item, Strict) else
                        AllowInfNan(False) if isinstance(item, AllowInfNan) and scalar is float else item
                        for item in value.metadata
                    ]
                preserved.append(value)
            metadata = preserved
        return Annotated[(strict_base, *metadata)]
    if origin in (Union, UnionType):
        return Union[tuple(strict_scalar_annotation(arg) for arg in arguments)]
    if origin in (list, dict, tuple, set, frozenset):
        return origin[tuple(strict_scalar_annotation(arg) for arg in arguments)] if arguments else annotation
    if origin is Literal:
        return annotation  # Tool enums declare strings; enum members are values, not types.
    return annotation


def enforce_strict_tool_inputs(func: Callable) -> Callable:
    """Update input annotations in place without wrapping or changing defaults."""
    annotations = dict(func.__annotations__)
    # Python 3.10 adds Optional around function annotations with None defaults,
    # nesting Annotated metadata. Resolve declared types in the original globals.
    annotation_scope = ModuleType(func.__module__)
    annotation_scope.__annotations__ = annotations
    hints = get_type_hints(annotation_scope, globalns=func.__globals__, include_extras=True)
    for name in inspect.signature(func).parameters:
        if name == "ctx" or name not in hints:
            continue
        original = hints[name]
        strict = strict_scalar_annotation(original)
        if strict != original:
            # FastMCP ignores legacy string descriptions once other metadata is
            # present, and descriptions within optional branches are not exposed.
            description = _description(original)
            if description is not None:
                strict = Annotated[strict, Field(description=description)]
            annotations[name] = strict
    func.__annotations__ = annotations
    if hasattr(func, "__annotate__"):
        # Python 3.14 wraps() copies __annotate__, rather than __annotations__.
        # Assigning annotations clears that provider, so publish the new values
        # through the public deferred-annotation interface as well.
        # The __annotate__ guard excludes interpreters without annotationlib.
        from annotationlib import Format, annotations_to_string  # pylint: disable=import-error

        def annotate(format, /):
            if format == Format.STRING:
                return annotations_to_string(annotations)
            if format in (Format.VALUE, Format.FORWARDREF):
                return dict(annotations)
            raise NotImplementedError

        func.__annotate__ = annotate
    return func
