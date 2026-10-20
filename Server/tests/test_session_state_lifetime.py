"""Session-state expiration must reclaim storage, not only hide expired reads."""

from datetime import datetime, timedelta, timezone

import pytest
from fastmcp.server.context import StateValue
from key_value.aio._utils import time_to_live


@pytest.mark.asyncio
async def test_server_state_physically_reclaims_expired_session_keys(monkeypatch, tmp_path):
    from main import create_mcp_server

    server = create_mcp_server(
        False,
        execution_receipts_root=tmp_path / "receipts",
        test_wait_tasks_root=tmp_path / "tasks",
    )
    adapter = server._state_store
    expired_created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    monkeypatch.setattr(time_to_live, "now", lambda: expired_created_at)
    for number in range(200):
        await adapter.put(
            key=f"expired-{number}:mcpforunity.active_instance",
            value=StateValue(value=f"Project-{number}"),
            ttl=1,
        )
    assert await adapter.get(key="expired-0:mcpforunity.active_instance") is None
    assert await server._state_storage.keys(collection="fastmcp_state") == []
    assert server._state_storage.retained_entries == 0
    assert server._state_storage.retained_bytes == 0


@pytest.mark.asyncio
async def test_expiry_reclaims_capacity_without_evicting_live_sessions():
    from transport.session_state_store import SessionStateStore, SessionStateCapacityError

    now = [datetime.now(timezone.utc).timestamp()]
    store = SessionStateStore(max_entries=2, clock=lambda: now[0])
    await store.put("old:selection", {"value": "Old"}, ttl=1)
    await store.put("live:selection", {"value": "Live"}, ttl=3600)
    with pytest.raises(SessionStateCapacityError):
        await store.put("new:selection", {"value": "New"}, ttl=3600)
    assert await store.get("old:selection") == {"value": "Old"}
    assert await store.get("live:selection") == {"value": "Live"}
    now[0] += 2
    assert store.expire() == 1
    await store.put("new:selection", {"value": "New"}, ttl=3600)
    assert store.retained_entries == 2
    assert await store.get("live:selection") == {"value": "Live"}
    assert await store.get("new:selection") == {"value": "New"}


@pytest.mark.asyncio
async def test_replacement_and_oversize_rejection_preserve_previous_value():
    from transport.session_state_store import SessionStateStore, SessionStateCapacityError

    store = SessionStateStore(max_entries=1, max_bytes=512, max_entry_bytes=256)
    await store.put("session:key", {"value": "first"})
    await store.put("session:key", {"value": "second"})
    previous_bytes = store.retained_bytes
    with pytest.raises(SessionStateCapacityError):
        await store.put("session:key", {"value": "x" * 512})
    assert await store.get("session:key") == {"value": "second"}
    assert store.retained_bytes == previous_bytes
    assert await store.delete("session:key") is True
    assert store.retained_entries == store.retained_bytes == 0


@pytest.mark.asyncio
async def test_aggregate_byte_budget_preserves_other_sessions():
    from transport.session_state_store import SessionStateStore, SessionStateCapacityError

    store = SessionStateStore(max_bytes=300, max_entry_bytes=300)
    await store.put("first:key", {"value": "x" * 100})
    with pytest.raises(SessionStateCapacityError):
        await store.put("second:key", {"value": "y" * 100})
    assert await store.get("first:key") == {"value": "x" * 100}
    assert store.retained_entries == 1


@pytest.mark.asyncio
async def test_storage_does_not_retain_or_expose_callers_mutable_values():
    from transport.session_state_store import SessionStateStore

    store = SessionStateStore()
    original = {"nested": {"value": "kept"}}
    await store.put("session:key", original)
    original["nested"]["value"] = "changed"
    result = await store.get("session:key")
    assert result == {"nested": {"value": "kept"}}
    result["nested"]["value"] = "also changed"
    assert await store.get("session:key") == {"nested": {"value": "kept"}}


@pytest.mark.asyncio
async def test_idle_maintenance_physically_expires_and_stops_with_lifespan(monkeypatch):
    import anyio
    from transport.session_state_store import SessionStateStore
    import transport.session_state_store as storage_module

    now = [datetime.now(timezone.utc).timestamp()]
    store = SessionStateStore(clock=lambda: now[0])
    started, expired = anyio.Event(), anyio.Event()
    real_sleep = anyio.sleep
    calls = []

    async def controlled_sleep(seconds):
        calls.append(seconds)
        if len(calls) == 1:
            await started.wait()
            now[0] += 2
        else:
            expired.set()
            await real_sleep(3600)

    monkeypatch.setattr(storage_module.anyio, "sleep", controlled_sleep)
    async with store.lifespan():
        await store.put("old:key", {"value": "old"}, ttl=1)
        await store.put("live:key", {"value": "live"}, ttl=3600)
        started.set()
        with anyio.fail_after(2):
            await expired.wait()
        assert store.retained_entries == 1
        assert await store.get("live:key") == {"value": "live"}
    assert store.retained_entries == store.retained_bytes == 0
    assert calls == [30, 30]


@pytest.mark.asyncio
async def test_fastmcp_contexts_keep_session_state_isolated(monkeypatch, tmp_path):
    from fastmcp.server.context import Context
    from main import create_mcp_server

    server = create_mcp_server(
        False,
        execution_receipts_root=tmp_path / "receipts",
        test_wait_tasks_root=tmp_path / "tasks",
    )
    current_session = ["one"]
    monkeypatch.setattr(Context, "session_id", property(lambda _: current_session[0]))
    context = Context(fastmcp=server)
    await context.set_state("selection", "First")
    current_session[0] = "two"
    assert await context.get_state("selection") is None
    await context.set_state("selection", "Second")
    current_session[0] = "one"
    assert await context.get_state("selection") == "First"
    await context.delete_state("selection")
    assert server._state_storage.retained_entries == 1
    current_session[0] = "two"
    assert await context.get_state("selection") == "Second"


@pytest.mark.asyncio
async def test_unknown_collections_do_not_allocate_unbounded_setup_metadata():
    from transport.session_state_store import SessionStateStore

    store = SessionStateStore()
    for number in range(20):
        with pytest.raises(ValueError, match="reserved"):
            await store.put("key", {"value": 1}, collection=f"unknown-{number}")
    assert not store._setup_collection_complete
    assert not store._setup_collection_locks


@pytest.mark.asyncio
async def test_real_http_sdk_shielded_teardown_joins_session_maintenance(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from fastmcp import FastMCP
    from transport.session_state_store import SessionStateStore

    store = SessionStateStore()
    started = asyncio.Event()
    tasks = []
    maintain = store._maintain

    async def observed_maintenance():
        tasks.append(asyncio.current_task())
        started.set()
        await maintain()

    monkeypatch.setattr(store, "_maintain", observed_maintenance)

    @asynccontextmanager
    async def lifespan(_server):
        async with store.lifespan():
            yield {}

    server = FastMCP("session-state-lifecycle", lifespan=lifespan, session_state_store=store)
    app = server.http_app()
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(), timeout=2)
        await store.put("live:key", {"value": "live"}, ttl=3600)
        assert store.retained_entries == 1
    assert store.retained_entries == store.retained_bytes == 0
    assert len(tasks) == 1
    assert tasks[0].done()
