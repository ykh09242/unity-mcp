"""Owned phase observations and finite progress for hermetic test fixtures."""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from tests.timing import await_completion


async def await_condition(
    predicate: Callable[[], bool], phase: str, *, producers: Iterable[asyncio.Future] = ()
) -> None:
    """Observe a test predicate without hiding early producer failures."""

    async def observe():
        while not predicate():
            await asyncio.sleep(0.001)

    observer = asyncio.create_task(observe())
    try:
        await await_completion(observer, phase, producers=producers)
    finally:
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)


@dataclass
class FixtureProgress:
    """Count fixture work and reject replayed continuations during forward traversal.

    Callers derive the ceiling from their known fixture rows, with room for
    work-only continuation pages and setup/cleanup phases. These are test
    progress assertions, never product limits or elapsed-time requirements.
    """

    maximum: int
    phase: str
    steps: int = field(default=0, init=False)
    seen: set[str | int] = field(default_factory=set, init=False)

    def step(self, continuation: str | int | None = None) -> None:
        self.steps += 1
        assert self.steps <= self.maximum, f"{self.phase} exceeded {self.maximum} steps"
        if continuation is not None:
            assert continuation not in self.seen, f"{self.phase} repeated continuation"
            self.seen.add(continuation)
