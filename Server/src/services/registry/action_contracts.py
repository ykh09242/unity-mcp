"""Reviewed action-dependent input rules shared by validation and discovery."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionRule:
    action: str
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    example: tuple[tuple[str, Any], ...] = ()

    def example_arguments(self) -> dict:
        return {"action": self.action, **deepcopy(dict(self.example))}


_ASSET_REQUIREMENTS = {
    "import": ("path",),
    "create": ("path", "asset_type"),
    "modify": ("path", "properties"),
    "delete": ("path",),
    "duplicate": ("path",),  # Existing native destination generation remains valid.
    "move": ("path", "destination"),
    "rename": ("path", "destination"),
    "create_folder": ("path",),
}
_ASSET_EXAMPLES = {
    "path": "Assets/Materials/Example.mat",
    "asset_type": "Material",
    "properties": '{"name":"Example"}',
    "destination": "Assets/Materials/Renamed.mat",
}
_PREFAB_EDITS = tuple(
    "prefab_path target position rotation scale name tag layer set_active parent components_to_add components_to_remove create_child delete_child component_properties preview_only".split()
)
_PREVIEW_FORBIDDEN = tuple(
    name
    for name in _PREFAB_EDITS
    if name not in {"prefab_path", "position", "rotation", "scale", "set_active"}
)

ACTION_RULES: dict[str, tuple[ActionRule, ...]] = {
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
        ActionRule(
            action, required, example=tuple((name, _ASSET_EXAMPLES[name]) for name in required)
        )
        for action, required in _ASSET_REQUIREMENTS.items()
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
    return None


def action_help(name: str) -> str:
    rules = ACTION_RULES.get(name, ())
    if not rules:
        return ""
    rows = []
    for rule in rules:
        parts = []
        if rule.required:
            parts.append("requires " + ", ".join(rule.required))
        if rule.forbidden:
            parts.append("excludes non-null " + ", ".join(rule.forbidden))
        rows.append(rule.action + ": " + ("; ".join(parts) or "no action-specific required inputs"))
    return "Action inputs: " + ". ".join(rows) + "."


def action_input_schema(name: str, schema: dict) -> dict:
    """Add conditional rules without replacing SDK types, aliases or defaults."""
    rules = ACTION_RULES.get(name, ())
    if not rules:
        return schema
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
