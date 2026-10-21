"""Finite scalar dataset expansion; selectors and file paths are never binding destinations."""

from copy import deepcopy
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, model_validator

Name = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


def _bounded_scalar(value: JsonValue) -> JsonValue:
    if type(value) is int and not -9223372036854775808 <= value <= 9223372036854775807:
        raise ValueError("Parameter integers must fit signed 64 bits")
    return value


Scalar = Annotated[
    (
        Annotated[bool, Field(strict=True)]
        | Annotated[int, Field(strict=True, ge=-9223372036854775808, le=9223372036854775807)]
        | Annotated[float, Field(strict=True, allow_inf_nan=False)]
        | Annotated[str, Field(strict=True, max_length=1024)]
    ),
    BeforeValidator(_bounded_scalar),
]
DESTINATION = re.compile(
    r"/(steps|setup_steps|cleanup_steps)/(0|[1-9][0-9]*)/"
    r"(state_equals|count|active|timeout_seconds|stable_for_ms|wait_frames|event_count|"
    r"property/equals|(state_compare|property/compare)/"
    r"(value|min|max|absolute_tolerance|relative_tolerance))"
)
METADATA = {"parameters", "parameter_bindings", "datasets"}


class ScenarioParameter(BaseModel):
    """Declared scalar type and required default, checked without coercion."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["boolean", "integer", "number", "string"]
    default: Scalar

    def check_value(self, value: JsonValue) -> None:
        """Use exact runtime types so booleans cannot become numbers."""
        matches = {
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "string": type(value) is str,
        }
        if not matches[self.type]:
            raise ValueError("Parameter value must match its declared scalar type")
        if isinstance(value, str) and len(value.encode("utf-16-le", "surrogatepass")) > 2048:
            raise ValueError("Parameter string exceeds 1024 UTF-16 units")

    @model_validator(mode="after")
    def check_default(self) -> "ScenarioParameter":
        """Check declaration defaults before inspecting any bindings."""
        self.check_value(self.default)
        return self


class ScenarioParameterBinding(BaseModel):
    """An exact existing value destination; no pointer escaping or dynamic traversal."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    parameter: Name


class ScenarioDataset(BaseModel):
    """One explicit case, inheriting defaults for omitted declared values."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Name
    values: Annotated[dict[Name, Scalar], Field(strict=True, max_length=64)]


def _destination(source: dict[str, JsonValue], path: str) -> tuple[dict[str, JsonValue], str]:
    if DESTINATION.fullmatch(path) is None:
        raise ValueError("Binding path is outside permitted existing scalar destinations")
    parts = path.split("/")[1:]
    current: JsonValue = source
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError("Binding requires an existing scalar destination")
    if not isinstance(current, dict) or type(current.get(parts[-1])) not in (bool, int, float, str):
        raise ValueError("Binding requires an existing scalar destination")
    return current, parts[-1]


def expand_parameters(
    source: dict[str, JsonValue], dataset_name: str | None = None
) -> dict[str, JsonValue]:
    """Copy one effective concrete case; callers validate it with the ordinary step schema."""
    expanded = {key: deepcopy(value) for key, value in source.items() if key not in METADATA}
    selected = None
    if dataset_name is not None:
        selected = next(
            (item for item in source.get("datasets", []) if item["name"] == dataset_name), None
        )
        if selected is None:
            raise ValueError("Unknown dataset")
        expanded["name"] = f"{source['name']}--{dataset_name}"
    for binding in source.get("parameter_bindings", []):
        parameter = binding["parameter"]
        value = source["parameters"][parameter]["default"]
        if selected is not None:
            value = selected["values"].get(parameter, value)
        parent, key = _destination(expanded, binding["path"])
        parent[key] = deepcopy(value)
    return expanded


def validate_parameter_cases(
    source: dict[str, JsonValue], declarations: dict[str, ScenarioParameter] | None
) -> list[dict[str, JsonValue]]:
    """Admit bounded definitions and return every case for the ordinary parser."""
    if not source.keys() & METADATA:
        return []
    bindings = source.get("parameter_bindings")
    if not declarations or not isinstance(bindings, list) or not bindings:
        raise ValueError("Parameters require declarations and explicit bindings")
    paths, used = set(), set()
    for binding in bindings:
        parameter, path = binding["parameter"], binding["path"]
        if parameter not in declarations or path in paths:
            raise ValueError("Bindings must name declared parameters and unique destinations")
        _destination(source, path)
        paths.add(path)
        used.add(parameter)
    if used != declarations.keys():
        raise ValueError("Every parameter must have a binding")
    cases = [expand_parameters(source)]
    names = set()
    for dataset in source.get("datasets", []):
        if dataset["name"] in names:
            raise ValueError("Dataset names must be unique")
        names.add(dataset["name"])
        for key, value in dataset["values"].items():
            if key not in declarations:
                raise ValueError("Dataset names an undeclared parameter")
            declarations[key].check_value(value)
        cases.append(expand_parameters(source, dataset["name"]))
    return cases
