"""Drive real resource read/send ownership cohorts without timing assertions."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import anyio
from mcp import ClientSession
from mcp.types import TextResourceContents
from pydantic import JsonValue, TypeAdapter

from tools.bench_transport_report import fingerprint

Control = Callable[[str, dict[str, JsonValue] | None], Awaitable[JsonValue]]


def normalize_resource(raw: JsonValue) -> JsonValue:
    """Validate volatile integers; omit exactly two named time fields for parity."""
    value = TypeAdapter(JsonValue).validate_json(json.dumps(raw))
    if not isinstance(value, dict) or value.get("success") is not True or not isinstance(value.get("data"), dict):
        raise ValueError(f"Resource did not return successful editor state: {str(value)[:1200]}")
    data = value["data"]
    observed = data.get("observed_at_unix_ms")
    staleness = data.get("staleness")
    if (type(observed) is not int or observed <= 0 or not isinstance(staleness, dict)
            or type(staleness.get("age_ms")) is not int or staleness["age_ms"] < 0):
        raise ValueError("Resource dynamic timestamp fields are invalid")
    del data["observed_at_unix_ms"]
    del staleness["age_ms"]
    return value


async def observe_resources(session: ClientSession, control: Control, counts: dict[str, int]) -> list[JsonValue]:
    """Observe ordinary and authoritative cohorts with two held real responses."""
    async def observe_cohort(mode: str) -> JsonValue:
        uri = "mcpforunity://editor/state" + ("/authoritative" if mode == "authoritative" else "")
        before = await control("bench_status", None)
        await control("bench_resource_arm", None)
        outputs: dict[int, JsonValue] = {}

        async def read_one(index: int) -> None:
            counts["resources/read"] = counts.get("resources/read", 0) + 1
            result = await session.read_resource(uri)
            text = next(item.text for item in result.contents if isinstance(item, TextResourceContents))
            outputs[index] = TypeAdapter(JsonValue).validate_json(text)

        async with anyio.create_task_group() as group:
            group.start_soon(read_one, 0)
            group.start_soon(read_one, 1)
            held = await control("bench_resource_held", None)
            if (not isinstance(held, dict) or held.get("hub_reserved_bytes", 0) + held.get("shared_read_reserved_bytes", 0) <= 0):
                raise RuntimeError("Resource body hold has no positive actual reservation")
            await control("bench_resource_release", None)
        after = await control("bench_wait_drained", None)
        raw = [outputs[index] for index in range(2)]
        hashes = [fingerprint(normalize_resource(value))[0] for value in raw]
        if len(set(hashes)) != 1:
            raise RuntimeError("Concurrent resource semantic payloads differ")
        return {"mode": mode, "before": before, "held": held, "after": after,
                        "raw_results": raw, "normalized_sha256": hashes,
                        "normalization_paths": ["data.observed_at_unix_ms", "data.staleness.age_ms"]}
    return [await observe_cohort(mode) for mode in ("ordinary", "authoritative")]
