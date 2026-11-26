"""Construct a public call without executing it or acquiring permissions."""

from jsonschema import Draft202012Validator, validators
from referencing import Registry
from referencing.exceptions import Unresolvable

from models.custom_tool_schema import SchemaContractError, bounded_json, validation_details


PAGE_SIZE = "x-unity-mcp-page-size"
ORIGINAL_REQUEST = "x-unity-mcp-original-request"
_Validator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _, value: type(value) is int
    ),
)


def not_prepared(reason: str, **details) -> dict:
    return {"success": False, "ready": False, "scope": "input_schema", "error": reason, **details}


def prepare_tool_call(
    name,
    schema,
    draft,
    *,
    operation=None,
    original=None,
    project=None,
    registered=False,
    closed_signature=False,
):
    """Only adjust fields explicitly annotated by the owner of this tool schema."""
    try:
        bounded_json(draft, max_bytes=64 * 1024, max_nodes=4096)
    except SchemaContractError as exc:
        return not_prepared(str(exc))

    args = dict(draft)
    fields = schema.get("properties", {})
    if "unity_instance" in args and (project is None or args["unity_instance"] != project):
        return not_prepared("Specify the same explicit project on prepare and its target call.")
    if project is not None and "unity_instance" in fields:
        args["unity_instance"] = project
    if operation is not None:
        args.setdefault("action", operation)

    adjustments = []
    for field, definition in fields.items() if registered else ():
        if not isinstance(definition, dict):
            continue
        source = definition.get(ORIGINAL_REQUEST)
        if isinstance(source, dict) and all(args.get(k) == v for k, v in source.items()):
            if field not in args and original is not None:
                args[field] = original
                adjustments.append({"field": field, "source": "original_request"})
        value = args.get(field)
        maximum = definition.get("maximum")
        if (
            definition.get(PAGE_SIZE) is True
            and type(value) is int
            and type(maximum) is int
            and value > maximum
        ):
            args[field] = maximum
            adjustments.append({"field": field, "source": "page_maximum", "value": maximum})

    # Function signatures can be closed even when the SDK omits additionalProperties.
    if (
        closed_signature
        and "properties" in schema
        and "additionalProperties" not in schema
        and "patternProperties" not in schema
    ):
        schema = {**schema, "additionalProperties": False}
    try:
        # Referencing's empty registry performs no retrieval outside this schema.
        validator = _Validator(schema, registry=Registry())
        details = validation_details(
            (
                (tuple(error.absolute_path), error.validator)
                for error in validator.iter_errors(args)
            ),
            set(fields),
        )
    except (Unresolvable, RecursionError):
        return not_prepared("The advertised input schema could not be resolved locally.")
    if details["validation_errors"]:
        return not_prepared("The draft does not satisfy this input schema.", **details)
    return {
        "ready": True,
        "scope": "input_schema",
        "prepared_call": {"tool": name, "arguments": args},
        "adjustments": adjustments,
    }
