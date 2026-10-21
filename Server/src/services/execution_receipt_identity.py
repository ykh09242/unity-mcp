"""Strict, callback-free request identity for optional execution receipts.

This module grants no authority and performs no dispatch. Callers supply only the
SDK-parsed arguments and server-validated semantic envelope after normal policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
Surface = Literal["mcp-tool", "/api/command", "/api/context-command"]
PERMISSIONS = frozenset({"read", "test", "edit", "run"})
# Match the pinned PluginHub command graph ceilings without importing its runtime.
MAX_ARGUMENT_BYTES = 32 * 1024 * 1024
MAX_ARGUMENT_NODES = 1_000_000
MAX_ARGUMENT_DEPTH = 64
KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{16,128}\Z")
HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
PROJECT_HASH = re.compile(r"[0-9a-f]{16}\Z")


class ReceiptInvalid(ValueError):
    """Malformed optional protection must fail before any handler work."""


class ReceiptUnavailable(RuntimeError):
    """No protected execution is allowed while the store is unavailable."""


class ReceiptCapacity(ReceiptUnavailable):
    """Bounded receipt admission is exhausted; never evict an unexpired key."""


class ReceiptConflict(ValueError):
    """The retained key belongs to a different exact request."""


class ReceiptNotFound(LookupError):
    """Uniform absent, foreign, expired, wrong-store or unauthorized lookup."""


def _bounded_json(value: object, *, own: bool) -> JsonValue:
    """Own exact builtin JSON containers under fixed traversal/encoded bounds.

    No deepcopy hooks, callbacks, schema defaults or model serializers are run.
    Strings remain immutable shared values; all mutable containers are owned.
    """
    nodes = size = 0

    def charge(count: int) -> None:
        nonlocal size
        size += count
        if size > MAX_ARGUMENT_BYTES:
            raise ReceiptInvalid("receipt_request_too_large")

    def string_size(item: str) -> None:
        if len(item) > MAX_ARGUMENT_BYTES:
            raise ReceiptInvalid("receipt_request_too_large")
        charge(2)
        for offset in range(0, len(item), 512):
            try:
                escaped = json.dumps(item[offset : offset + 512], ensure_ascii=False)
                encoded = escaped.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ReceiptInvalid("receipt_request_not_utf8") from error
            charge(len(encoded) - 2)

    def visit(item: object, depth: int) -> JsonValue:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_ARGUMENT_NODES or depth > MAX_ARGUMENT_DEPTH:
            raise ReceiptInvalid("receipt_request_graph_limit")
        if type(item) is dict:
            if len(item) > MAX_ARGUMENT_NODES:
                raise ReceiptInvalid("receipt_request_graph_limit")
            charge(2 + max(0, len(item) - 1))
            result: dict[str, JsonValue] = {} if own else item
            for key, child in item.items():
                if type(key) is not str:
                    raise ReceiptInvalid("receipt_request_not_json")
                string_size(key)
                charge(1)
                validated = visit(child, depth + 1)
                if own:
                    result[key] = validated
            return result
        if type(item) is list:
            if len(item) > MAX_ARGUMENT_NODES:
                raise ReceiptInvalid("receipt_request_graph_limit")
            charge(2 + max(0, len(item) - 1))
            if own:
                return [visit(child, depth + 1) for child in item]
            for child in item:
                visit(child, depth + 1)
            return item
        if type(item) is str:
            string_size(item)
            return item
        if item is None or type(item) in (int, bool, float):
            if type(item) is float and not math.isfinite(item):
                raise ReceiptInvalid("receipt_request_not_json")
            try:
                encoded_length = len(json.dumps(item, allow_nan=False))
            except ValueError as error:
                raise ReceiptInvalid("receipt_request_not_json") from error
            charge(encoded_length)
            return item
        raise ReceiptInvalid("receipt_request_not_json")

    return visit(value, 0)


def owned_json(value: object) -> JsonValue:
    """Validate and own JSON without invoking callbacks or serialization hooks."""
    return _bounded_json(value, own=True)


@dataclass(frozen=True, slots=True)
class ReceiptOptions:
    store_id: str
    project_hash: str
    idempotency_key: str | None = None
    retain_result: bool = False

    @classmethod
    def parse(cls, value: object) -> ReceiptOptions:
        if type(value) is not dict or not {"version", "store_id", "project_hash"} <= value.keys():
            raise ReceiptInvalid("invalid_execution_options")
        allowed = {"version", "store_id", "project_hash", "idempotency_key", "retain_result"}
        if value.keys() - allowed:
            raise ReceiptInvalid("invalid_execution_options")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ReceiptInvalid("unsupported_execution_version")
        if "idempotency_key" in value and type(value["idempotency_key"]) is not str:
            raise ReceiptInvalid("invalid_execution_options")
        options = cls(
            value["store_id"],
            value["project_hash"],
            value.get("idempotency_key"),
            value.get("retain_result", False),
        )
        options.validate()
        if len(json.dumps(value, ensure_ascii=True, separators=(",", ":"))) > 1024:
            raise ReceiptInvalid("execution_options_too_large")
        return options

    def validate(self) -> None:
        if type(self.store_id) is not str or not HEX_ID.fullmatch(self.store_id):
            raise ReceiptInvalid("invalid_execution_options")
        if type(self.project_hash) is not str or not PROJECT_HASH.fullmatch(self.project_hash):
            raise ReceiptInvalid("invalid_execution_options")
        if self.idempotency_key is not None and (
            type(self.idempotency_key) is not str or not KEY_PATTERN.fullmatch(self.idempotency_key)
        ):
            raise ReceiptInvalid("invalid_execution_options")
        if type(self.retain_result) is not bool:
            raise ReceiptInvalid("invalid_execution_options")


@dataclass(frozen=True, slots=True)
class ReceiptScope:
    """Server-owned stable principal/project; never construct from receipt claims."""

    owner: str
    project_hash: str
    owner_kind: Literal["remote", "local"] = "remote"

    def __post_init__(self) -> None:
        if type(self.owner) is not str or not 1 <= len(self.owner) <= 1024:
            raise ReceiptInvalid("invalid_receipt_scope")
        if type(self.project_hash) is not str or not PROJECT_HASH.fullmatch(self.project_hash):
            raise ReceiptInvalid("invalid_receipt_scope")
        if type(self.owner_kind) is not str or self.owner_kind not in {"remote", "local"}:
            raise ReceiptInvalid("invalid_receipt_scope")

    @property
    def owner_digest(self) -> str:
        value = "execution-owner-v1:" + self.owner_kind + ":" + self.owner
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    digest: str
    encoded_bytes: int
    arguments: dict[str, JsonValue]


def capture_identity(
    *,
    surface: Surface,
    name: str,
    arguments: object,
    options: ReceiptOptions,
    semantic_context: object,
    component_version: str | None = None,
) -> RequestIdentity:
    """Capture exact parsed values; omission/default/coercion equivalence is not inferred.

    REST context is the complete validated semantic envelope, captured once,
    including expected_project.root. It is never reconstructed from a live target.
    """
    options.validate()
    if surface not in {"mcp-tool", "/api/command", "/api/context-command"}:
        raise ReceiptInvalid("unsupported_receipt_surface")
    if type(name) is not str or not 1 <= len(name) <= 256:
        raise ReceiptInvalid("invalid_receipt_operation")
    if component_version is not None and (
        type(component_version) is not str or not 1 <= len(component_version) <= 128
    ):
        raise ReceiptInvalid("invalid_component_version")
    owned = owned_json(arguments)
    context = owned_json(semantic_context)
    if type(owned) is not dict or type(context) is not dict:
        raise ReceiptInvalid("invalid_receipt_request")
    if surface == "/api/context-command" and (
        type(context.get("expected_project")) is not dict
        or type(context["expected_project"].get("root")) is not str
        or not 1 <= len(context["expected_project"]["root"]) <= 4096
    ):
        raise ReceiptInvalid("missing_receipt_project_expectation")
    frame = _bounded_json(
        {
            "version": 1,
            "surface": surface,
            "name": name,
            "arguments": {
                key: item
                for key, item in owned.items()
                if surface != "mcp-tool" or key != "unity_instance"
            },
            "semantic_context": context,
            "retain_result": options.retain_result,
            "component_version": component_version,
        },
        own=False,
    )
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    digest = hashlib.sha256()
    length = 0
    for chunk in encoder.iterencode(frame):
        raw = chunk.encode("utf-8")
        digest.update(raw)
        length += len(raw)
    return RequestIdentity(digest.hexdigest(), length, owned)


def permission_floor(value: frozenset[str]) -> tuple[str, ...]:
    if type(value) is not frozenset or not value <= PERMISSIONS:
        raise ReceiptInvalid("invalid_receipt_permission_floor")
    return tuple(sorted(value))
