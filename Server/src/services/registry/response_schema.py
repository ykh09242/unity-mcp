"""Keep object-valued tool envelopes at the structuredContent root."""

from copy import deepcopy


def direct_object_output_schema(schema):
    if not isinstance(schema, dict) or schema.get("x-fastmcp-wrap-result") is not True:
        return schema
    payload = schema.get("properties", {}).get("result")
    definitions = schema.get("$defs", {})

    def is_object(value, seen=frozenset()):
        if not isinstance(value, dict):
            return False
        if value.get("type") == "object":
            return True
        reference = value.get("$ref")
        if (
            isinstance(reference, str)
            and reference.startswith("#/$defs/")
            and reference not in seen
        ):
            return is_object(definitions.get(reference[8:]), seen | {reference})
        variants = value.get("anyOf") or value.get("oneOf")
        return (
            isinstance(variants, list)
            and bool(variants)
            and all(is_object(v, seen) for v in variants)
        )

    if not is_object(payload):
        return schema  # Lists/scalars and mixed content keep their SDK contract.
    result = {"type": "object", **deepcopy(payload)}
    if definitions:
        result["$defs"] = deepcopy(definitions)
    return result
