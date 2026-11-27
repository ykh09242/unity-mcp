"""Find bounded string selectors using the advertised JSON Schema."""

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

OPERATION_SELECTOR_MAX_LENGTH = 128


def local_definition(reference: str, definitions: dict):
    if reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        return definitions.get(name)
    return None


def selector_values(
    field, definitions, seen=frozenset(), *, include_intersections=False
) -> list[str] | None:
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
        values = selector_values(
            local_definition(reference, definitions),
            definitions,
            seen,
            include_intersections=include_intersections,
        )
        if values is not None or not include_intersections:
            return values
    if include_intersections:
        for branch in field.get("allOf", ()):
            # One finite operand bounds the intersection. The complete schema
            # still decides which of those literals are actually admitted.
            if (
                values := selector_values(branch, definitions, seen, include_intersections=True)
            ) is not None:
                return values
    branches = field.get("oneOf") or field.get("anyOf")
    if branches:
        values = [
            selector_values(branch, definitions, seen, include_intersections=include_intersections)
            for branch in branches
        ]
        if all(group is not None for group in values):
            return list(dict.fromkeys(value for group in values for value in group))
    return None


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
            values = selector_values(field, definitions, include_intersections=True)
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
