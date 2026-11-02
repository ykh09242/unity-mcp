"""Recover actual paged MCP output before benchmark payload/timing validation."""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import Literal

import anyio
from mcp import ClientSession
from mcp.types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field

PAGE_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class Handle(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    state: Literal["retained"]
    response_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    total_bytes: int = Field(ge=1, le=MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_bytes: Literal[PAGE_BYTES]
    encoding: Literal["base64"]
    json_encoding: Literal["utf-8"]


class Page(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    state: Literal["page"]
    response_id: str
    offset: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    next_offset: int | None = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    total_bytes: int = Field(ge=1, le=MAX_ARTIFACT_BYTES)
    sha256: str
    data_base64: str = Field(max_length=4 * ((PAGE_BYTES + 2) // 3))
    encoding: Literal["base64"]
    json_encoding: Literal["utf-8"]


class PageResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    success: Literal[True]
    data: Page


class ReleasedData(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    state: Literal["released"]
    response_id: str


class ReleasedResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    success: Literal[True]
    data: ReleasedData


@dataclass(frozen=True, slots=True)
class DeliveryMeasurement:
    page_calls: int = 0
    artifact_bytes: int = 0
    elapsed_ms: float = 0


@dataclass(frozen=True, slots=True)
class RecoveredResponse:
    result: CallToolResult
    delivery: DeliveryMeasurement = DeliveryMeasurement()


class DeliveryReads:
    """Own a serial benchmark cohort's handles through all concurrent readers.

    Identical product results can share a handle. Cleanup belongs to the batch
    boundary after its task group settles, never an individual reader.
    """

    def __init__(self, session: ClientSession, counts: dict[str, int]):
        self.session, self.counts = session, counts
        self.pending: dict[str, Handle] = {}
        self.release_calls = 0
        self.cleanup_ms = 0.0

    @asynccontextmanager
    async def batch(self):
        try:
            yield self
        finally:
            if self.pending:
                started = time.perf_counter()
                try:
                    with anyio.CancelScope(shield=True), anyio.fail_after(5):
                        failure: Exception | None = None
                        while self.pending:
                            _, handle = self.pending.popitem()
                            self.counts["tools/call"] += 1
                            self.counts["delivery_tools/call"] = (
                                self.counts.get("delivery_tools/call", 0) + 1
                            )
                            self.release_calls += 1
                            try:
                                released = await self.session.call_tool(
                                    "read_response",
                                    {"response_id": handle.response_id, "release": True},
                                    read_timeout_seconds=5,
                                )
                                if (
                                    released.is_error
                                    or ReleasedResult.model_validate(
                                        released.structured_content
                                    ).data.response_id
                                    != handle.response_id
                                ):
                                    raise ValueError("Retained response release failed")
                            except Exception as error:
                                # Cleanup is an ownership boundary: preserve the
                                # first failure but attempt all remaining owners
                                # within the same fixed total cleanup budget.
                                if failure is None:
                                    failure = error
                        if failure is not None:
                            raise failure
                finally:
                    self.cleanup_ms += (time.perf_counter() - started) * 1000

    async def recover(self, response: CallToolResult) -> RecoveredResponse:
        """Verify complete pages; batch cleanup also covers failed/cancelled readers."""
        structured = response.structured_content
        if isinstance(structured, dict) and set(structured) == {"result"}:
            structured = structured["result"]
        if not isinstance(structured, dict) or "delivery" not in structured:
            return RecoveredResponse(response)
        handle = Handle.model_validate(structured["delivery"])
        self.pending[handle.response_id] = handle
        started = time.perf_counter()
        offset = page_calls = 0
        payload = bytearray()
        while offset < handle.total_bytes:
            self.counts["tools/call"] += 1
            self.counts["delivery_tools/call"] = self.counts.get("delivery_tools/call", 0) + 1
            page_calls += 1
            reply = await self.session.call_tool(
                "read_response",
                {"response_id": handle.response_id, "offset": offset},
                read_timeout_seconds=10,
            )
            if reply.is_error:
                raise ValueError("Retained response page failed")
            page = PageResult.model_validate(reply.structured_content).data
            end = min(offset + PAGE_BYTES, handle.total_bytes)
            if (
                page.response_id != handle.response_id
                or page.offset != offset
                or page.next_offset != (end if end < handle.total_bytes else None)
                or page.total_bytes != handle.total_bytes
                or page.sha256 != handle.sha256
            ):
                raise ValueError("Retained response page identity or offset changed")
            decoded = base64.b64decode(page.data_base64, validate=True)
            if len(decoded) != end - offset:
                raise ValueError("Retained response page length changed")
            payload.extend(decoded)
            offset = end
        if hashlib.sha256(payload).hexdigest() != handle.sha256:
            raise ValueError("Retained response checksum mismatch")
        result = CallToolResult.model_validate_json(payload)
        if result.is_error:
            raise ValueError("Recovered public workload failed")
        return RecoveredResponse(
            result,
            DeliveryMeasurement(page_calls, len(payload), (time.perf_counter() - started) * 1000),
        )
