"""Budgets for caller-controlled patterns used by script search and editing."""

import asyncio
from itertools import islice
import re
from threading import BoundedSemaphore, Event
from time import monotonic
from typing import Callable, TypeVar

import regex

MAX_PATTERN_CHARS = 2048
MAX_TEXT_CHARS = 2_000_000
MAX_MATCHES = 10_000
TIMEOUT_SECONDS = 0.1
TOTAL_TIMEOUT_SECONDS = 1.0
MAX_WORK_CHARS = 16_000_000

_REGEX_WORKERS = BoundedSemaphore(2)
_RegexResult = TypeVar("_RegexResult")


class WorkBudget:
    """Mutable per-request accounting shared by matching and postprocessing."""

    def __init__(self) -> None:
        self.deadline = monotonic() + TOTAL_TIMEOUT_SECONDS
        self.remaining = MAX_WORK_CHARS
        self.cancelled = Event()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise TimeoutError("Regex work cancelled")
        if monotonic() >= self.deadline:
            raise TimeoutError("Regex total work timed out")

    def consume(self, amount: int) -> None:
        self.check()
        self.remaining -= amount
        if self.remaining < 0:
            raise ValueError("Regex total work exceeds the character budget")

    def timeout(self) -> float:
        self.check()
        return min(TIMEOUT_SECONDS, max(0.000001, self.deadline - monotonic()))


def _compile(pattern: str, text: str, flags: int):
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"Regex pattern exceeds {MAX_PATTERN_CHARS} characters")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Regex input exceeds {MAX_TEXT_CHARS} characters")
    repetitions = re.findall(r"\{(\d*)(?:,(\d*))?\}", pattern)
    if any(int(value) > MAX_TEXT_CHARS for bounds in repetitions for value in bounds if value):
        raise ValueError("Regex repetition exceeds the input size limit")
    return regex.compile(pattern, flags | regex.VERSION0)


def find_matches(
    pattern: str,
    text: str,
    flags: int = 0,
    limit: int = MAX_MATCHES,
    *,
    budget: WorkBudget | None = None,
):
    budget = budget or WorkBudget()
    budget.consume(len(text) + len(pattern))
    compiled = _compile(pattern, text, flags)
    matches = list(islice(compiled.finditer(text, timeout=budget.timeout()), limit + 1))
    budget.consume(len(matches))
    if len(matches) > limit:
        raise ValueError(f"Regex exceeds the {limit} match limit; use a narrower pattern")
    return matches


def search(pattern: str, text: str, flags: int = 0, *, budget: WorkBudget | None = None):
    budget = budget or WorkBudget()
    budget.consume(len(text) + len(pattern))
    match = _compile(pattern, text, flags).search(text, timeout=budget.timeout())
    budget.check()
    return match


def substitute(
    pattern: str,
    replacement: str,
    text: str,
    count: int,
    flags: int = 0,
    *,
    budget: WorkBudget | None = None,
):
    budget = budget or WorkBudget()
    budget.consume(len(text) + len(pattern) + len(replacement))
    if count < 0:
        raise ValueError("Regex replacement count must be nonnegative")
    if len(replacement) + replacement.count("\\") * len(text) > MAX_TEXT_CHARS:
        raise ValueError("Regex replacement expansion exceeds the output size limit")
    compiled = _compile(pattern, text, flags)
    # Bound replacement expansion before allocating a potentially enormous result.
    pieces = []
    cursor = 0
    length = 0
    for i, match in enumerate(compiled.finditer(text, timeout=budget.timeout())):
        budget.check()
        if count and i >= count:
            break
        if i >= MAX_MATCHES:
            raise ValueError("Regex replacement exceeds the match limit")
        part = text[cursor : match.start()] + match.expand(replacement)
        length += len(part)
        budget.consume(len(part))
        if length > MAX_TEXT_CHARS:
            raise ValueError("Regex replacement exceeds the output size limit")
        pieces.append(part)
        cursor = match.end()
    length += len(text) - cursor
    if length > MAX_TEXT_CHARS:
        raise ValueError("Regex replacement exceeds the output size limit")
    pieces.append(text[cursor:])
    budget.consume(len(text) - cursor + length)
    result = "".join(pieces)
    budget.check()
    return result


async def run_work(
    budget: WorkBudget,
    work: Callable[[], _RegexResult],
    *,
    workers: BoundedSemaphore | None = None,
) -> _RegexResult:
    """Keep queued/running script regex work bounded until the worker exits."""
    budget.check()
    workers = _REGEX_WORKERS if workers is None else workers
    if not workers.acquire(blocking=False):
        raise ValueError("Script regex workers are busy; retry later")

    def run() -> _RegexResult:
        try:
            budget.check()
            return work()
        finally:
            workers.release()

    # Submit before awaiting so even a queued cancelled request eventually
    # releases admission in the worker.
    try:
        future = asyncio.get_running_loop().run_in_executor(None, run)
    except BaseException:
        workers.release()
        raise
    try:
        # Waiting must not cancel the admitted worker. Unlike shield on Python
        # 3.14, wait leaves late exception reporting to our cancellation observer.
        await asyncio.wait({future})
        return future.result()
    except asyncio.CancelledError:
        budget.cancelled.set()
        future.add_done_callback(
            lambda finished: finished.exception() if not finished.cancelled() else None
        )
        raise
