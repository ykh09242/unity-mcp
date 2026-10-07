"""Check SDK argument structure before instance discovery, without running a tool."""

from functools import lru_cache
from typing import Any, Callable

from fastmcp.exceptions import ValidationError
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.types import get_cached_typeadapter
from pydantic_core import SchemaValidator, ValidationError as PydanticValidationError


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
        or (kind == "enum" and callable(value.get("missing")))
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
    tool: object, arguments: dict[str, object], *, strict: bool | None = None
) -> None:
    """Reuse FunctionTool's argument schema; never invoke handler/dependency code."""
    if not isinstance(tool, FunctionTool):
        return  # Remote/proxy tools own their validation beyond this local boundary.
    try:
        _argument_validator(tool.fn).validate_python(arguments, strict=strict)
    except PydanticValidationError as exc:
        # Input and arbitrary validator context must not enter logs/error text.
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        declared = tool.parameters.get("properties", {})
        locations = []
        for error in errors[:8]:
            location = error["loc"]
            field = location[0] if location and location[0] in declared else "arguments"
            # Dictionary keys and unknown keywords can contain caller data.
            suffix = "".join(f"[{part}]" if type(part) is int else "[*]" for part in location[1:])
            locations.append(f"{field}{suffix}: {error['type']}")
        details = "; ".join(locations)
        raise ValidationError(f"Invalid arguments for tool '{tool.name}': {details}") from None
