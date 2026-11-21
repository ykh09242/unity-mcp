"""Bounded predicates on one decoded MCP result. Paths are relative to its data object."""

import re
from typing import Literal, TypedDict


class Equal(TypedDict):
    op: Literal["equals"]
    path: str
    value: bool | str


class Choice(TypedDict):
    op: Literal["one_of"]
    path: str
    values: list[str]


class FieldType(TypedDict):
    op: Literal["type"]
    path: str
    type: Literal["string", "boolean", "array", "positive_integer", "nonnegative_integer", "hex32"]


class RelatedField(TypedDict):
    op: Literal["single_item_equals", "timestamp_lte"]
    path: str
    other_path: str


class OrderedHits(TypedDict):
    op: Literal["ordered_hits"]
    path: str
    total_path: str
    truncated_path: str
    rank_field: str


Predicate = Equal | Choice | FieldType | RelatedField | OrderedHits


class EvidencePredicates(TypedDict):
    all: list[Predicate]
    by_value: dict[str, list[Predicate]]


def _field(data: dict, path: str):
    if not isinstance(path, str) or len(path) > 128:
        return None
    keys = path.split(".")
    if len(keys) > 8:
        return None
    value = data
    for key in keys:
        if not isinstance(value, dict) or not key:
            return None
        value = value.get(key)
    return value


def _satisfies(data: dict, clause: Predicate) -> bool:
    if not isinstance(clause, dict):
        return False
    value = _field(data, clause.get("path"))
    match clause.get("op"):
        case "equals":
            expected = clause.get("value")
            return (
                type(expected) in (bool, str)
                and type(value) is type(expected)
                and value == expected
            )
        case "one_of":
            choices = clause.get("values")
            return (
                type(value) is str
                and type(choices) is list
                and 0 < len(choices) <= 16
                and value in choices
            )
        case "type":
            match clause.get("type"):
                case "string":
                    return type(value) is str
                case "boolean":
                    return type(value) is bool
                case "array":
                    return type(value) is list
                case "positive_integer":
                    return type(value) is int and value > 0
                case "nonnegative_integer":
                    return type(value) is int and value >= 0
                case "hex32":
                    return (
                        type(value) is str
                        and len(value) == 32
                        and re.fullmatch("[0-9a-f]{32}", value) is not None
                    )
        case "single_item_equals":
            other = _field(data, clause.get("other_path"))
            return (
                type(other) is int
                and type(value) is list
                and len(value) == 1
                and type(value[0]) is int
                and value[0] == other
            )
        case "timestamp_lte":
            other = _field(data, clause.get("other_path"))
            return type(value) is int and type(other) is int and 0 <= value <= other
        case "ordered_hits":
            total = _field(data, clause.get("total_path"))
            truncated = _field(data, clause.get("truncated_path"))
            field = clause.get("rank_field")
            return (
                type(value) is list
                and len(value) <= 128
                and type(total) is int
                and len(value) <= total <= 2**31 - 1
                and truncated is (len(value) < total)
                and (total == 0 or bool(value))
                and type(field) is str
                and all(
                    type(hit) is dict and type(hit.get(field)) is int and hit[field] == rank
                    for rank, hit in enumerate(value)
                )
            )
    return False


def evaluate_observation(
    observation: dict, reply: dict, required_values: list[str]
) -> dict[str, bool]:
    """Check each requested fact against the same successful receipt, including false or empty values."""
    if (
        not isinstance(required_values, (list, tuple))
        or len(required_values) > 32
        or any(type(value) is not str or len(value) > 128 for value in required_values)
    ):
        raise ValueError("Expected up to 32 bounded observation value IDs.")
    passed = dict.fromkeys(required_values, False)
    if (
        type(observation) is not dict
        or type(reply) is not dict
        or reply.get("success") is not True
        or type(reply.get("data")) is not dict
    ):
        return passed
    data = reply["data"]
    values, unavailable = observation.get("values"), observation.get("unavailable")
    paths = observation.get("wire_paths", {})
    if (
        any(type(names) is not list or len(names) > 32 for names in (values, unavailable))
        or type(paths) is not dict
    ):
        return passed
    if observation.get("conditions") and "predicates" not in observation:
        # Earlier declarations express these checks only in prose.
        return passed
    predicates = observation.get("predicates", {"all": [], "by_value": {}})
    if (
        type(predicates) is not dict
        or len(predicates) != 2
        or set(predicates) != {"all", "by_value"}
    ):
        return passed
    common, by_value = predicates["all"], predicates["by_value"]
    if (
        type(common) is not list
        or len(common) > 32
        or type(by_value) is not dict
        or not all(_satisfies(data, clause) for clause in common)
    ):
        return passed
    for value in passed:
        if value not in values or value in unavailable:
            continue
        if value in by_value:
            clauses = by_value[value]
            passed[value] = (
                type(clauses) is list
                and 0 < len(clauses) <= 32
                and all(_satisfies(data, clause) for clause in clauses)
            )
        else:
            path = paths.get(value, value)
            passed[value] = _field(data, path) is not None
    return passed
