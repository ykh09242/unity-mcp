"""Reviewed action-dependent input rules shared by validation and discovery."""

from copy import deepcopy
from dataclasses import dataclass
import inspect
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from jsonschema import Draft202012Validator
from pydantic import Field

from services.action_inputs import INPUT_ACTION_REQUIRED, REFLECTION_REQUIRED, TERRAIN_REQUIRED
from services.declared_action_contracts import (
    ACTION_CONTRACTS,
    ASSET_MOVE_CONTRACTS,
    PREFAB_CHECKPOINT_CONTRACTS,
    PREFAB_FIELD_PATTERNS,
    PREFAB_PAGING_DEFAULTS,
)


_INPUT_ACTIONS = "x-unity-mcp-input-actions"
_REQUIRED_ACTIONS = "x-unity-mcp-required-for-actions"
_USED_ACTIONS = "x-unity-mcp-used-by-actions"
_INACTIVE_VALUE = "x-unity-mcp-inactive-value"


def input_actions(*actions: str, inactive_value=None):
    """Declare where a parameter has an effect. An explicit neutral value remains valid."""
    extra = {_INPUT_ACTIONS: list(actions)}
    if inactive_value is not None:
        extra[_INACTIVE_VALUE] = inactive_value
    return Field(json_schema_extra=extra)


def required_for_actions(*actions: str):
    """Declare where omission or JSON null cannot identify a valid request."""
    return Field(json_schema_extra={_REQUIRED_ACTIONS: list(actions)})


def used_by_actions(*actions: str):
    """Identify meaningful inputs without rejecting values older callers could supply."""
    return Field(json_schema_extra={_USED_ACTIONS: list(actions)})


def declared_optional_parameters(schema, operation):
    required = set(schema.get("required", ()))
    return [
        name
        for name, field in schema.get("properties", {}).items()
        if name not in required
        and isinstance(field, dict)
        and operation in field.get(_USED_ACTIONS, field.get(_INPUT_ACTIONS, ()))
    ]


def scoped_parameters(schema):
    return {
        name: field[_INPUT_ACTIONS]
        for name, field in schema.get("properties", {}).items()
        if isinstance(field, dict) and isinstance(field.get(_INPUT_ACTIONS), list)
    }


def required_action_parameters(schema):
    return {
        name: field[_REQUIRED_ACTIONS]
        for name, field in schema.get("properties", {}).items()
        if isinstance(field, dict) and isinstance(field.get(_REQUIRED_ACTIONS), list)
    }


def parameter_description(annotation):
    """Recover Annotated prose the SDK can omit beside other field metadata."""
    if get_origin(annotation) in (Union, UnionType):
        members = [member for member in get_args(annotation) if member is not type(None)]
        if len(members) == 1:
            return parameter_description(members[0])
    if get_origin(annotation) is Annotated:
        return next((meta for meta in get_args(annotation)[1:] if isinstance(meta, str)), None)
    return None


def publish_parameter_scopes(schema, function):
    """Keep action conditions visible to clients that render only individual fields."""
    properties = schema.get("properties", {})
    updated = {}
    parameters = None
    for name, field in properties.items():
        if not isinstance(field, dict):
            continue
        notes = []
        for key, prefix in (
            (_INPUT_ACTIONS, "Set only for"),
            (_REQUIRED_ACTIONS, "Required for"),
            (_USED_ACTIONS, "Used by"),
        ):
            if actions := field.get(key):
                notes.append(f"{prefix} {', '.join(actions)}.")
        if notes:
            description = field.get("description")
            if not description:
                if parameters is None:
                    parameters = inspect.signature(function, eval_str=True).parameters
                if name in parameters:
                    description = parameter_description(parameters[name].annotation)
            description = " ".join([*notes, description or ""]).rstrip()
            updated[name] = {**field, "description": description}
    return {**schema, "properties": {**properties, **updated}} if updated else schema


def bind_parameter_scopes(schema, action):
    """Project declared conditions onto a selected action without relaxing its wire schema."""
    fields = schema.get("properties", {})
    for name, actions in scoped_parameters(schema).items():
        if action not in actions:
            field = fields[name]
            inactive = field.get(_INACTIVE_VALUE)
            restriction = {"const": inactive}
            # Intersect with the field's own constraints, including an existing constant.
            if "const" in field:
                restriction = {"allOf": [*field.get("allOf", ()), restriction]}
            fields[name] = {**field, **restriction, "description": f"Omit for {action}."}
    for name, actions in required_action_parameters(schema).items():
        if action not in actions:
            continue
        field = dict(fields[name])
        if field.get("default") is None:
            field.pop("default", None)
        if "anyOf" in field:
            options = [option for option in field["anyOf"] if option != {"type": "null"}]
            if options:
                field["anyOf"] = options
        # A referenced type or another composition may itself allow null.
        # Keep the non-null rule local to the field as well as on the full request.
        non_null = {"not": {"type": "null"}}
        if "not" in field:
            field["allOf"] = [*field.get("allOf", ()), non_null]
        else:
            field.update(non_null)
        fields[name] = field


def conflicting_action_fields(schema, arguments):
    """Name submitted values that conflict with a declared action condition."""
    action = arguments.get("action")
    if not isinstance(action, str):
        return []
    return [
        {"field": name, "applies_to": actions}
        for name, actions in scoped_parameters(schema).items()
        if name in arguments
        and action not in actions
        and not Draft202012Validator(
            {"const": schema["properties"][name].get(_INACTIVE_VALUE)}
        ).is_valid(arguments[name])
    ]


def parameter_conditions(schema):
    properties = schema.get("properties", {})
    permitted = [
        {
            "if": {
                "required": [name],
                "properties": {name: {"not": {"const": properties[name].get(_INACTIVE_VALUE)}}},
            },
            "then": {"properties": {"action": {"enum": actions}}},
        }
        for name, actions in scoped_parameters(schema).items()
    ]
    required = [
        {
            "if": {"required": ["action"], "properties": {"action": {"enum": actions}}},
            "then": {"required": [name], "properties": {name: {"not": {"type": "null"}}}},
        }
        for name, actions in required_action_parameters(schema).items()
    ]
    return permitted + required


@dataclass(frozen=True)
class ActionRule:
    action: str
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    example: tuple[tuple[str, Any], ...] = ()
    # Guaranteed fields on the native success data, including fallback responses.
    success_fields: tuple[tuple[str, str], ...] = ()

    def example_arguments(self) -> dict:
        return {"action": self.action, **deepcopy(dict(self.example))}

    def success_data_schema(self) -> dict:
        return {
            "type": "object",
            "required": [name for name, _ in self.success_fields],
            "properties": {name: {"type": kind} for name, kind in self.success_fields},
        }


_ASSET_REQUIREMENTS = {
    "import": ("path",),
    "create": ("path", "asset_type"),
    "modify": ("path", "properties"),
    "delete": ("path",),
    "duplicate": ("path",),  # Existing native destination generation remains valid.
    "move": ASSET_MOVE_CONTRACTS["move"].required,
    "rename": ASSET_MOVE_CONTRACTS["rename"].required,
    "create_folder": ("path",),
}
_ASSET_EXAMPLES = {
    "path": "Assets/Materials/Example.mat",
    "asset_type": "Material",
    "properties": '{"name":"Example"}',
    "destination": "Assets/Materials/Renamed.mat",
}
_PREFAB_EDITS = tuple(
    "prefab_path target position rotation scale name tag layer set_active parent components_to_add components_to_remove create_child delete_child component_properties preview_only preview_scope".split()
)
_PREVIEW_FORBIDDEN = tuple(
    name
    for name in _PREFAB_EDITS
    if name not in {"prefab_path", "position", "rotation", "scale", "set_active"}
)


def _declared_rule(tool: str, action: str) -> ActionRule:
    contract = ACTION_CONTRACTS[tool][action]
    return ActionRule(
        action,
        contract.required,
        contract.forbidden,
        example=tuple(
            (name, _ASSET_EXAMPLES[name]) for name in contract.required if name in _ASSET_EXAMPLES
        ),
        success_fields=contract.result,
    )


_DOMAIN_INPUTS = {
    "manage_input_actions": INPUT_ACTION_REQUIRED,
    "manage_terrain": TERRAIN_REQUIRED,
    "unity_reflect": REFLECTION_REQUIRED,
}

ACTION_RULES: dict[str, tuple[ActionRule, ...]] = {
    "manage_tools": (ActionRule("list_instances", forbidden=("unity_instance",)),),
    **{
        tool: tuple(ActionRule(action, required) for action, required in actions.items())
        for tool, actions in _DOMAIN_INPUTS.items()
    },
    "gameobject_create": (ActionRule("create", ("name",), example=(("name", "Example"),)),),
    "manage_components": (
        ActionRule("add"),
        ActionRule("remove"),
        ActionRule("set_property"),
    ),
    "gameobject_positioning": (
        ActionRule(
            "move_relative",
            ("reference_object",),
            example=(("target", "Mover"), ("reference_object", "Anchor"), ("offset", [1, 0, 0])),
        ),
        ActionRule(
            "look_at",
            ("look_at_target",),
            example=(("target", "Mover"), ("look_at_target", [0, 0, 0])),
        ),
    ),
    "asset_edit": tuple(
        _declared_rule("asset_edit", action)
        if action in ACTION_CONTRACTS["asset_edit"]
        else ActionRule(
            action,
            required,
            example=tuple((name, _ASSET_EXAMPLES[name]) for name in required),
        )
        for action, required in _ASSET_REQUIREMENTS.items()
    ),
    "asset_reference_diagnostics": (_declared_rule("asset_reference_diagnostics", "preview_move"),),
    "manage_addressables": tuple(
        _declared_rule("manage_addressables", action) for action in PREFAB_CHECKPOINT_CONTRACTS
    ),
    "prefab_contents": (
        ActionRule(
            "modify_contents",
            ("prefab_path",),
            ("preview_id",),
            (("prefab_path", "Assets/Prefabs/Example.prefab"), ("position", [0, 1, 0])),
        ),
        ActionRule(
            "preview_modify_contents",
            ("prefab_path",),
            (*_PREVIEW_FORBIDDEN, "preview_id"),
            (("prefab_path", "Assets/Prefabs/Example.prefab"), ("position", [0, 1, 0])),
        ),
        ActionRule(
            "apply_preview",
            ("preview_id",),
            _PREFAB_EDITS,
            (("preview_id", "replace-with-observed-preview-token"),),
        ),
    ),
    "asset_bundles": (
        ActionRule("list_asset_bundles"),
        ActionRule(
            "get_bundle_assets", ("bundle_name",), example=(("bundle_name", "example-bundle"),)
        ),
        ActionRule(
            "get_bundle_dependencies",
            ("bundle_name",),
            example=(("bundle_name", "example-bundle"),),
        ),
    ),
}


def forbidden_action_parameters(name, action, schema):
    rule = next((item for item in ACTION_RULES.get(name, ()) if item.action == action), None)
    return list(
        dict.fromkeys(
            [
                *(rule.forbidden if rule else ()),
                *(
                    key
                    for key, actions in scoped_parameters(schema).items()
                    if action is not None and action not in actions
                ),
            ]
        )
    )


def action_input_error(name: str, arguments: dict) -> str | None:
    for rule in ACTION_RULES.get(name, ()):
        if rule.action != arguments.get("action"):
            continue
        for field in rule.required:
            value = arguments.get(field)
            if value is None or isinstance(value, str) and not value.strip():
                return f"Action '{rule.action}' requires parameter '{field}'."
        for field in rule.forbidden:
            if arguments.get(field) is not None:
                return f"Action '{rule.action}' does not accept parameter '{field}'."
        break
    if name == "gameobject_create":
        save = arguments.get("save_as_prefab")
        if save is True or isinstance(save, str) and save.strip().lower() == "true":
            path = arguments.get("prefab_path")
            folder = arguments.get("prefab_folder")
            if path is not None:
                if (
                    not isinstance(path, str)
                    or not path.strip()
                    or not path.lower().endswith(".prefab")
                ):
                    return "Parameter 'prefab_path' must be a nonempty .prefab path."
            elif not isinstance(folder, str) or not folder.strip():
                return "'prefab_path' or 'prefab_folder' is required when 'save_as_prefab' is true."
    return None


def action_help(name: str) -> str:
    rules = ACTION_RULES.get(name, ())
    if not rules:
        return ""
    rows = []
    for rule in rules:
        parts = []
        contract = ACTION_CONTRACTS.get(name, {}).get(rule.action)
        if rule.required:
            parts.append(
                ("accepts only " if contract and contract.exact_shape else "requires ")
                + ", ".join(rule.required)
            )
        if rule.forbidden:
            parts.append("excludes non-null " + ", ".join(rule.forbidden))
        if contract and contract.alias_of:
            parts.append("alias of " + contract.alias_of)
        if rule.success_fields:
            parts.append(
                "success data includes " + ", ".join(name for name, _ in rule.success_fields)
            )
        rows.append(rule.action + ": " + ("; ".join(parts) or "no action-specific required inputs"))
    return "Action inputs: " + ". ".join(rows) + "."


def _prefab_input_schema(schema: dict) -> dict:
    rules = []
    for action, contract in PREFAB_CHECKPOINT_CONTRACTS.items():
        fields = {
            name: {"type": "null"}
            for name in schema["properties"]
            if name not in {"action", *contract.required, *PREFAB_PAGING_DEFAULTS}
        }
        fields.update({name: {"const": value} for name, value in PREFAB_PAGING_DEFAULTS.items()})
        fields.update({name: {"type": "string"} for name in contract.required})
        for name in contract.required:
            if pattern := PREFAB_FIELD_PATTERNS.get(name):
                fields[name]["pattern"] = rf"^{pattern}(?![\s\S])"
        rules.append(
            {
                "if": {"properties": {"action": {"const": action}}, "required": ["action"]},
                "then": {"required": list(contract.required), "properties": fields},
            }
        )
    return {**schema, "allOf": [*schema.get("allOf", ()), *rules]}


def action_input_schema(name: str, schema: dict) -> dict:
    """Add conditional rules without replacing SDK types, aliases or defaults."""
    rules = ACTION_RULES.get(name, ())
    if not rules:
        return schema
    if name in _DOMAIN_INPUTS:
        conditions = []
        for rule in rules:
            if not rule.required:
                continue
            selector = {"const": rule.action}
            if name in {"manage_terrain", "unity_reflect"}:
                # Both wire handlers accept mixed-case ASCII action names.
                letters = "".join(f"[{c}{c.upper()}]" if c.isalpha() else c for c in rule.action)
                selector = {"pattern": rf"^{letters}(?![\s\S])"}
            conditions.append(
                {
                    "if": {"properties": {"action": selector}, "required": ["action"]},
                    "then": {
                        "required": list(rule.required),
                        "properties": {field: {"not": {"type": "null"}} for field in rule.required},
                    },
                }
            )
        return {**schema, "allOf": [*schema.get("allOf", ()), *conditions]}
    if name == "manage_addressables":
        return _prefab_input_schema(schema)
    if name == "gameobject_create":
        # The focused tool always creates; generic legacy actions retain their
        # original nullable name and action-specific defaults.
        path = {"type": "string", "pattern": r"\.[pP][rR][eE][fF][aA][bB](?![\s\S])"}
        folder = {"type": "string", "pattern": r"\S"}
        return {
            **schema,
            "required": list(dict.fromkeys([*schema.get("required", ()), "name"])),
            "properties": {
                **schema["properties"],
                "name": {**schema["properties"]["name"], "type": "string", "pattern": r"\S"},
            },
            "allOf": [
                *schema.get("allOf", ()),
                {
                    "if": {
                        "required": ["save_as_prefab"],
                        "properties": {
                            "save_as_prefab": {
                                "anyOf": [
                                    {"const": True},
                                    {"type": "string", "pattern": r"^\s*[tT][rR][uU][eE]\s*$"},
                                ]
                            }
                        },
                    },
                    "then": {
                        "anyOf": [
                            {"required": ["prefab_path"], "properties": {"prefab_path": path}},
                            {
                                "required": ["prefab_folder"],
                                "properties": {"prefab_folder": folder},
                            },
                        ],
                        "properties": {"prefab_path": {"anyOf": [{"type": "null"}, path]}},
                    },
                },
            ],
            "examples": [rule.example_arguments() for rule in rules],
        }
    if name == "manage_components":
        # Preserve the SDK's nullable value type: only presence is required for
        # a single write, so JSON null continues to clear object references.
        single = {
            "required": ["property", "value"],
            "properties": {"property": {"type": "string", "pattern": r"\S"}},
        }
        multiple = {
            "required": ["properties"],
            "properties": {
                "properties": {
                    "anyOf": [
                        {"type": "object", "minProperties": 1},
                        {"type": "string", "pattern": r'^\s*\{\s*"'},
                    ]
                }
            },
        }
        return {
            **schema,
            "allOf": [
                *schema.get("allOf", ()),
                {
                    "if": {
                        "properties": {"action": {"const": "set_property"}},
                        "required": ["action"],
                    },
                    "then": {
                        "anyOf": [single, multiple],
                        # If a property name is supplied it always owns a single
                        # write, even when a multiple-property update is also given.
                        "if": {
                            "required": ["property"],
                            "properties": {"property": {"type": "string", "minLength": 1}},
                        },
                        "then": single,
                    },
                },
            ],
        }
    if name == "asset_edit":
        # Typed action alternatives render as concrete objects in clients which
        # cannot express if/then schema intersections. Runtime rules stay shared.
        variants = []
        for rule in rules:
            properties = {"action": {"type": "string", "const": rule.action}}
            for field in rule.required:
                declared = deepcopy(schema["properties"][field])
                declared.pop("default", None)
                if "anyOf" in declared:
                    alternatives = [v for v in declared.pop("anyOf") if v.get("type") != "null"]
                    declared.update(
                        alternatives[0] if len(alternatives) == 1 else {"anyOf": alternatives}
                    )
                # String keywords apply only to string instances, including those
                # selected by anyOf. Objects retain their declared constraints.
                declared.update(minLength=1, pattern=r"\S")
                properties[field] = declared
            properties.update({field: {"type": "null"} for field in rule.forbidden})
            variants.append(
                {"type": "object", "properties": properties, "required": ["action", *rule.required]}
            )
        return {
            **schema,
            "oneOf": variants,
            "examples": [rule.example_arguments() for rule in rules],
        }
    conditions = []
    for rule in rules:
        properties = {
            field: {
                "anyOf": [
                    {"not": {"type": ["null", "string"]}},
                    {"type": "string", "minLength": 1, "pattern": r"\S"},
                ]
            }
            for field in rule.required
        }
        properties.update({field: {"type": "null"} for field in rule.forbidden})
        conditions.append(
            {
                "if": {"properties": {"action": {"const": rule.action}}, "required": ["action"]},
                "then": {"required": list(rule.required), "properties": properties},
            }
        )
    return {
        **schema,
        "allOf": [*schema.get("allOf", ()), *conditions],
        "examples": [rule.example_arguments() for rule in rules],
    }
