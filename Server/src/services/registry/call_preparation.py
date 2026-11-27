"""Construct a public call without executing it or acquiring permissions."""

from typing import Annotated

from jsonschema import Draft202012Validator, validators
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr
from referencing import Registry
from referencing.exceptions import Unresolvable

from models.custom_tool_schema import SchemaContractError, bounded_json, validation_details
from services.registry.action_contracts import conflicting_action_fields


PAGE_SIZE = "x-unity-mcp-page-size"
ORIGINAL_REQUEST = "x-unity-mcp-original-request"
MAX_ORIGINAL_REQUEST_LENGTH = 512
_Validator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _, value: type(value) is int
    ),
)


class ExactTextAssertion(BaseModel):
    """A caller-selected literal from the original request and its outbound field."""

    model_config = ConfigDict(extra="forbid")
    path: Annotated[
        list[StrictStr | Annotated[int, Field(strict=True, ge=0)]],
        Field(
            min_length=1,
            max_length=32,
            description="Path within arguments. Strings are exact object keys. Integers are zero-based array indices.",
        ),
    ]
    expected: Annotated[
        StrictStr,
        Field(
            min_length=1,
            max_length=MAX_ORIGINAL_REQUEST_LENGTH,
            description="Select this literal verbatim from the original query before drafting arguments.",
        ),
    ]


def not_prepared(reason: str, **details) -> dict:
    return {"success": False, "ready": False, "scope": "input_schema", "error": reason, **details}


def _page_maximum(name, definition, arguments):
    # Registered tools use page_size for response pagination. Other field names
    # must explicitly declare that meaning. Never infer a cap from prose.
    declared = definition.get(PAGE_SIZE)
    if isinstance(declared, dict):
        actions = declared.get("actions")
        if actions is not None and arguments.get("action") not in actions:
            return None
    elif name != "page_size" and declared is not True:
        return None
    alternatives = definition.get("anyOf", [definition])
    if not all(isinstance(branch, dict) for branch in alternatives):
        return None
    numeric = [branch for branch in alternatives if branch.get("type") != "null"]
    if len(numeric) == 1 and numeric[0].get("type") == "integer":
        cap = numeric[0].get("maximum")
        if cap is None and isinstance(declared, dict):
            cap = declared.get("maximum")
        if type(cap) is int and cap >= 1:
            return cap
    return None


def _exact_text_error(args: dict[str, JsonValue], assertion: ExactTextAssertion) -> str | None:
    value: JsonValue = args
    for segment in assertion.path:
        match value, segment:
            case dict() as values, str() as key if key in values:
                value = values[key]
            case list() as values, int() as index if index < len(values):
                value = values[index]
            case _:
                return "missing_path"
    if not isinstance(value, str):
        return "not_string"
    return "mismatch" if value != assertion.expected else None


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
    exact_text: list[ExactTextAssertion] | None = None,
):
    """Prepare registered pagination and original-text fields within the advertised schema."""
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
        maximum = _page_maximum(field, definition, args)
        if type(value) is int and maximum is not None and value > maximum:
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
        if conflicts := conflicting_action_fields(schema, args):
            details["action_field_conflicts"] = conflicts
        return not_prepared("The draft does not satisfy this input schema.", **details)

    result = {
        "ready": True,
        "scope": "input_schema",
        "prepared_call": {"tool": name, "arguments": args},
        "adjustments": adjustments,
    }
    if exact_text is not None:
        if not original:
            return not_prepared("Supply the original request verbatim in query with exact_text.")
        errors = []
        ranges = []
        for index, assertion in enumerate(exact_text):
            literal = assertion.expected
            start = original.find(literal)
            reason = "expected_not_in_query" if start < 0 else _exact_text_error(args, assertion)
            if reason is not None:
                errors.append({"index": index, "reason": reason})
            elif original.find(literal, start + 1) == -1:
                ranges.append({"start": start, "end": start + len(literal)})
            else:
                ranges.append(None)
        if errors:
            return not_prepared("Exact text check failed.", exact_text_errors=errors)
        result["exact_text_checked"] = len(exact_text)
        result["exact_text_evidence"] = {
            "scope": "caller_selected_text",
            "offset_unit": "unicode_code_points",
            "source_ranges": ranges,
        }
    return result
