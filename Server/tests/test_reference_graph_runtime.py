"""Runtime evidence stays scoped, replayable and distinct from static proof."""

from tests.timing import await_signal

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.config import config
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.queries import GraphQueries
from services.tools.manage_reference_graph import manage_reference_graph

from services.reference_graph.runtime import RuntimeObservations
from services.reference_graph.store import Edge, GraphScope, GraphStore, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace


@pytest.fixture
def capture(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    runtime = RuntimeObservations(workspace)
    identity = runtime.begin()["id"]
    return workspace, runtime, identity


def event(seq=1, kind="acquire", handle="handle"):
    return {
        "sequence": seq,
        "kind": kind,
        "scheme": "resources",
        "key": "dynamic/key",
        "handle": handle,
        "owner_path": "Assets/Scripts/Loader.cs",
        "target_guid": "a" * 32,
        "target_path": "Assets/Resources/Dynamic.asset",
        "utc_ticks": 123,
    }


def page(events, offset=100, state="active"):
    return {
        "events": events,
        "next_offset": offset,
        "state": state,
        "count": len(events),
        "error": None,
        "build": {"revision": "build"},
        "runtime_session": "play",
        "playing": True,
    }


def test_runtime_replay_is_idempotent_and_tokens_never_leave_status(capture):
    workspace, runtime, identity = capture
    token = runtime.native(identity)["token"]
    runtime.ingest(identity, 0, page([event()]))
    runtime.ingest(identity, 0, page([event()]))
    restored = RuntimeObservations(GraphWorkspace(workspace.store))
    assert restored.status(identity)["events"] == 1
    assert token not in str(restored.status(identity))
    result = workspace.store.query("asset:" + "a" * 32, direction="incoming")
    assert result["edges"][0]["confidence"] == "runtime_observed"
    assert result["edges"][0]["provider"] == "runtime:" + identity


def test_runtime_handles_keep_pairing_and_unmatched_release(capture):
    _, runtime, identity = capture
    runtime.ingest(
        identity,
        0,
        page([event(), event(2, "release"), event(3, "release"), event(4, "acquire", "retained")]),
    )
    result = runtime.handles(identity)
    assert [row["balance"] for row in result["handles"]] == [-1, 1]
    assert result["not_a_leak_proof"] is True
    assert result["handles"][0]["unmatched_releases"] == 1


def test_runtime_pages_are_scope_bound_and_release_preserves_authored_edges(capture, tmp_path):
    workspace, runtime, identity = capture
    other = RuntimeObservations(
        GraphWorkspace(GraphStore.open(tmp_path, GraphScope("other", "editor", "project")))
    )
    with pytest.raises(ValueError):
        other.native(identity)
    workspace.store.put_declaration(
        "manual", "owner", Edge("target", "declared"), expected_version=0
    )
    runtime.ingest(identity, 0, page([event()], state="stopped"))
    assert runtime.release(identity)
    assert workspace.store.declarations()[0]["version"] == 1
    assert not workspace.store.query("asset:" + "a" * 32, direction="incoming")["edges"]


def test_runtime_policy_is_rechecked_for_historical_delivery(capture):
    workspace, runtime, identity = capture
    runtime.ingest(identity, 0, page([event()]))
    workspace.set_policy({"exclude": ["Assets/Resources/**"]}, expected_version=0)
    with pytest.raises(RevisionConflict):
        runtime.events(identity)
    with pytest.raises(RevisionConflict):
        runtime.handles(identity)


def test_changed_runtime_session_cannot_be_silently_joined(capture):
    _, runtime, identity = capture
    runtime.ingest(identity, 0, page([event()]))
    changed = page([event(2)], offset=200)
    changed["runtime_session"] = "another-play"
    with pytest.raises(RevisionConflict):
        runtime.ingest(identity, 100, changed)


def test_runtime_continuations_preserve_large_sessions(capture):
    _, runtime, identity = capture
    for start in range(0, 3000, 100):
        runtime.ingest(
            identity,
            start,
            page([event(i + 1, "load", "") for i in range(start, start + 100)], offset=start + 100),
        )
    after, total = 0, 0
    while True:
        result = runtime.events(identity, after=after, limit=137)
        total += len(result["events"])
        if result["next_after"] is None:
            break
        after = result["next_after"]
    assert total == 3000


@pytest.mark.parametrize("guid", ["invalid", "", "A" * 32])
def test_unresolved_runtime_target_does_not_claim_observed_asset(capture, guid):
    workspace, runtime, identity = capture
    runtime.ingest(identity, 0, page([{**event(), "target_guid": guid}]))
    result = workspace.store.query("runtime:" + identity + ":1")
    assert result["edges"][0]["confidence"] == "runtime_unresolved"


@pytest.mark.parametrize("path", ["Assets/../private", "Assets//file", "Assets/Hidden/file"])
def test_disallowed_observations_do_not_create_edges_or_handles(capture, path):
    workspace, runtime, identity = capture
    workspace.set_policy({"exclude": ["Assets/Hidden/**"]}, expected_version=0)
    runtime.ingest(identity, 0, page([{**event(), "owner_path": path}]))
    assert runtime.status(identity)["events"] == 0
    assert not runtime.handles(identity)["handles"]
    assert not workspace.store.query("asset:" + "a" * 32, direction="incoming")["edges"]
    runtime.ingest(identity, 100, page([event(2)], offset=200))
    assert runtime.status(identity)["events"] == 1


def test_out_of_order_runtime_page_is_atomic(capture):
    _, runtime, identity = capture
    with pytest.raises(ValueError):
        runtime.ingest(identity, 0, page([event(), event(3)]))
    assert runtime.status(identity)["events"] == 0
    assert runtime.native(identity)["offset"] == 0


def test_snapshot_history_rechecks_runtime_policy_and_source_release(capture, tmp_path):
    workspace, runtime, identity = capture
    runtime.ingest(identity, 0, page([event()]))
    queries = GraphQueries(workspace.store)
    before = queries.snapshot(name="before")["snapshot"]["id"]
    after = queries.snapshot(name="after")["snapshot"]["id"]
    delta = queries.diff(before, after)
    assert not delta["changes"] and delta["_history_access"]
    service = ReferenceGraphService(tmp_path)
    workspace.set_policy({"exclude": ["Assets/Resources/**"]}, expected_version=0)
    with pytest.raises(RevisionConflict, match="excluded"):
        service._validate_exploration(workspace, queries.diff(before, after))
    runtime.release(identity)
    with pytest.raises(RevisionConflict):
        queries.diff(before, after)


def test_rule_changes_and_static_rebuild_preserve_runtime_evidence(capture):
    workspace, runtime, identity = capture
    runtime.ingest(identity, 0, page([event()]))
    workspace.store.put_rule("custom", {"enabled": False}, expected_version=0)
    assert not workspace.store.query("runtime:" + identity + ":1")["edges"][0]["stale"]
    runtime.ingest(identity, 100, page([event(2, "release")], offset=200))
    workspace.store.rebuild()
    assert runtime.status(identity)["events"] == 2
    assert not workspace.store.query("runtime:" + identity + ":1")["edges"][0]["stale"]
    assert not workspace.store.pending()


class Native:
    def __init__(self):
        self.captures = {}
        self.block = False
        self.reading = asyncio.Event()
        self.identity = "editor"

    async def __call__(self, instance, params):
        result = {
            "instance_id": self.identity + instance,
            "project_id": "project",
            "epoch": "epoch",
            "revision": 0,
            "ignore_state": "available",
            "ignore_revision": 0,
        }
        action = params["action"]
        if action == "ignore_rules":
            result.update(snapshot="ignore", files=[], next_offset=None)
        elif action == "inventory":
            result.update(snapshot="inventory", assets=[], next_offset=None)
        elif action == "changes":
            result.update(requires_census=False, changes=[], next_after=0, has_more=False)
        elif action == "runtime_start":
            result.update(self.captures.setdefault(params["token"], page([], offset=0)))
        elif action == "runtime_stop":
            self.captures[params["token"]]["state"] = "stopped"
        elif action == "runtime_read":
            self.reading.set()
            if self.block:
                await asyncio.Future()
            capture = self.captures[params["token"]]
            result.update(capture)
            result["events"] = capture["events"][
                params["offset"] : params["offset"] + params["page_size"]
            ]
            result["next_offset"] = params["offset"] + len(result["events"])
            result["has_more"] = result["next_offset"] < len(capture["events"])
        elif action == "runtime_release":
            self.captures.pop(params["token"], None)
        return result


class Context:
    def __init__(self, service):
        self.instance = "one"
        self.fastmcp = SimpleNamespace(_reference_graph_service=service)

    async def get_state(self, name):
        return self.instance if name == "unity_instance" else None


@pytest.mark.asyncio
async def test_runtime_tool_pages_stop_and_scope_separation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    native = Native()
    service = ReferenceGraphService(tmp_path, send=native)
    wakes = []
    monkeypatch.setattr(service, "start", lambda *_, **options: wakes.append(options["wake"]))
    ctx = Context(service)

    async def call(action, **options):
        result = await manage_reference_graph(ctx, action, options)
        assert result["success"], result
        assert result["data"]["historical"] and not result["data"]["graph_status"]["current"]
        assert all(token not in json.dumps(result) for token in native.captures)
        return result["data"]

    try:
        identity = (await call("runtime_start"))["capture"]["id"]
        token = next(iter(native.captures))
        native.captures[token] = page([event(), event(2, "release")], offset=2)
        first = await call("runtime_poll", id=identity, limit=1)
        assert first["has_more"] and first["capture"]["events"] == 1
        final = await call("runtime_stop", id=identity, limit=1)
        assert not final["has_more"] and final["capture"]["state"] == "stopped"
        assert (await call("runtime_handles", id=identity))["handles"][0]["balance"] == 0
        assert len((await call("runtime_events", id=identity))["events"]) == 2
        assert len((await call("runtime_sessions"))["captures"]) == 1
        assert not any(wakes)
        ctx.instance = "another"
        assert not (await manage_reference_graph(ctx, "runtime_status", {"id": identity}))[
            "success"
        ]
        ctx.instance = "one"
        assert (await call("runtime_release", id=identity))["released"]
        assert not native.captures
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_poll_keeps_durable_cursor_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    native = Native()
    service = ReferenceGraphService(tmp_path, send=native)
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    ctx = Context(service)
    try:
        started = await service.execute(ctx, "runtime_start", {})
        identity = started["data"]["capture"]["id"]
        token = next(iter(native.captures))
        native.captures[token] = page([event()], offset=1)
        native.block = True
        pending = asyncio.create_task(service.execute(ctx, "runtime_poll", {"id": identity}))
        await await_signal(native.reading, "native.reading")
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (await service.execute(ctx, "runtime_status", {"id": identity}))["data"]["capture"][
            "offset"
        ] == 0
        native.block = False
        resumed = await service.execute(ctx, "runtime_poll", {"id": identity})
        assert resumed["data"]["capture"]["events"] == 1
    finally:
        await service.aclose()
