"""Final tool admission stays responsive while retaining actual worker owners."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import anyio
import pytest
from fastmcp.tools.base import ToolResult
from mcp.server.runner import _dump_result
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, PrivateAttr, model_serializer

from models.response_limits import ResponseOwner, response_owner
from services.tools.response_work import needs_response_worker
from tests.timing import watchdog_timeout
from transport.response_limit_middleware import ResponseLimitMiddleware


def result_for(kind, large):
    text = "x" * (100_000 if large else 10)
    if kind == "raw":
        return ToolResult.from_mcp_result(CallToolResult(content=[TextContent(text=text)]))
    if kind == "structured":
        return ToolResult(content=[], structured_content={"rows": [text]})
    return ToolResult(content=[TextContent(text=text)])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "structured", "raw"])
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("rejected", [False, True])
async def test_admission_uses_worker_only_for_large_graphs(monkeypatch, kind, large, rejected):
    source = result_for(kind, large)
    if rejected:
        source.meta = {"invalid": float("nan")}
        if kind == "raw":
            source._raw_mcp_result.meta = source.meta
    loop_thread = threading.get_ident()
    original = ResponseLimitMiddleware.admit_tool_result
    guard = ResponseLimitMiddleware()
    expected = original(guard, source)
    seen = []

    def admit(self, result):
        seen.append(threading.get_ident())
        return original(self, result)

    monkeypatch.setattr(ResponseLimitMiddleware, "admit_tool_result", admit)

    async def call_next(_context):
        return source

    result = await guard.on_call_tool(None, call_next)
    assert result.is_error is rejected
    if result._raw_mcp_result is not None:
        assert _dump_result(result.to_mcp_result()) == _dump_result(expected.to_mcp_result())
    else:
        assert result.to_mcp_result() == expected.to_mcp_result()
    assert len(seen) == 1
    assert (seen[0] != loop_thread) is large


def test_scheduling_probe_bounds_cycles_and_does_not_serialize_models():
    calls = []

    class HookModel(BaseModel):
        value: str
        _hidden: str = PrivateAttr(default="x" * 100_000)

        @model_serializer
        def serialize(self):
            calls.append("serialized")
            raise AssertionError("scheduling must not serialize")

    assert needs_response_worker(HookModel(value="small"))
    cycle = []
    cycle.append(cycle)
    assert needs_response_worker(cycle)
    assert needs_response_worker(("x" * 100_000,))
    assert calls == []


def test_scheduling_probe_does_not_call_model_or_container_extension_hooks():
    class HookModel(BaseModel):
        value: str

        def __getattribute__(self, name):
            if name in {"__dict__", "__pydantic_extra__", "__pydantic_private__"}:
                raise AssertionError("probe must inspect native model storage")
            return super().__getattribute__(name)

    class HookList(list):
        def __iter__(self):
            raise AssertionError("probe must not invoke subclass iterators")

    assert needs_response_worker(HookModel(value="x" * 100_000))
    assert needs_response_worker(HookList([1]))


def test_scheduling_probe_uses_actual_type_without_attribute_or_equality_hooks():
    class HookMeta(type):
        @property
        def __mro__(cls):
            raise AssertionError("probe must bypass metaclass descriptors")

        def __getattribute__(cls, name):
            if name == "__mro__":
                raise AssertionError("probe must bypass metaclass attribute hooks")
            return super().__getattribute__(name)

        def __eq__(cls, other):
            raise AssertionError("probe must compare type ancestry by identity")

    class HookList(list, metaclass=HookMeta):
        def __getattribute__(self, name):
            if name == "__class__":
                raise AssertionError("probe must not read instance class attributes")
            return super().__getattribute__(name)

    assert needs_response_worker(HookList([1]))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["asyncio", "anyio"])
async def test_cancelled_admission_holds_response_reservations_until_worker_exit(monkeypatch, mode):
    entered, release = threading.Event(), threading.Event()
    guard = ResponseLimitMiddleware()
    owners = []
    retained = {"response": 123}
    source = result_for("structured", True)
    scopes = []
    loop_thread = threading.get_ident()

    def admit(_result):
        # Baseline fails deterministically before it could block the loop.
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(watchdog_timeout())
        assert retained == {"response": 123}
        return source

    monkeypatch.setattr(guard, "admit_tool_result", admit)

    async def call_next(_context):
        return source

    async def dispatch(context):
        owner = response_owner.get()
        owners.append(owner)
        owner.entries.append((retained, "response"))
        return await guard.on_call_tool(context, call_next)

    async def consume():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await guard.on_message(SimpleNamespace(fastmcp_context=None), dispatch)

    task = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while not entered.is_set() and not task.done():
                await asyncio.sleep(0)
        assert entered.is_set()
        if mode == "asyncio":
            task.cancel()
            task.cancel()
        else:
            scopes[0].cancel()
        for _ in range(10):
            await asyncio.sleep(0)
        assert not task.done()
        assert retained == {"response": 123}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert retained == {}
    assert len(owners) == 1 and owners[0].released


@pytest.mark.asyncio
async def test_cancelled_admission_in_executor_queue_never_uses_released_owner(monkeypatch):
    from services.tools import response_work as module

    gate = threading.Event()
    loop = asyncio.get_running_loop()
    submitted = asyncio.Event()
    original = module.submit_owned_executor
    invoked = []
    owner = ResponseOwner()

    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = loop.run_in_executor(executor, gate.wait, watchdog_timeout())

        def submit(owner_loop, _executor, operation):
            future = original(owner_loop, executor, operation)
            submitted.set()
            return future

        monkeypatch.setattr(module, "submit_owned_executor", submit)
        guard = ResponseLimitMiddleware()
        monkeypatch.setattr(guard, "admit_tool_result", lambda result: invoked.append(result))

        async def call_next(_context):
            return result_for("raw", True)

        async def consume():
            try:
                await guard.on_call_tool(None, call_next)
            finally:
                owner.release()

        task = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(watchdog_timeout()):
                while not submitted.is_set() and not task.done():
                    await asyncio.sleep(0)
            assert submitted.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert owner.released
        finally:
            gate.set()
            await blocker
            await asyncio.gather(task, return_exceptions=True)
    assert invoked == []
