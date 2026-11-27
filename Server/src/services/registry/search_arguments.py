"""Two wire representations of the same read-only catalog request."""

from fastmcp.exceptions import ValidationError

from services.registry.task_search import RANKED_SEARCH_FIELDS, SearchRequest


def _one_search_value(*names):
    return {
        "anyOf": [
            {"required": [name], "properties": {name: {"not": {"type": "null"}}}} for name in names
        ]
    }


SEARCH_INPUT = {
    "properties": {"query": {"pattern": r"\S"}, "catalog_query": {"pattern": r"\S"}},
    "not": {
        "required": ["host_tool_names", "host_tool_evidence"],
        "properties": {
            "host_tool_names": {"not": {"type": "null"}},
            "host_tool_evidence": {"not": {"type": "null"}},
        },
    },
    "if": {"required": ["selection"], "properties": {"selection": {"const": "agent"}}},
    "then": {
        **_one_search_value("query", "family"),
        "properties": {name: {"type": "null"} for name in RANKED_SEARCH_FIELDS},
    },
    "else": {
        **_one_search_value("query", "observation_source", "required_values"),
        "properties": {"family": {"type": "null"}, "offset": {"const": 0}},
    },
}


def _outer_fields(managed):
    return ("query", "unity_instance", "action") if managed else ("query", "unity_instance")


def with_search_request(schema, *, managed):
    properties = schema["properties"]
    query = properties["query"]
    # Reuse the published flat constraints. Only the original text must be a leaf.
    nested = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            name: {"anyOf": query["anyOf"]} if name == "query" else {"$ref": f"#/properties/{name}"}
            for name in SearchRequest.model_fields.keys()
        },
        "allOf": [SEARCH_INPUT],
    }
    outer = {"propertyNames": {"enum": list(_outer_fields(managed))}}
    if managed:
        outer["properties"] = {"action": {"enum": ["search", "catalog"]}}
    return {
        **schema,
        "properties": {**properties, "query": {**query, "anyOf": [*query["anyOf"], nested]}},
        "allOf": [
            *schema.get("allOf", ()),
            {
                "if": {"required": ["query"], "properties": {"query": {"type": "object"}}},
                "then": outer,
            },
        ],
    }


def unpack_search_request(arguments, *, managed):
    request = arguments.get("query")
    if not isinstance(request, dict):
        return arguments
    if managed and arguments.get("action") not in ("search", "catalog"):
        raise ValidationError("A structured query requires search or unscoped catalog.")
    if arguments.keys() - _outer_fields(managed):
        raise ValidationError("Put all search options together, inside query or at the top level.")
    if request.keys() - SearchRequest.model_fields.keys():
        raise ValidationError("A structured query accepts only catalog search fields.")
    outer = {key: value for key, value in arguments.items() if key != "query"}
    if managed:
        outer["action"] = "search"
    return {**outer, **request}
