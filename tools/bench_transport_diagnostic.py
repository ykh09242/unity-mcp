"""Opt-in bounded observation; delegates unchanged and never labels await time CPU."""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Awaitable, Callable, Iterator, ParamSpec, TypeVar, get_type_hints

from pydantic import JsonValue

P = ParamSpec('P')
T = TypeVar('T')
MAX_EVENTS = 8192
MAX_SIDECAR_BYTES = 4 * 1024 * 1024


class DiagnosticError(RuntimeError):
    """The diagnostic seam or bounded evidence is unsupported/incomplete."""


def require_sdk() -> None:
    """Only the source-reviewed installed SDK versions are supported."""
    for package, expected in (('mcp', '2.3.0'), ('fastmcp', '4.0.11')):
        if importlib.metadata.version(package) != expected:
            raise DiagnosticError(f'Unsupported diagnostic SDK: {package}; expected {expected}')


def write_sidecar(path: Path, value: dict[str, JsonValue], byte_limit: int = MAX_SIDECAR_BYTES) -> bool:
    """Bound encoded storage as well as events; preserve an explicit failure stub."""
    if not 1024 <= byte_limit <= MAX_SIDECAR_BYTES:
        raise DiagnosticError('Diagnostic byte capacity must be 1KiB..4MiB')
    data = bytearray()
    for chunk in json.JSONEncoder(indent=2).iterencode(value):
        encoded = chunk.encode('utf-8')
        if len(data) + len(encoded) + 1 > byte_limit:
            path.write_text(json.dumps({'data_kind': 'diagnostic', 'complete': False,
                'error': 'diagnostic_byte_capacity', 'byte_limit': byte_limit}) + '\n', encoding='utf-8')
            return False
        data.extend(encoded)
    data.extend(b'\n')
    path.write_bytes(data)
    return True


class Trace:
    """Bounded, mutable recorder shared by owned event-loop and writer threads."""
    def __init__(self, max_events: int = MAX_EVENTS) -> None:
        if not 1 <= max_events <= MAX_EVENTS:
            raise DiagnosticError('Diagnostic event capacity must be 1..8192')
        self.max_events = max_events
        self.events: list[dict[str, JsonValue]] = []
        self.seams: list[dict[str, JsonValue]] = []
        self.dropped = 0
        self.aliases: dict[str, str] = {}
        self.batch = ''
        self.lock = threading.Lock()
        self.correlation: ContextVar[str] = ContextVar('diagnostic_correlation', default='')

    @contextmanager
    def correlated(self, correlation: str) -> Iterator[None]:
        """Propagate an owned label without changing any wire fields."""
        if len(correlation) > 128:
            raise DiagnosticError('Diagnostic correlation exceeds 128 characters')
        token = self.correlation.set(correlation)
        try:
            yield
        finally:
            self.correlation.reset(token)

    @contextmanager
    def span(self, name: str, correlation: str | None = None, *, synchronous: bool = False) -> Iterator[None]:
        """Observe one local duration; CPU only surrounds synchronous execution."""
        label = (self.correlation.get() or ('batch:' + self.batch if self.batch else '')) if correlation is None else correlation
        if len(name) > 64 or len(label) > 128:
            raise DiagnosticError('Diagnostic label exceeds its bound')
        begin = time.perf_counter_ns()
        cpu = time.thread_time_ns() if synchronous else 0
        completed = False
        try:
            yield
            completed = True
        finally:
            event: dict[str, JsonValue] = {'name': name, 'correlation': label,
                'wall_ms': (time.perf_counter_ns() - begin) / 1e6, 'completed': completed}
            if synchronous:
                event['thread_cpu_ms'] = (time.thread_time_ns() - cpu) / 1e6
            # Never mask the delegated failure/cancellation with recorder overflow.
            with self.lock:
                if len(self.events) < self.max_events:
                    self.events.append(event)
                else:
                    self.dropped += 1

    def export(self) -> dict[str, JsonValue]:
        """Copy bounded evidence after the workload; never retain result payloads."""
        with self.lock:
            return {'data_kind': 'diagnostic', 'complete': self.dropped == 0,
                    'max_events': self.max_events, 'dropped_events': self.dropped,
                    'events': list(self.events), 'seams': list(self.seams),
                    'cpu_clock_limit': 'Whole-process/current-thread clocks may be quantized; zero samples do not prove zero CPU work.'}

    def check(self) -> None:
        """Reject overflow only after owned cleanup and saving bounded evidence."""
        if self.dropped:
            raise DiagnosticError(f'Diagnostic event overflow: {self.dropped} dropped')

    def alias(self, identity: str, correlation: str) -> None:
        """Join owned command IDs out of band with bounded retained labels."""
        if len(identity) > 128 or len(correlation) > 128:
            raise DiagnosticError('Diagnostic alias exceeds its bound')
        if len(self.aliases) >= 1024 and identity not in self.aliases:
            self.dropped += 1
        else:
            self.aliases[identity] = correlation


@contextmanager
def patch(owner: T, attribute: str, parameters: tuple[str, ...], trace: Trace,
          name: str, *, synchronous: bool = False,
          correlation: Callable[..., str] | None = None) -> Iterator[None]:
    """Patch one verified seam, restoring both its value and descriptor topology."""
    original = getattr(owner, attribute)
    if getattr(original, '_bench_diagnostic', False):
        raise DiagnosticError(f'Diagnostic wrapper stack forbidden: {name}')
    if (not callable(original) or tuple(inspect.signature(original).parameters) != parameters
            or inspect.iscoroutinefunction(original) == synchronous):
        raise DiagnosticError(f'Unsupported diagnostic signature: {name}')
    source = inspect.getsourcefile(original)
    if source is None:
        raise DiagnosticError(f'Diagnostic seam has no source: {name}')
    if len(trace.seams) >= 16:
        raise DiagnosticError('Diagnostic seam capacity exceeded')
    trace.seams.append({'name': name, 'parameters': list(parameters),
                        'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                        'source': str(Path(source).resolve())})

    @wraps(original)
    def sync_wrapper(*args, **kwargs):
        label = correlation(*args, **kwargs) if correlation is not None else None
        with trace.span(name, label, synchronous=True):
            return original(*args, **kwargs)

    @wraps(original)
    async def async_wrapper(*args, **kwargs):
        label = correlation(*args, **kwargs) if correlation is not None else None
        with trace.span(name, label):
            return await original(*args, **kwargs)

    wrapper = sync_wrapper if synchronous else async_wrapper
    wrapper._bench_diagnostic = True
    namespace = vars(owner)
    owned = attribute in namespace
    previous = namespace.get(attribute)
    setattr(owner, attribute, wrapper)
    try:
        yield
    finally:
        if owned:
            setattr(owner, attribute, previous)
        else:
            delattr(owner, attribute)


def traced_tool(trace: Trace | None, argument: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """OFF returns the original callable; ON observes the actual fixture tool."""
    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if trace is None:
            return function
        signature = inspect.signature(function)
        hints = get_type_hints(function, include_extras=True)

        @wraps(function)
        async def call(*args: P.args, **kwargs: P.kwargs) -> T:
            correlation = signature.bind(*args, **kwargs).arguments[argument]
            if not isinstance(correlation, str):
                raise DiagnosticError('Owned tool correlation is not text')
            with trace.correlated(correlation), trace.span('fixture_tool'):
                return await function(*args, **kwargs)
        # The wrapper lives in this helper's globals. Resolve the fixture's
        # postponed Context annotations before FastMCP introspects the wrapper.
        call.__annotations__ = hints
        call.__signature__ = signature.replace(
            parameters=[parameter.replace(annotation=hints.get(parameter.name, parameter.annotation))
                        for parameter in signature.parameters.values()],
            return_annotation=hints.get('return', signature.return_annotation))
        return call
    return decorate
