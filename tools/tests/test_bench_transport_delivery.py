"""Benchmark delivery recovery must validate bytes, bounded pages and actual cleanup."""

import base64
import hashlib
import json

import pytest
import anyio
from mcp.types import CallToolResult, TextContent

from tools.bench_transport import validate_output, validate_large_output
from tools.bench_transport_delivery import PAGE_BYTES, DeliveryReads, Handle


class Pages:
    def __init__(self, fault=""):
        self.fault = fault
        self.value = {"success": True, "data": {"lines": [{"message": "x" * 700_000}]}}
        self.original = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(self.value))],
            structuredContent=self.value,
        )
        self.raw = self.original.model_dump_json(by_alias=True, exclude_none=True).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.calls = []
        self.handle = {
            "state": "retained",
            "response_id": "a" * 32,
            "total_bytes": len(self.raw),
            "sha256": self.digest,
            "page_bytes": PAGE_BYTES,
            "encoding": "base64",
            "json_encoding": "utf-8",
        }

    async def call_tool(self, name, arguments, **_options):
        self.calls.append((name, arguments))
        if arguments.get("release"):
            result = {"success": True, "data": {"state": "released", "response_id": "a" * 32}}
        else:
            offset = arguments["offset"]
            end = min(offset + PAGE_BYTES, len(self.raw))
            chunk = self.raw[offset:end]
            if self.fault == "checksum":
                chunk = b"!" + chunk[1:]
            if self.fault == "page_size":
                chunk = chunk[:-1]
            result = {
                "success": True,
                "data": {
                    "state": "page",
                    "response_id": "a" * 32,
                    "offset": offset,
                    "next_offset": (end if end < len(self.raw) else None),
                    "total_bytes": len(self.raw),
                    "sha256": self.digest,
                    "data_base64": base64.b64encode(chunk).decode(),
                    "encoding": "base64",
                    "json_encoding": "utf-8",
                },
            }
            if self.fault == "offset":
                result["data"]["next_offset"] = offset
        return CallToolResult(content=[], structuredContent=result)


def summary(handle):
    value = {"success": True, "delivery": handle}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value))], structuredContent=value
    )


@pytest.mark.asyncio
async def test_paged_result_restores_public_payload_and_counts_every_rpc():
    peer = Pages()
    counts = {"tools/call": 1}
    reader = DeliveryReads(peer, counts)
    async with reader.batch():
        recovered = await reader.recover(summary(peer.handle))
        assert not any(arguments.get("release") for _, arguments in peer.calls)
    assert validate_output(recovered.result) == peer.value
    validate_large_output(peer.value, 700_000)
    expected_pages = (len(peer.raw) + PAGE_BYTES - 1) // PAGE_BYTES
    assert recovered.delivery.page_calls == expected_pages
    assert reader.release_calls == 1
    assert recovered.delivery.artifact_bytes == len(peer.raw)
    assert counts == {"tools/call": expected_pages + 2, "delivery_tools/call": expected_pages + 1}
    assert peer.calls[-1] == ("read_response", {"response_id": "a" * 32, "release": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["checksum", "offset", "page_size"])
async def test_corrupt_pages_fail_measurement_and_still_release_owned_artifact(fault):
    peer = Pages(fault)
    reader = DeliveryReads(peer, {"tools/call": 1})
    with pytest.raises(ValueError, match="checksum|offset|length"):
        async with reader.batch():
            await reader.recover(summary(peer.handle))
    assert peer.calls[-1][1].get("release") is True


def test_delivery_summary_cannot_be_fingerprinted_as_successful_workload():
    with pytest.raises(RuntimeError, match="summary"):
        validate_output(summary({"state": "unavailable"}))
    with pytest.raises(RuntimeError, match="requested payload"):
        validate_large_output({"success": True, "data": {"lines": [{"message": "short"}]}}, 4096)


@pytest.mark.asyncio
async def test_unavailable_delivery_fails_without_reexecuting_original_operation():
    peer = Pages()
    reader = DeliveryReads(peer, {"tools/call": 1})
    with pytest.raises(ValueError):
        async with reader.batch():
            await reader.recover(summary({"state": "unavailable"}))
    assert peer.calls == []


@pytest.mark.asyncio
async def test_identical_concurrent_handles_release_only_after_both_readers_settle():
    peer = Pages()
    reader = DeliveryReads(peer, {"tools/call": 2})
    recovered = []

    async def read():
        recovered.append(await reader.recover(summary(peer.handle)))

    async with reader.batch(), anyio.create_task_group() as group:
        group.start_soon(read)
        group.start_soon(read)
    assert len(recovered) == 2
    assert sum(arguments.get("release") is True for _, arguments in peer.calls) == 1
    assert peer.calls[-1][1].get("release") is True


@pytest.mark.asyncio
async def test_cleanup_attempts_other_owned_handles_after_one_release_fails():
    peer = Pages()
    calls = []

    class FailedRelease:
        async def call_tool(self, _name, arguments, **_options):
            response_id = arguments["response_id"]
            calls.append(response_id)
            if response_id == "b" * 32:
                raise ValueError("first release failed")
            return CallToolResult(
                content=[],
                structuredContent={
                    "success": True,
                    "data": {"state": "released", "response_id": response_id},
                },
            )

    reader = DeliveryReads(FailedRelease(), {"tools/call": 0})
    with pytest.raises(ValueError, match="first release failed"):
        async with reader.batch():
            for name in ("a", "b"):
                handle = Handle.model_validate({**peer.handle, "response_id": name * 32})
                reader.pending[handle.response_id] = handle
    assert calls == ["b" * 32, "a" * 32]
    assert reader.pending == {}


@pytest.mark.asyncio
async def test_cancelled_page_reader_still_releases_its_owned_handle():
    class HeldPage(Pages):
        async def call_tool(self, name, arguments, **options):
            if not arguments.get("release"):
                await anyio.sleep_forever()
            return await super().call_tool(name, arguments, **options)

    peer = HeldPage()
    reader = DeliveryReads(peer, {"tools/call": 1})
    with anyio.CancelScope() as scope:
        async with reader.batch():
            scope.cancel()
            await reader.recover(summary(peer.handle))
    assert reader.release_calls == 1
    assert peer.calls[-1][1].get("release") is True
