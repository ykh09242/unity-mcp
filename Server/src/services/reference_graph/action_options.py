"""Option presence contracts shared by graph execution and action discovery."""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class RequiredWhen:
    field: str
    value: str | bool
    required: tuple[str, ...]
    matches_omitted: bool = False

    def schema(self) -> dict:
        condition = {"properties": {self.field: {"const": self.value}}}
        if not self.matches_omitted:
            condition["required"] = [self.field]
        return {"if": condition, "then": {"required": list(self.required)}}


@dataclass(frozen=True, slots=True)
class GraphOptions:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    exactly_one: tuple[str, ...] = ()
    conditions: tuple[RequiredWhen, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        return self.required + self.optional + self.exactly_one

    def missing(self, options: dict) -> list[str]:
        names = list(self.required)
        for condition in self.conditions:
            default = condition.value if condition.matches_omitted else None
            if options.get(condition.field, default) == condition.value:
                names.extend(condition.required)
        return [name for name in names if name not in options]

    def schema(self) -> dict:
        # The general tool accepts null as an empty options object. Keep that
        # spelling on actions that work without options, including runtime_start.
        schema = {
            "type": "object" if self.required or self.exactly_one else ["object", "null"],
            "properties": {name: {} for name in self.fields},
            "additionalProperties": False,
        }
        if self.required:
            schema["required"] = list(self.required)
        if self.exactly_one:
            schema["oneOf"] = [{"required": [name]} for name in self.exactly_one]
        if self.conditions:
            schema["allOf"] = [condition.schema() for condition in self.conditions]
        return schema


GRAPH_ACTION_OPTIONS: Final = {
    "overview": GraphOptions(),
    "export_config": GraphOptions(("kind",), ("after", "limit")),
    "import_config": GraphOptions(
        ("kind", "config", "expected_version"),
        ("id", "enabled", "reauthorize"),
        conditions=(
            RequiredWhen("kind", "source", ("id", "reauthorize")),
            RequiredWhen("kind", "rule", ("id",)),
        ),
    ),
    "cleanup": GraphOptions(("before",), ("limit",)),
    "retry": GraphOptions(),
    "status": GraphOptions(),
    "sync": GraphOptions(optional=("force",)),
    "query": GraphOptions(("node",), ("direction", "limit", "cursor", "consistency")),
    "diagnostics": GraphOptions(
        ("node",), ("offset", "limit", "revision", "cursor", "consistency")
    ),
    "explain": GraphOptions(("id",), ("consistency",)),
    "impact": GraphOptions(("node",), ("direction", "limit", "cursor", "max_depth", "consistency")),
    "path": GraphOptions(("node", "target"), ("direction", "limit", "cursor", "consistency")),
    "snapshot": GraphOptions(optional=("name", "limit", "cursor", "consistency")),
    "diff": GraphOptions(("before", "after"), ("limit", "cursor")),
    "release_query": GraphOptions(("id",)),
    "delete_snapshot": GraphOptions(("id",)),
    "begin_mutation": GraphOptions(exactly_one=("paths", "guids")),
    "add_mutation_targets": GraphOptions(("id",), exactly_one=("paths", "guids")),
    "finish_mutation": GraphOptions(("id",), ("outcome",)),
    "await_mutation": GraphOptions(("id",), ("wait_seconds",)),
    "mutation_diff": GraphOptions(("id",), ("limit", "cursor")),
    "release_mutation": GraphOptions(("id",)),
    "runtime_sessions": GraphOptions(optional=("after", "limit")),
    "runtime_start": GraphOptions(optional=("id",)),
    "runtime_poll": GraphOptions(("id",), ("limit",)),
    "runtime_status": GraphOptions(("id",)),
    "runtime_stop": GraphOptions(("id",), ("limit",)),
    "runtime_events": GraphOptions(("id",), ("after", "limit")),
    "runtime_handles": GraphOptions(("id",), ("after", "limit")),
    "runtime_release": GraphOptions(("id",)),
    "declarations": GraphOptions(),
    "declare": GraphOptions(("id", "owner", "edge", "expected_version")),
    "remove_declaration": GraphOptions(("id", "expected_version")),
    "sources": GraphOptions(),
    "set_source": GraphOptions(
        ("id", "expected_version"),
        ("config", "enabled"),
        conditions=(RequiredWhen("enabled", True, ("config",), matches_omitted=True),),
    ),
    "rules": GraphOptions(),
    "set_rule": GraphOptions(("id", "config", "expected_version")),
    "policy": GraphOptions(),
    "set_policy": GraphOptions(("expected_version",), ("config",)),
    "rebuild": GraphOptions(),
    "watch": GraphOptions(("enabled",)),
}
