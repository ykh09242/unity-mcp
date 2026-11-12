"""Client assembly verifies identity, byte limits and digest before presentation."""

import base64
import json
from unittest.mock import AsyncMock

import pytest

from services.response_delivery import ResponseAuthority, ResponseScope, ResponseStore
from utils.retained_response import assemble_response


def fixture_payload():
    value = {"summary": "saved", "details": "x" * 300_000}
    store = ResponseStore()
    authority = ResponseAuthority(ResponseScope("a", "p", "s"), frozenset())
    handle = store.put(json.dumps(value).encode(), authority)

    async def call(name, options):
        assert name == "read_response"
        return {
            "success": True,
            "data": store.read(options["response_id"], authority, offset=options["offset"]),
        }

    return value, handle, AsyncMock(side_effect=call)


@pytest.mark.asyncio
async def test_assembly_checks_full_digest_before_field_selection():
    _, handle, call = fixture_payload()
    assert await assemble_response(call, handle, fields=["summary"]) == {"summary": "saved"}
    assert call.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("limits", [{"max_bytes": 100}, {"max_pages": 1}, {"max_bytes": True}])
async def test_assembly_admits_limits_before_any_read(limits):
    _, handle, call = fixture_payload()
    with pytest.raises(ValueError):
        await assemble_response(call, handle, **limits)
    call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["digest", "identity", "offset", "oversized", "unavailable"])
async def test_assembly_rejects_corrupt_or_unavailable_pages_without_replay(failure):
    _, handle, call = fixture_payload()
    original = call.side_effect

    async def damaged(name, options):
        result = await original(name, options)
        page = result["data"]
        if failure == "digest":
            raw = bytearray(base64.b64decode(page["data_base64"]))
            raw[0] = ord(" ")
            page["data_base64"] = base64.b64encode(raw).decode()
        elif failure == "identity":
            page["sha256"] = "0" * 64
        elif failure == "offset":
            page["next_offset"] = 0
        elif failure == "oversized":
            page["data_base64"] += "AAAA"
        else:
            result = {"success": False, "error": "response_not_found"}
        return result

    call.side_effect = damaged
    with pytest.raises(ValueError):
        await assemble_response(call, handle)
    assert all(item.args[0] == "read_response" for item in call.await_args_list)
