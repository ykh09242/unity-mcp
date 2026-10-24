"""Advertise middleware-owned routing without changing tool payload contracts."""


def with_unity_instance(schema: dict) -> dict:
    """Return a new schema with the optional selector consumed before execution."""
    routed = {
        **schema,
        "properties": {
            **schema.get("properties", {}),
            "unity_instance": {
                "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}],
                "description": (
                    "Target Unity instance for this call: Name@hash or hash prefix; "
                    "an integer port is also supported over stdio. "
                    "Read mcpforunity://instances for available targets. "
                    "Omit to use the legacy active selection or the sole available instance."
                ),
            },
        },
    }

    # The selector is not part of the native object. Preserve the supported
    # rich-schema root constraints on that object, including exact object values.
    rules = list(routed.get("allOf", []))
    bounds = {key: routed.pop(key) for key in ("minProperties", "maxProperties") if key in routed}
    if bounds:
        rules.append(
            {
                "if": {"required": ["unity_instance"]},
                "then": {key: value + 1 for key, value in bounds.items()},
                "else": bounds,
            }
        )
    if "const" in routed:
        rules.append(_native_object_constant(routed.pop("const")))
    if "enum" in routed:
        choices = [_native_object_constant(value) for value in routed.pop("enum")]
        rules.append({"anyOf": choices} if choices else False)
    if rules:
        routed["allOf"] = rules
    return routed


def _native_object_constant(value):
    # Nonobjects and a reserved native argument cannot match a tool payload.
    if not isinstance(value, dict) or "unity_instance" in value:
        return False
    return {
        "properties": {
            **{key: {"const": item} for key, item in value.items()},
            "unity_instance": {},
        },
        "required": list(value),
        "additionalProperties": False,
    }
