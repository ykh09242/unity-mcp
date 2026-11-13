"""Public file searches share bounded worker ownership with script edits."""

from tests.timing import watchdog_timeout

import asyncio
import base64
import gc
import importlib
from functools import wraps
import json
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from services.tools import bounded_regex

search = importlib.import_module("services.tools.find_in_file")
selection = importlib.import_module("services.tools.script_apply_edits")


class Source(str):
    """Observe script inputs without an observer owning the producer result."""


class CountingPool(ThreadPoolExecutor):
    def __init__(self, workers):
        super().__init__(max_workers=workers)
        self.submissions = 0

    def submit(self, *args, **kwargs):
        self.submissions += 1
        return super().submit(*args, **kwargs)


async def until(predicate):
    async with asyncio.timeout(watchdog_timeout()):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
def routing(monkeypatch):
    state = SimpleNamespace(source="first\nneedle NEEDLE\nlast", refs=[], reads=0, encoded=False)

    async def target(_ctx):
        return "Synthetic@abc123"

    async def read(_send, instance, command, params):
        assert instance == "Synthetic@abc123" and command == "manage_script"
        assert params == {"action": "read", "name": "Test", "path": "Assets"}
        state.reads += 1
        source = Source(state.source)
        state.refs.append(weakref.ref(source))
        if state.encoded:
            return {
                "success": True,
                "data": {
                    "contentsEncoded": True,
                    "encodedContents": base64.b64encode(source.encode()).decode(),
                },
            }
        return {"success": True, "data": {"contents": source}}

    monkeypatch.setattr(search, "get_unity_instance_from_context", target)
    monkeypatch.setattr(search, "send_with_unity_instance", read)
    return state


@pytest.fixture
def executor(monkeypatch):
    pools = []
    releases = []

    def install(workers):
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor
        pool = CountingPool(workers)
        pools.append(pool)

        def submit(selected, func, *args):
            return original(pool if selected is None else selected, func, *args)

        monkeypatch.setattr(loop, "run_in_executor", submit)
        return pool, original

    yield install, releases
    for release in releases:
        release.set()
    for pool in pools:
        pool.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_cancelled_search_queue_retains_at_most_two_inputs(routing, executor):
    install, releases = executor
    pool, original = install(1)
    started, release = Event(), Event()
    releases.append(release)

    def occupy():
        started.set()
        assert release.wait(watchdog_timeout())

    blocker = pool.submit(occupy)
    routing.source = "x" * 1_000_000 + "\nneedle"
    tasks = []
    try:
        await until(started.is_set)
        tasks = [
            asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
            for _ in range(12)
        ]
        await until(lambda: routing.reads == 12)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        task = None
        await asyncio.sleep(0)
        gc.collect()
        assert pool.submissions == 3, "Only two regex jobs may enter the blocked executor"
        assert sum(ref() is not None for ref in routing.refs) == 2
        # Cancelled queued work still owns admission until its worker actually exits.
        refused = await search.find_in_file(None, "Assets/Test.cs", "needle")
        assert refused["success"] is False and "busy" in refused["message"]
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wrap_future(blocker)
        await original(pool, lambda: None)
        await asyncio.sleep(0)
    gc.collect()
    assert not any(ref() is not None for ref in routing.refs)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True


@pytest.mark.asyncio
async def test_running_search_and_edit_keep_shared_admission_until_exit(
    routing, executor, monkeypatch
):
    install, releases = executor
    pool, original_executor = install(2)
    release = Event()
    releases.append(release)
    entered = [Event(), Event()]
    exited = [Event(), Event()]
    actual = bounded_regex.find_matches
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context.get("message")))

    def blocked(*args, **kwargs):
        index = 0 if args[0] == "needle" else 1
        entered[index].set()
        try:
            assert release.wait(watchdog_timeout())
            return actual(*args, **kwargs)
        finally:
            exited[index].set()

    monkeypatch.setattr(bounded_regex, "find_matches", blocked)
    refused_task = None
    tasks = [
        asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle")),
        asyncio.create_task(
            selection._apply_edits_locally(
                "class C {\n marker;\n}",
                [{"op": "anchor_insert", "anchor": "marker", "text": "new"}],
            )
        ),
    ]
    try:
        await until(lambda: all(event.is_set() for event in entered))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        task = None
        refused_task = asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
        await until(lambda: refused_task.done() or pool.submissions > 2)
        assert refused_task.done(), "Busy searches must reject without entering the executor queue"
        refused = await refused_task
        assert refused["success"] is False and "busy" in refused["message"]
        assert pool.submissions == 2
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if refused_task is not None:
            refused_task.cancel()
            await asyncio.gather(refused_task, return_exceptions=True)
        await until(lambda: all(event.is_set() for event in exited))
        await original_executor(pool, lambda: None)
        await asyncio.sleep(0.01)
        loop.set_exception_handler(previous)
    gc.collect()
    assert not any(ref() is not None for ref in routing.refs)
    assert not errors
    monkeypatch.setattr(bounded_regex, "find_matches", actual)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
    assert await selection._apply_edits_locally("x", [{"op": "append", "text": "y"}]) == "xy"


@pytest.mark.asyncio
async def test_submission_failure_releases_search_admission(routing, monkeypatch):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor

    def failed(*_args):
        raise RuntimeError("executor closed")

    with monkeypatch.context() as patched:
        patched.setattr(loop, "run_in_executor", failed)
        with pytest.raises(RuntimeError, match="executor closed"):
            await search.find_in_file(None, "Assets/Test.cs", "needle")
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
    assert loop.run_in_executor == original


@pytest.mark.asyncio
async def test_queue_wait_is_part_of_the_search_budget(routing, executor, monkeypatch):
    install, releases = executor
    pool, original = install(1)
    started, release = Event(), Event()
    releases.append(release)
    clock = [100.0]
    monkeypatch.setattr(bounded_regex, "monotonic", lambda: clock[0])

    def occupy():
        started.set()
        assert release.wait(watchdog_timeout())

    blocker = pool.submit(occupy)
    await until(started.is_set)
    task = asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
    try:
        await until(lambda: pool.submissions == 2)
        clock[0] = 100.0 + bounded_regex.TOTAL_TIMEOUT_SECONDS + 1
        release.set()
        await asyncio.wrap_future(blocker)
        result = await task
        assert result["success"] is False and "total work" in result["message"]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await original(pool, lambda: None)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True


@pytest.mark.asyncio
async def test_search_preserves_to_thread_context_without_changing_edit_context(
    routing, monkeypatch
):
    marker = ContextVar("regex-search-context", default="default")
    actual = bounded_regex.find_matches
    observed = []

    def inspect(*args, **kwargs):
        observed.append(marker.get())
        return actual(*args, **kwargs)

    monkeypatch.setattr(bounded_regex, "find_matches", inspect)
    token = marker.set("request")
    try:
        assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
        await selection._apply_edits_locally(
            "marker", [{"op": "anchor_insert", "anchor": "marker", "text": "new"}]
        )
    finally:
        marker.reset(token)
    assert observed == ["request", "default"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded,pattern,case",
    [(False, "needle", True), (True, "needle", False), (False, "(?r)needle", True)],
)
async def test_matches_keep_order_counts_excerpts_and_encoded_read(routing, encoded, pattern, case):
    routing.encoded = encoded
    result = await search.find_in_file(
        None, "Assets/Test.cs", pattern, max_results=1, ignore_case=case
    )
    assert result["success"] is True
    data = result["data"]
    assert data["count"] == 1 and data["total_matches"] == (2 if case else 1)
    assert data["matches"][0] == {
        "line": 2,
        "content": "needle NEEDLE",
        "match": "NEEDLE" if pattern.startswith("(?r)") else "needle",
        "start": 13 if pattern.startswith("(?r)") else 6,
        "end": 19 if pattern.startswith("(?r)") else 12,
    }
    assert routing.reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse,encoded", [(False, False), (True, True)])
async def test_search_continuation_returns_all_1200_matches_once(routing, reverse, encoded):
    routing.source = "\n".join(f"needle_{index:04d}" for index in range(1200))
    routing.encoded = encoded
    pattern = ("(?r)" if reverse else "") + r"needle_\d+"
    first = await search.find_in_file(None, "Assets/Test.cs", pattern, max_results=1200)
    assert first["success"] is True
    assert first["data"]["count"] == 1000
    assert first["data"]["total_matches"] == 1200
    cursor = first["data"]["next_cursor"]
    second = await search.find_in_file(
        None, "Assets/Test.cs", pattern, max_results=1000, cursor=cursor
    )
    assert second["success"] is True
    assert second["data"]["count"] == 200
    assert second["data"]["total_matches"] == 1200
    assert second["data"]["next_cursor"] is None
    matches = first["data"]["matches"] + second["data"]["matches"]
    indices = range(1199, -1, -1) if reverse else range(1200)
    assert [match["match"] for match in matches] == [f"needle_{index:04d}" for index in indices]
    assert len({(match["start"], match["end"]) for match in matches}) == 1200
    assert (
        await search.find_in_file(None, "Assets/Test.cs", pattern, max_results=1000, cursor=cursor)
        == second
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["(?=a)|a", "(?r)(?=a)|a", "", "(?r)"])
async def test_search_continuation_preserves_zero_width_engine_order(routing, pattern):
    routing.source = "aa"
    expected = [
        (match.start(), match.end())
        for match in bounded_regex.find_matches(pattern, routing.source, search.re.MULTILINE)
    ]
    actual, cursor = [], None
    for _ in range(len(expected)):
        result = await search.find_in_file(
            None, "Assets/Test.cs", pattern, max_results=1, cursor=cursor
        )
        assert result["success"] is True
        actual.extend((match["start"], match["end"]) for match in result["data"]["matches"])
        cursor = result["data"]["next_cursor"]
    assert actual == expected
    assert cursor is None


@pytest.mark.asyncio
async def test_search_continuation_rejects_changed_source_and_effective_options(routing):
    first = await search.find_in_file(
        None, "Assets/Test.cs", "needle", max_results=1, ignore_case="yes"
    )
    cursor = first["data"]["next_cursor"]
    for options in ({"ignore_case": False}, {"pattern": "NEEDLE"}, {"max_results": 2}):
        parameters = {"pattern": "needle", "max_results": 1, "ignore_case": True, **options}
        refused = await search.find_in_file(None, "Assets/Test.cs", cursor=cursor, **parameters)
        assert refused["success"] is False
        assert refused["code"] == "stale_search_cursor"
    routing.source += "\nedit"
    refused = await search.find_in_file(
        None, "Assets/Test.cs", "needle", max_results=1, cursor=cursor
    )
    assert refused["success"] is False
    assert refused["code"] == "stale_search_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["target", "path", "bytes_sha256"])
async def test_search_continuation_binds_target_path_and_raw_identity(monkeypatch, change):
    snapshot = {"target": "A", "bytes_sha256": "original"}

    async def target(_ctx):
        return snapshot["target"]

    async def read(*_args):
        return {
            "success": True,
            "data": {"contents": "needle needle", "bytes_sha256": snapshot["bytes_sha256"]},
        }

    monkeypatch.setattr(search, "get_unity_instance_from_context", target)
    monkeypatch.setattr(search, "send_with_unity_instance", read)
    first = await search.find_in_file(None, "Assets/Test.cs", "needle", max_results=1)
    path = "Assets/Other.cs" if change == "path" else "Assets/Test.cs"
    if change != "path":
        snapshot[change] = "changed"
    refused = await search.find_in_file(
        None, path, "needle", max_results=1, cursor=first["data"]["next_cursor"]
    )
    assert refused["success"] is False
    assert refused["code"] == "stale_search_cursor"


@pytest.mark.asyncio
async def test_search_continuation_rejects_out_of_range_ordinal(routing):
    first = await search.find_in_file(None, "Assets/Test.cs", "needle", max_results=1)
    cursor = first["data"]["next_cursor"].rsplit(":", 1)[0] + ":2"
    refused = await search.find_in_file(
        None, "Assets/Test.cs", "needle", max_results=1, cursor=cursor
    )
    assert refused["success"] is False
    assert refused["code"] == "stale_search_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["", "bad", "x" * 10000, "fif1:" + "a" * 64 + ":100001"])
async def test_search_rejects_invalid_cursor_before_unity_read(routing, cursor):
    refused = await search.find_in_file(None, "Assets/Test.cs", "needle", cursor=cursor)
    assert refused["success"] is False
    assert refused["code"] == "stale_search_cursor"
    assert routing.reads == 0


@pytest.mark.asyncio
async def test_search_paging_does_not_bypass_total_match_limit(routing):
    routing.source = "a" * (bounded_regex.MAX_MATCHES + 1)
    refused = await search.find_in_file(None, "Assets/Test.cs", "a", max_results=1)
    assert refused["success"] is False
    assert "match limit" in refused["message"]


@pytest.mark.asyncio
async def test_search_checks_source_bound_before_cursor_hash_allocation(routing, monkeypatch):
    routing.source = "a" * (bounded_regex.MAX_TEXT_CHARS + 1)

    def unexpected_hash(*_args):
        pytest.fail("Oversized source must be refused before encoding and hashing it")

    monkeypatch.setattr(search.hashlib, "sha256", unexpected_hash)
    refused = await search.find_in_file(None, "Assets/Test.cs", "a", max_results=1)
    assert refused["success"] is False
    assert "input exceeds" in refused["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_accepts_search_continuation(routing, mode):
    app = FastMCP("paged-file-search")

    @wraps(search.find_in_file)
    async def sdk_search(*args, **kwargs):
        return await search.find_in_file(*args, **kwargs)

    app.tool(sdk_search)
    parameters = {"uri": "Assets/Test.cs", "pattern": "needle", "max_results": 1}
    async with Client(app, mode=mode) as client:
        first = await client.call_tool("find_in_file", parameters)
        first_page = json.loads(first.content[0].text)
        parameters["cursor"] = first_page["data"]["next_cursor"]
        second = await client.call_tool("find_in_file", parameters)
        second_page = json.loads(second.content[0].text)
    assert first_page["data"]["matches"][0]["match"] == "needle"
    assert second_page["data"]["matches"][0]["match"] == "NEEDLE"
    assert second_page["data"]["next_cursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_reports_busy_and_recovers_with_the_same_search_result(routing, mode):
    entered = [Event(), Event()]
    release = Event()

    def hold(index):
        entered[index].set()
        assert release.wait(watchdog_timeout())

    tasks = [
        asyncio.create_task(
            selection._run_regex_work(bounded_regex.WorkBudget(), lambda i=i: hold(i))
        )
        for i in range(2)
    ]
    app = FastMCP("bounded-file-search")

    # SDK registration rewrites callable metadata; keep the shared implementation
    # intact for subsequent direct invocation and native-admission tests.
    @wraps(search.find_in_file)
    async def sdk_search(*args, **kwargs):
        return await search.find_in_file(*args, **kwargs)

    app.tool(sdk_search)
    try:
        await until(lambda: all(event.is_set() for event in entered))
        async with Client(app, mode=mode) as client:
            result = await client.call_tool(
                "find_in_file", {"uri": "Assets/Test.cs", "pattern": "needle"}
            )
            refused = json.loads(result.content[0].text)
            assert refused["success"] is False and "busy" in refused["message"]
            release.set()
            await asyncio.gather(*tasks)
            result = await client.call_tool(
                "find_in_file", {"uri": "Assets/Test.cs", "pattern": "needle"}
            )
            matched = json.loads(result.content[0].text)
            assert matched["success"] is True and matched["data"]["count"] == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert routing.reads == 2
    gc.collect()
    # A Future can publish its result before the executor drops its result frame.
    # Observe bounded finalization instead of requiring same-turn destruction.
    await until(lambda: not any(ref() is not None for ref in routing.refs))
    assert not any(ref() is not None for ref in routing.refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["value", "error", "timeout", "cancelled"])
async def test_already_completed_worker_preserves_result_and_releases_once(monkeypatch, outcome):
    loop = asyncio.get_running_loop()

    def immediate(_executor, run):
        future = loop.create_future()
        try:
            future.set_result(run())
        except (ValueError, TimeoutError, asyncio.CancelledError) as exc:
            future.set_exception(exc)
        return future

    def work():
        if outcome == "error":
            raise ValueError("worker rejected")
        if outcome == "timeout":
            raise TimeoutError("worker timed out")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return "ready"

    monkeypatch.setattr(loop, "run_in_executor", immediate)
    budget = bounded_regex.WorkBudget()
    if outcome == "value":
        assert await selection._run_regex_work(budget, work) == "ready"
    else:
        expected = {
            "error": ValueError,
            "timeout": TimeoutError,
            "cancelled": asyncio.CancelledError,
        }[outcome]
        with pytest.raises(expected):
            await selection._run_regex_work(budget, work)
    assert await selection._run_regex_work(bounded_regex.WorkBudget(), lambda: "reused") == "reused"
