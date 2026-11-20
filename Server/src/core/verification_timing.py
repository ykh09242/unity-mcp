"""Run-owned, bounded scalar observations on the clocks that execute the work."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic

_active = ContextVar("verification_timing", default=None)
MAX_COMMANDS = 96


class VerificationTiming:
    def __init__(self, entry="post_edit"):
        self.entry = entry
        self._lock = Lock()
        self._closed = False
        self._ms = {}
        self._commands = []
        self._omitted = 0
        self._player = {}

    def record(self, name, elapsed_ms, player=None):
        with self._lock:
            if self._closed:
                return
            phases = self._ms if player is None else self._player.setdefault(player, {})
            phases[name] = phases.get(name, 0) + elapsed_ms

    def command(self, name, row):
        with self._lock:
            if self._closed:
                return
            if len(self._commands) == MAX_COMMANDS:
                self._omitted += 1
            else:
                self._commands.append({"command": name, **row})

    def finish(self):
        with self._lock:
            self._closed = True
            return {
                "schema": "verification_timing@1",
                "entry": self.entry,
                "clock": "python_monotonic",
                "client_delivery_ms": None,
                "cross_process_clock_offset_ms": None,
                "phases_ms": self._ms,
                "players_ms": self._player,
                "commands": self._commands,
                "omitted_commands": self._omitted,
            }

    @contextmanager
    def bind(self):
        token = _active.set(self)
        try:
            yield
        finally:
            _active.reset(token)

    @property
    def closed(self):
        return self._closed


def current():
    trace = _active.get()
    return trace if trace is not None and not trace.closed else None


@contextmanager
def interval(name, player=None):
    trace = current()
    if trace is None:
        yield
        return
    started = monotonic()
    try:
        yield
    finally:
        trace.record(name, (monotonic() - started) * 1000, player)
