"""Bound intermediate polling reservations to the current observation."""

import asyncio
from typing import Awaitable, TypeVar

from models.response_limits import ResponseOwner, response_owner

T = TypeVar("T")


class ResponsePollLifetime:
    """Replace intermediate results; deliver only the last observed response."""

    def __init__(self) -> None:
        self._consumer = response_owner.get()
        self._current = ResponseOwner()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None and self._consumer is not None:
            self._consumer.adopt(self._current)
        else:
            self._current.release()

    def discard(self) -> None:
        """Release an observation replaced with an uncharged local diagnostic."""
        self._current.release()
        self._current = ResponseOwner()

    async def fetch(self, operation: Awaitable[T], timeout: float | None) -> T:
        incoming = ResponseOwner()
        token = response_owner.set(incoming)
        try:
            result = await asyncio.wait_for(operation, timeout=timeout)
        except BaseException:
            incoming.release()
            raise
        else:
            # A failed/expired next fetch preserves the last valid response for
            # timeout delivery. Replace its owner only after receiving a result.
            self._current.release()
            self._current = incoming
            return result
        finally:
            response_owner.reset(token)
