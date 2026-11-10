"""Admit MCP stdin graph decoding independently of native command quotas."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import Enum
from sys import getsizeof
from threading import Lock
from typing import Any, Final

from models.response_limits import bounded_json_profile
from services.tools.response_work import response_work


MAX_STDIO_INPUT_DEPTH: Final = 72
MAX_STDIO_INPUT_NODES: Final = 1_000_128
MAX_STDIO_DECODE_BYTES: Final = 512 * 1024 * 1024
MAX_STDIO_DECODE_TOTAL_BYTES: Final = 1024 * 1024 * 1024


class InputFailure(Enum):
    INVALID = "Invalid MCP stdio message"
    LIMIT = "MCP stdio message exceeds decode limits"
    BUSY = "MCP stdio decode capacity is exhausted"

    def exception(self) -> ValueError:
        # Construct outside the decoder's except block. Never forward a validation
        # exception: its fields and traceback can retain the entire rejected graph.
        return ValueError(self.value)


class StdioInputAdmission:
    """Own parser work and queued messages until actual decode and stream handoff."""

    def __init__(
        self,
        *,
        max_request: int = MAX_STDIO_DECODE_BYTES,
        max_total: int = MAX_STDIO_DECODE_TOTAL_BYTES,
        max_depth: int = MAX_STDIO_INPUT_DEPTH,
        max_nodes: int = MAX_STDIO_INPUT_NODES,
    ):
        self.max_request, self.max_total = max_request, max_total
        self.max_depth, self.max_nodes = max_depth, max_nodes
        self.retained_bytes = 0
        self._lock = Lock()

    @asynccontextmanager
    async def parse(
        self, line: str, validate: Callable[..., Any]
    ) -> AsyncIterator[Any | ValueError]:
        reserved = 0

        def reserve(total: int) -> InputFailure | None:
            nonlocal reserved
            if total > self.max_request:
                return InputFailure.LIMIT
            with self._lock:
                change = total - reserved
                if change > self.max_total - self.retained_bytes:
                    return InputFailure.BUSY
                self.retained_bytes += change
                reserved = total
            return None

        def materialize():
            try:
                profile = bounded_json_profile(
                    line,
                    # Raw bytes (excluding line endings) were checked by the input
                    # reader. Replacement decoding can expand malformed UTF-8, and
                    # a legal delimiter must not lower that existing byte ceiling.
                    max_bytes=4 * len(line),
                    max_depth=self.max_depth,
                    max_nodes=self.max_nodes,
                    profile_string_widening=True,
                )
                if profile is None:
                    return InputFailure.LIMIT
                if not profile.complete_strings:
                    return InputFailure.INVALID
                # Charge the text, SDK UTF-8 conversion, decoded strings, and an
                # estimate for containers/validation copies before SDK allocation.
                encoded = len(line) if line.isascii() else 4 * len(line)
                graph = getsizeof(line) + encoded + 4 * len(line) + 512 * profile.nodes + 4096
                failure = reserve(graph + 12 * profile.max_widening_string)
                if failure is not None:
                    return failure
                message = validate(line, by_name=False)
                reserve(graph)
                return message
            except Exception:
                return InputFailure.INVALID

        result = None

        def decode():
            nonlocal result
            # Return only None to the executor. A cancellation traceback can keep
            # its future alive; clearing this holder must also discard its graph.
            result = materialize()

        try:
            # The scanner's Unicode byte count uses at most one 64-Ki-character
            # slice and its encoding. Admit queued text before waiting for a worker.
            failure = reserve(getsizeof(line) + 8 * min(len(line), 65_536) + 4096)
            if failure is not None:
                result = failure
            else:
                await response_work(decode, large=True)
            if isinstance(result, InputFailure):
                line = ""
                reserve(0)
                result = result.exception()
            yield result
        finally:
            # response_work drains a started worker under repeated cancellation;
            # queued workers discard the callable before they can materialize it.
            result = None
            line = ""
            with self._lock:
                self.retained_bytes -= reserved


stdio_input_admission = StdioInputAdmission()
