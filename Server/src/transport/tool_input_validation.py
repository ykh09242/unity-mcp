"""Check SDK argument structure before instance discovery, without running a tool."""

from enum import Enum, EnumType
from functools import lru_cache
import logging
from typing import Any, Callable, get_args

from fastmcp.exceptions import ValidationError
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.types import get_cached_typeadapter
from pydantic_core import ErrorType, SchemaValidator, ValidationError as PydanticValidationError

from models.custom_tool_schema import SchemaContractError, bounded_json


_ERROR_CODES = frozenset(get_args(ErrorType))
_SCHEMA_HINT = "Use tools/list on this connection to find a visible tool and its inputSchema."


def _structural_schema(value: Any) -> Any:
    # SDK core schemas are heterogeneous dictionaries. Only schema callbacks are
    # changed; defaults, aliases and field constraints retain the SDK's contract.
    if isinstance(value, list):
        return [_structural_schema(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_structural_schema(item) for item in value)
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    if kind == "function-after":
        schema = _structural_schema(value["schema"])
        return {**schema, **({"ref": value["ref"]} if "ref" in value else {})}
    if (
        kind in ("function-before", "function-wrap", "function-plain", "model", "dataclass")
        or (kind == "tagged-union" and callable(value.get("discriminator")))
        or (
            kind == "enum"
            and (
                callable(value.get("missing"))
                # Pydantic 2.14 invokes the enum class without a `missing` schema entry.
                or getattr(value["cls"]._missing_, "__func__", None) is not Enum._missing_.__func__
                or type(value["cls"]).__call__ is not EnumType.__call__
            )
        )
    ):
        # These may deliberately accept a different raw type or construct objects.
        # Leave them to the SDK so custom validators/constructors execute once.
        return {"type": "any", **({"ref": value["ref"]} if "ref" in value else {})}
    if kind == "default" and "default_factory" in value:
        return {
            **{
                key: item
                for key, item in value.items()
                if key not in ("default_factory", "default_factory_takes_data")
            },
            "schema": _structural_schema(value["schema"]),
            "default": None,
            "validate_default": False,
        }
    return {
        key: item if key in ("default", "metadata", "function", "cls") else _structural_schema(item)
        for key, item in value.items()
    }


@lru_cache(maxsize=128)
def _argument_validator(function: Callable) -> SchemaValidator:
    schema = get_cached_typeadapter(without_injected_parameters(function)).core_schema
    if schema["type"] == "definitions":
        arguments = {**schema, "schema": schema["schema"]["arguments_schema"]}
    else:
        arguments = schema["arguments_schema"]
    return SchemaValidator(_structural_schema(arguments))


def validate_tool_arguments(
    tool: object,
    arguments: dict[str, object],
    *,
    strict: bool | None = None,
    show_guidance: bool = False,
) -> None:
    """Reuse FunctionTool's argument schema; never invoke handler/dependency code."""
    if not isinstance(tool, FunctionTool):
        return  # Remote/proxy tools own their validation beyond this local boundary.
    try:
        _argument_validator(tool.fn).validate_python(arguments, strict=strict)
    except PydanticValidationError as exc:
        raise tool_argument_error(tool, exc, show_guidance=show_guidance, structural=True) from None


def sdk_argument_validation_cause(error: ValidationError) -> PydanticValidationError | None:
    """Recognize the SDK's separate pre-handler argument validation error."""
    cause = error.__cause__
    if (
        isinstance(cause, PydanticValidationError)
        and error.log_level == logging.WARNING
        and cause.title.startswith("call[")
    ):
        return cause
    return None


def tool_argument_error(
    tool: object,
    error: PydanticValidationError,
    *,
    show_guidance: bool = False,
    structural: bool = False,
) -> ValidationError:
    """Render declared fields and schema examples without reflecting rejected data."""
    declared = tool.parameters.get("properties", {}) if isinstance(tool, FunctionTool) else {}
    prefix = (
        f"Invalid arguments for tool '{tool.name}'"
        if isinstance(tool, FunctionTool)
        else "Invalid tool arguments"
    )
    locations, fields = [], {}
    for detail in error.errors(include_input=False, include_context=False, include_url=False)[:8]:
        location = detail["loc"]
        field = location[0] if location and location[0] in declared else None
        if field is not None:
            fields[field] = declared[field]
        # Model/dictionary keys and unknown keywords may contain caller data.
        suffix = "".join(
            f"[{part}]" if structural and type(part) is int else "[*]" for part in location[1:9]
        )
        code = detail["type"] if detail["type"] in _ERROR_CODES else "invalid_input"
        locations.append(f"{field or 'arguments'}{suffix}: {code}")

    messages = [f"{prefix}: {'; '.join(locations)}"]
    if show_guidance:
        remaining = 2
        for field, schema in fields.items():
            examples = schema.get("examples") if isinstance(schema, dict) else None
            if not isinstance(examples, list) or not examples:
                continue
            try:
                example = bounded_json(examples[0], max_bytes=512, max_nodes=64, max_depth=6)
            except SchemaContractError:
                continue
            description = schema.get("description")
            if isinstance(description, str) and 0 < len(description) <= 512:
                messages.append(description)
            messages.append(f"Published example for {field}: {example}")
            remaining -= 1
            if not remaining:
                break
        if len(messages) == 1:
            messages.append(_SCHEMA_HINT)
    return ValidationError("\n".join(messages))
