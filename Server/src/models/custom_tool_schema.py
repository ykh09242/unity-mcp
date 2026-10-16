"""Bounded version-one custom schemas using the installed JSON Schema validator."""

from collections import OrderedDict
from dataclasses import dataclass
import json
import math
from threading import RLock

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue

DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_SCHEMA_BYTES = 64 * 1024
MAX_CACHE_BYTES = 4 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
_ANNOTATIONS = {"title", "description", "examples", "$comment"}
_KEYWORDS = _ANNOTATIONS | {
    "$schema",
    "$defs",
    "$ref",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "default",
    "anyOf",
}


class SchemaContractError(ValueError):
    """A declaration or JSON value violates the supported bounded contract."""


def _encode_json(
    value: JsonValue, *, max_bytes: int, max_nodes: int, max_depth: int = 32
) -> tuple[str, int]:
    """Reject non-JSON/cyclic/oversized graphs before creating a canonical encoded copy."""
    active = set()
    work = [(value, 0, False)]
    nodes = characters = 0
    while work:
        item, depth, leaving = work.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise SchemaContractError("Custom JSON graph limit exceeded")
        match item:
            case None | bool() | int():
                try:
                    characters += len(str(item))
                except ValueError as exc:
                    raise SchemaContractError("Custom JSON integer is not encodable") from exc
            case float():
                if not math.isfinite(item):
                    raise SchemaContractError("Custom JSON must be finite")
                characters += len(str(item))
            case str():
                characters += len(item) + 2
            case list() | dict():
                if len(item) > max_nodes - nodes:
                    raise SchemaContractError("Custom JSON graph limit exceeded")
                if id(item) in active:
                    raise SchemaContractError("Custom JSON must not contain cycles")
                active.add(id(item))
                work.append((item, depth, True))
                characters += max(len(item) - 1, 0) + 2
                if isinstance(item, dict):
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise SchemaContractError("Custom JSON object keys must be strings")
                        characters += len(key) + 3
                        work.append((child, depth + 1, False))
                else:
                    work.extend((child, depth + 1, False) for child in item)
            case _:
                raise SchemaContractError("Custom value must be JSON")
        if characters > max_bytes:
            raise SchemaContractError("Custom JSON byte limit exceeded")
    chunks = []
    size = 0
    for chunk in json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).iterencode(value):
        try:
            size += len(chunk.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SchemaContractError("Custom JSON must be valid UTF8") from exc
        if size > max_bytes:
            raise SchemaContractError("Custom JSON byte limit exceeded")
        chunks.append(chunk)
    return "".join(chunks), nodes


def bounded_json(value: JsonValue, *, max_bytes: int, max_nodes: int, max_depth: int = 32) -> str:
    """Apply independent value limits without schema wrapper/depth overhead."""
    return _encode_json(value, max_bytes=max_bytes, max_nodes=max_nodes, max_depth=max_depth)[0]


def _profile(root: dict[str, JsonValue], *, input_root: bool) -> None:
    if input_root and (root.get("type") != "object" or "$ref" in root or "anyOf" in root):
        raise SchemaContractError("Custom input schema must have a nonnullable object root")
    definitions = root.get("$defs", {})
    if not isinstance(definitions, dict) or len(definitions) > 128:
        raise SchemaContractError("Custom schema definition limit exceeded")
    if root.get("$schema", DIALECT) != DIALECT:
        raise SchemaContractError("Unsupported custom schema dialect")
    work = [root, *definitions.values()]
    defaults = []
    while work:
        schema = work.pop()
        if not isinstance(schema, dict) or set(schema) - _KEYWORDS:
            raise SchemaContractError("Unsupported custom schema vocabulary")
        if schema is not root and ("$defs" in schema or "$schema" in schema):
            raise SchemaContractError("Custom definitions belong at the schema root")
        for key in _ANNOTATIONS:
            if (
                key in schema
                and key != "examples"
                and (not isinstance(schema[key], str) or len(schema[key]) > 1024)
            ):
                raise SchemaContractError("Custom schema text limit exceeded")
        if "$ref" in schema:
            allowed = _ANNOTATIONS | {"$ref", "default"}
            if schema is root:
                allowed |= {"$schema", "$defs"}
            if set(schema) - allowed:
                raise SchemaContractError("Custom references cannot have sibling validation rules")
            reference = schema["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise SchemaContractError("Only local custom schema references are supported")
            name = reference[len("#/$defs/") :]
            target = definitions.get(name)
            if (
                not name
                or len(name) > 256
                or "/" in name
                or "~" in name
                or "%" in name
                or not isinstance(target, dict)
                or not target.get("type")
                or "$ref" in target
                or "anyOf" in target
            ):
                raise SchemaContractError("Custom reference requires a concrete typed definition")
        kind = schema.get("type")
        if isinstance(kind, list) and (
            not all(isinstance(item, str) for item in kind)
            or len(kind) != 2
            or "null" not in kind
            or len(set(kind)) != 2
        ):
            raise SchemaContractError("Only nullable custom type unions are supported")
        if "anyOf" in schema:
            allowed = _ANNOTATIONS | {"anyOf", "default"}
            if schema is root:
                allowed |= {"$schema", "$defs"}
            if set(schema) - allowed:
                raise SchemaContractError(
                    "Nullable custom references cannot have sibling validation rules"
                )
            union = schema["anyOf"]
            if (
                not isinstance(union, list)
                or len(union) != 2
                or not any(branch == {"type": "null"} for branch in union)
                or not any(isinstance(branch, dict) and set(branch) == {"$ref"} for branch in union)
            ):
                raise SchemaContractError("Only nullable custom reference wrappers are supported")
            work.extend(union)
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > (
            128 if schema is root and input_root else 64
        ):
            raise SchemaContractError("Custom schema property limit exceeded")
        if any(len(name) > 256 for name in properties):
            raise SchemaContractError("Custom property name limit exceeded")
        if schema is root and input_root and set(properties) & {"ctx", "unity_instance"}:
            raise SchemaContractError("Custom input contains a reserved argument")
        work.extend(properties.values())
        for key in ("items", "additionalProperties"):
            child = schema.get(key)
            if isinstance(child, dict):
                work.append(child)
            elif child is not None and not (
                key == "additionalProperties" and isinstance(child, bool)
            ):
                raise SchemaContractError("Unsupported custom child schema")
        if "enum" in schema and (not isinstance(schema["enum"], list) or len(schema["enum"]) > 256):
            raise SchemaContractError("Custom enum limit exceeded")
        for low, high in (
            ("minimum", "maximum"),
            ("exclusiveMinimum", "maximum"),
            ("minimum", "exclusiveMaximum"),
            ("exclusiveMinimum", "exclusiveMaximum"),
            ("minLength", "maxLength"),
            ("minItems", "maxItems"),
            ("minProperties", "maxProperties"),
        ):
            if (
                low in schema
                and high in schema
                and isinstance(schema[low], (int, float))
                and isinstance(schema[high], (int, float))
                and (
                    schema[low] > schema[high]
                    or schema[low] == schema[high]
                    and "exclusive" in (low + high)
                )
            ):
                raise SchemaContractError("Custom schema bounds contradict")
        if "default" in schema:
            defaults.append((schema, schema["default"]))
    if any(len(name) > 256 or "/" in name or "~" in name or "%" in name for name in definitions):
        raise SchemaContractError("Invalid custom definition name")
    try:
        Draft202012Validator.check_schema(root)
        for schema, default in defaults:
            Draft202012Validator({"$defs": definitions, **schema}).validate(default)
    except (SchemaError, ValidationError) as exc:
        raise SchemaContractError("Invalid custom schema or default") from exc


@dataclass(frozen=True, slots=True)
class PreparedSchema:
    """Canonical identity; validators never share SDK-visible dictionaries."""

    input_json: str
    output_json: str | None
    input_validator: Draft202012Validator
    output_validator: Draft202012Validator | None

    def validate_input(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if not isinstance(arguments, dict):
            raise SchemaContractError("Custom input must be an object")
        bounded_json(arguments, max_bytes=256 * 1024, max_nodes=4096)
        schema = self.input_validator.schema
        applied = dict(arguments)
        for name, child in schema.get("properties", {}).items():
            if (
                name not in applied
                and name not in schema.get("required", [])
                and "default" in child
            ):
                applied[name] = json.loads(json.dumps(child["default"], allow_nan=False))
        bounded_json(applied, max_bytes=256 * 1024, max_nodes=4096)
        if not self.input_validator.is_valid(applied):
            raise SchemaContractError("Invalid custom tool input")
        return applied

    def validate_output(self, value: JsonValue) -> None:
        bounded_json(value, max_bytes=1024 * 1024, max_nodes=16384)
        if self.output_validator is not None and not self.output_validator.is_valid(value):
            raise SchemaContractError("Invalid custom tool output")


_cache: OrderedDict[tuple[str, str | None], PreparedSchema] = OrderedDict()
_cache_lock = RLock()
_cache_weight = 0


def prepare_schema(
    input_schema: dict[str, JsonValue], output_schema: dict[str, JsonValue] | None = None
) -> PreparedSchema:
    """Validate an entire declaration before publication; cache bounded canonical data only."""
    global _cache_weight
    input_json, nodes = _encode_json(input_schema, max_bytes=MAX_SCHEMA_BYTES, max_nodes=4096)
    remaining = MAX_SCHEMA_BYTES - len(input_json.encode("utf-8"))
    output_json = (
        None
        if output_schema is None
        else _encode_json(output_schema, max_bytes=remaining, max_nodes=4096 - nodes)[0]
    )
    key = (input_json, output_json)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    checked_input = json.loads(input_json)
    checked_output = None if output_json is None else json.loads(output_json)
    if not isinstance(checked_input, dict) or (
        checked_output is not None and not isinstance(checked_output, dict)
    ):
        raise SchemaContractError("Custom schemas must be JSON objects")
    _profile(checked_input, input_root=True)
    if checked_output is not None:
        _profile(checked_output, input_root=False)
    if (
        len(checked_input.get("$defs", {}))
        + (len(checked_output.get("$defs", {})) if checked_output else 0)
        > 128
    ):
        raise SchemaContractError("Custom combined definition limit exceeded")
    prepared = PreparedSchema(
        input_json,
        output_json,
        Draft202012Validator(checked_input),
        None if checked_output is None else Draft202012Validator(checked_output),
    )
    weight = len(input_json.encode("utf-8")) + (
        len(output_json.encode("utf-8")) if output_json else 0
    )
    with _cache_lock:
        if key not in _cache:
            _cache[key] = prepared
            _cache_weight += weight
        else:
            prepared = _cache[key]
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE_ENTRIES or _cache_weight > MAX_CACHE_BYTES:
            (old_input, old_output), _ = _cache.popitem(last=False)
            _cache_weight -= len(old_input.encode("utf-8")) + (
                len(old_output.encode("utf-8")) if old_output else 0
            )
    return prepared


def validation_contract(value: JsonValue) -> JsonValue:
    """Remove descriptive annotations only; preserve validation and applied defaults."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in _ANNOTATIONS:
                continue
            if key in {"properties", "$defs"}:
                result[key] = {name: validation_contract(schema) for name, schema in child.items()}
            elif key in {"items", "additionalProperties"} and isinstance(child, dict):
                result[key] = validation_contract(child)
            elif key == "anyOf":
                result[key] = [validation_contract(schema) for schema in child]
            else:
                result[key] = child
        return result
    return value


def prepare_descriptor(
    version: int,
    input_schema: dict[str, JsonValue] | None,
    output_schema: dict[str, JsonValue] | None,
    structured_output: bool | None,
) -> PreparedSchema | None:
    """Also protect preparation of models constructed or mutated without validation."""
    if type(version) is not int or version not in (0, 1):
        raise SchemaContractError("Unsupported custom schema version")
    if version == 0:
        if input_schema is not None or output_schema is not None:
            raise SchemaContractError("Rich schemas require explicit version one")
        return None
    if input_schema is None:
        raise SchemaContractError("Version one requires an input schema")
    if output_schema is not None and structured_output is not True:
        raise SchemaContractError("Typed output requires structured output")
    return prepare_schema(input_schema, output_schema)
