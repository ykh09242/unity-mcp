"""Find bounded string selectors using the advertised JSON Schema."""

import re
from collections.abc import Callable

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

OPERATION_SELECTOR_MAX_LENGTH = 128
_SCHEMA_ANNOTATIONS = {
    "$comment",
    "$defs",
    "$id",
    "$schema",
    "description",
    "title",
    "default",
    "deprecated",
    "examples",
    "readOnly",
    "writeOnly",
}
_PLAIN_OBJECT_KEYS = _SCHEMA_ANNOTATIONS | {
    "type",
    "properties",
    "required",
    "additionalProperties",
}
_PLAIN_VALUE_KEYS = _SCHEMA_ANNOTATIONS | {"type", "const", "enum"}
_ACTION_PREDICATE_KEYS = _SCHEMA_ANNOTATIONS | {
    "$ref",
    "type",
    "properties",
    "required",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
}


class _UnsupportedActionProjection(ValueError):
    """A selector cannot be verified independently of unknown inputs."""


def local_definition(reference: str, definitions: dict):
    if reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        return definitions.get(name)
    return None


def selector_values(field, definitions, seen=frozenset()) -> list[str] | None:
    """Enumerate bounded string selectors, including nullable local schema references."""
    if field is False:
        return []
    if not isinstance(field, dict) or id(field) in seen:
        return None
    if "const" in field:
        value = field["const"]
        return [value] if isinstance(value, str) else []
    if "enum" in field:
        return [value for value in field["enum"] if isinstance(value, str)]
    kind = field.get("type")
    if kind is not None and "string" not in (kind if isinstance(kind, list) else (kind,)):
        return []
    seen = seen | {id(field)}
    reference = field.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        values = selector_values(local_definition(reference, definitions), definitions, seen)
        if values is not None:
            return values
    for branch in field.get("allOf", ()):
        # The complete schema checks which literals from the finite bound are admitted.
        if (values := selector_values(branch, definitions, seen)) is not None:
            return values
    branches = field.get("oneOf") or field.get("anyOf")
    if branches:
        values = [selector_values(branch, definitions, seen) for branch in branches]
        if all(group is not None for group in values):
            return list(dict.fromkeys(value for group in values for value in group))
    return None


def _selector_reference(schema, definitions):
    if reference := schema.get("$dynamicRef") or schema.get("$recursiveRef"):
        raise Unresolvable(ref=reference)
    if not (reference := schema.get("$ref")):
        return None
    target = local_definition(reference, definitions)
    if target is None or isinstance(target, dict) and "$id" in target:
        raise Unresolvable(ref=reference)
    return target


def _action_upper_bound(schema, definitions) -> list[str] | None:
    if schema is False:
        return []
    if not isinstance(schema, dict):
        return None
    values = selector_values(schema.get("properties", {}).get("action"), definitions)
    if values is not None:
        return values
    target = _selector_reference(schema, definitions)
    if target is not None and (values := _action_upper_bound(target, definitions)) is not None:
        return values
    for branch in schema.get("allOf", ()):
        if (values := _action_upper_bound(branch, definitions)) is not None:
            return values
    for keyword in ("oneOf", "anyOf"):
        if branches := schema.get(keyword):
            values_by_branch = [_action_upper_bound(branch, definitions) for branch in branches]
            if all(branch is not None for branch in values_by_branch):
                return list(dict.fromkeys(value for branch in values_by_branch for value in branch))
    return None


def _action_only_predicate(schema, definitions) -> bool:
    return all(
        part is not None
        and part.keys() <= _ACTION_PREDICATE_KEYS
        and part.get("properties", {}).keys() <= {"action"}
        and set(part.get("required", ())) <= {"action"}
        for part in _same_instance_schemas(schema, definitions)
    )


def _optional_payload_predicate(predicate, required) -> bool:
    if not isinstance(predicate, dict):
        return False
    optional = set(predicate.get("required", ())) - required
    return bool(optional) and optional <= predicate.get("properties", {}).keys()


def _action_projection(schema, definitions, required, *, document_root=False) -> dict | bool:
    """Project supported object assertions onto an action, leaving other arguments unbound."""
    if isinstance(schema, bool):
        return schema
    if not document_root and "$id" in schema:
        raise _UnsupportedActionProjection
    if schema.keys() & {
        "const",
        "enum",
        "unevaluatedProperties",
        "dependencies",
        "dependentSchemas",
    }:
        raise _UnsupportedActionProjection
    required = required | set(schema.get("required", ()))
    projected = {
        key: schema[key]
        for key in ("patternProperties", "propertyNames", "maxProperties")
        if key in schema
    }
    if schema.get("type", "object") != "object":
        projected["type"] = schema["type"]
    if schema.get("additionalProperties", True) is not True:
        projected["additionalProperties"] = schema["additionalProperties"]
    if "action" in (properties := schema.get("properties", {})):
        projected["properties"] = {"action": properties["action"]}
    intersection = []
    if (target := _selector_reference(schema, definitions)) is not None:
        intersection.append(_action_projection(target, definitions, required))
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            branches = schema[keyword]
            constraints = [_action_projection(branch, definitions, required) for branch in branches]
            if keyword == "allOf":
                intersection.extend(constraints)
            elif keyword == "anyOf" and any(item in (True, {}) for item in constraints):
                continue
            elif keyword == "oneOf":
                selector_only = [
                    constraint
                    for branch, constraint in zip(branches, constraints, strict=True)
                    if _action_only_predicate(branch, definitions)
                ]
                if len(selector_only) == len(constraints):
                    projected[keyword] = constraints
                else:
                    # Non-selector arguments can distinguish these alternatives.
                    # The original oneOf still applies to the complete draft.
                    intersection.append({"anyOf": constraints})
                    if len(selector_only) > 1:
                        # Two selector-only matches would always violate oneOf.
                        intersection.append(
                            {"anyOf": [{"not": {"anyOf": selector_only}}, {"oneOf": selector_only}]}
                        )
            else:
                projected[keyword] = constraints
    if "not" in schema:
        predicate = schema["not"]
        if _action_only_predicate(predicate, definitions):
            projected["not"] = predicate
        elif _action_projection(predicate, definitions, required) not in (True, {}):
            raise _UnsupportedActionProjection
    if "if" in schema:
        outcomes = {
            branch: constraint
            for branch in ("then", "else")
            if (constraint := _action_projection(schema.get(branch, True), definitions, required))
            not in (True, {})
        }
        if outcomes:
            predicate = schema["if"]
            if _action_only_predicate(predicate, definitions):
                projected.update({"if": predicate, **outcomes})
            # A guard requiring an optional, constrained payload field need not
            # fire when selecting an action. The full schema still governs it.
            elif set(outcomes) != {"then"} or not _optional_payload_predicate(predicate, required):
                raise _UnsupportedActionProjection
    if constraints := [item for item in intersection if item not in (True, {})]:
        projected["allOf"] = constraints
    return projected


def advertised_actions(
    schema: dict, fallback: Callable[[], list[str]] | None = None
) -> list[str] | None:
    """Offer finite names admitted by the wire selector and its relevant branches."""
    definitions = schema.get("$defs", {})
    field = schema.get("properties", {}).get("action")
    try:
        plain = (
            schema.keys() <= _PLAIN_OBJECT_KEYS
            and schema.get("type", "object") == "object"
            and isinstance(field, dict)
            and field.keys() <= _PLAIN_VALUE_KEYS
        )
        candidates = (
            selector_values(field, definitions)
            if plain
            else _action_upper_bound(schema, definitions)
        )
        if candidates is None:
            if fallback is None:
                return None
            candidates = fallback()
        if not candidates:
            return []
        candidates = list(dict.fromkeys(candidates))
        if plain:
            kind = field.get("type", "string")
            if "string" not in (kind if isinstance(kind, list) else (kind,)):
                return []
            if "enum" in field and "const" in field:
                return [value for value in candidates if value in field["enum"]]
            return candidates
        required = {
            name
            for part in _same_instance_schemas(schema, definitions, unconditional_only=True)
            if part is not None
            for name in part.get("required", ())
        } | {"action"}
        projection = _action_projection(schema, definitions, required, document_root=True)
        validator = Draft202012Validator(schema, registry=Registry()).evolve(schema=projection)
        return [action for action in candidates if validator.is_valid({"action": action})]
    except (_UnsupportedActionProjection, Unresolvable, RecursionError, re.error):
        return []


def _same_instance_schemas(schema, definitions, *, unconditional_only=False):
    """Walk local applicators without entering nested object values."""
    pending = [schema]
    seen: set[int] = set()
    while pending:
        branch = pending.pop()
        if not isinstance(branch, dict) or id(branch) in seen:
            continue
        seen.add(id(branch))
        yield branch
        if "$dynamicRef" in branch or "$recursiveRef" in branch:
            yield None
        if isinstance(reference := branch.get("$ref"), str):
            target = local_definition(reference, definitions)
            if target is None:
                yield None
            else:
                pending.append(target)
        pending.extend(branch.get("allOf", ()))
        if not unconditional_only:
            pending.extend(child for key in ("anyOf", "oneOf") for child in branch.get(key, ()))
            pending.extend(branch.get(key) for key in ("if", "then", "else", "not"))
            pending.extend(branch.get("dependentSchemas", {}).values())


def _could_declare_literal(field, definitions) -> bool:
    return any(
        branch is None
        or isinstance(branch.get("const"), str)
        or any(isinstance(value, str) for value in branch.get("enum", ()))
        for branch in _same_instance_schemas(field, definitions)
    )


def _can_infer_choice(schema, definitions, bounded_fields: set[str]) -> bool:
    for branch in _same_instance_schemas(schema, definitions):
        if branch is None:
            return False
        properties = branch.get("properties", {})
        if "action" in properties or "action" in branch.get("required", ()):
            return False
        if any(
            name not in bounded_fields and _could_declare_literal(field, definitions)
            for name, field in properties.items()
        ):
            return False
    return True


def advertised_input_choices(schema: dict) -> dict[str, list[str]] | None:
    """Return selectable top-level literals admitted by their own parameter schema."""
    definitions = schema.get("$defs", {})
    validator = Draft202012Validator(schema, registry=Registry())
    components = list(_same_instance_schemas(schema, definitions, unconditional_only=True))
    if any(component is None for component in components):
        return None
    property_constraints = [component.get("properties", {}) for component in components]
    bounded_fields: set[str] = set()
    choices: dict[str, list[str]] = {}
    for name, field in schema.get("properties", {}).items():
        try:
            values = selector_values(field, definitions)
            if values is None:
                continue
            bounded_fields.add(name)
            if not values:
                continue
            requirements = [fields[name] for fields in property_constraints if name in fields]
            parameter = validator.evolve(schema={"allOf": requirements})
            admitted = [
                value
                for value in dict.fromkeys(values)
                if 1 <= len(value) <= OPERATION_SELECTOR_MAX_LENGTH and parameter.is_valid(value)
            ]
        except (Unresolvable, RecursionError):
            return None
        if admitted:
            choices[name] = admitted
    return choices if _can_infer_choice(schema, definitions, bounded_fields) else None
