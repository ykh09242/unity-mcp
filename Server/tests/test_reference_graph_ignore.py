"""Gitignore admission is independent of Git and follows directory traversal rules."""

import pytest

from services.reference_graph.ignore import GitIgnoreRules
from services.reference_graph.external import ExternalSource
from services.reference_graph.runtime import RuntimeObservations
from services.reference_graph.store import Edge, RevisionConflict
from tests.test_reference_graph_reconcile import setup


@pytest.mark.parametrize(
    "path,ignored",
    [
        ("Assets/cache/a.asset", True),
        ("Assets/cache/keep.asset", True),
        ("Assets/open/drop.asset", True),
        ("Assets/open/keep.asset", False),
        ("Assets/other/drop.asset", False),
        ("root.txt", True),
        ("Assets/root.txt", False),
        ("Assets/#literal.asset", True),
        ("Assets/!literal.asset", True),
        ("Assets/file1.tmp", True),
        ("Assets/filex.tmp", False),
        ("Assets/space name.asset", True),
    ],
)
def test_nested_gitignore_and_parent_negation(path, ignored):
    # Given root and nested rules, including an ineffective child re-inclusion.
    rules = GitIgnoreRules(
        [
            (
                ".gitignore",
                "cache/\n/root.txt\n\\#literal.asset\n\\!literal.asset\nfile[0-9].tmp\nspace name.asset\n",
            ),
            ("Assets/cache/.gitignore", "!keep.asset\n"),
            ("Assets/open/.gitignore", "*.asset\n!keep.asset\n"),
        ]
    )
    # When matching a root-relative path, then all ancestor directories are respected.
    assert rules.ignored(path) is ignored


def test_child_rule_no_match_preserves_ancestor_match_and_explicit_negation_wins():
    # Given a parent file pattern and a child file that only overrides one name.
    rules = GitIgnoreRules(
        [(".gitignore", "*.asset\n"), ("Assets/.gitignore", "!keep.asset\n*.tmp\n")]
    )
    # When matching descendants, then no match is distinct from explicit negation.
    assert rules.ignored("Assets/drop.asset")
    assert not rules.ignored("Assets/keep.asset")


def test_reinclude_parent_before_child_and_cross_platform_separators():
    # Given a directory explicitly reopened before a file-level exception.
    rules = GitIgnoreRules(
        [(".gitignore", "Assets/*\n!Assets/open/\nAssets/open/*\n!Assets/open/keep.asset\n")]
    )
    # When matching Windows or POSIX logical paths, then the same exception applies.
    assert not rules.ignored("Assets/open/keep.asset")
    assert not rules.ignored(r"Assets\open\keep.asset")
    assert rules.ignored("Assets/open/drop.asset")


def test_directory_cache_is_bounded_and_empty_rules_are_fast():
    # Given many distinct parent paths and a bounded matcher cache.
    rules = GitIgnoreRules([(".gitignore", "*.tmp\n")], cache_size=16)
    # When scanning a large manifest, then admission does not retain the manifest.
    for number in range(1000):
        assert not rules.ignored(f"Assets/D{number}/file.asset")
    assert len(rules._directories) <= 16
    assert not GitIgnoreRules([]).ignored("Assets/file.asset")
    assert not GitIgnoreRules([(".gitignore", "# comment\n\n")])._specs


def test_shared_directory_cache_survives_concurrent_match_and_eviction():
    from concurrent.futures import ThreadPoolExecutor
    from collections import OrderedDict
    import time

    class ScheduledCache(OrderedDict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if value is not None:
                time.sleep(0)
            return value

    # Given one compiled policy shared by drained request threads and a small eviction cache.
    rules = GitIgnoreRules([(".gitignore", "*.tmp\n")], cache_size=4)
    rules._directories = ScheduledCache()

    def match(worker):
        return all(
            not rules.ignored(f"Assets/D{number % 16}/T{worker}/file.asset")
            for number in range(500)
        )

    # When threads repeatedly match/evict ancestors, then all admissions finish correctly.
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert all(workers.map(match, range(8)))
    assert len(rules._directories) <= 4


@pytest.mark.parametrize(
    "path", ["../.gitignore", "/.gitignore", "Assets/../.gitignore", "Assets/x.txt"]
)
def test_rule_file_paths_cannot_escape_the_logical_root(path):
    # Given an untrusted collector rule-file identity.
    # When compiling the snapshot, then non-rule and escaping paths are rejected.
    with pytest.raises(ValueError):
        GitIgnoreRules([(path, "*.asset")])


def test_logical_pattern_case_is_deterministic():
    # Given case-sensitive logical rules independent of Git's per-host configuration.
    rules = GitIgnoreRules([(".gitignore", "Assets/Private/\n")])
    # When only spelling changes, then matching does not depend on the host filesystem.
    assert rules.ignored("Assets/Private/a.asset")
    assert not rules.ignored("Assets/private/a.asset")


def test_repeated_star_patterns_do_not_backtrack_without_bound(monkeypatch):
    import services.reference_graph.ignore as module

    # Given a pattern that makes the standard re backend backtrack exponentially.
    rules = GitIgnoreRules([(".gitignore", "a*" * 18 + "b\n")])
    # When a nonmatching long name is checked, then the bounded engine completes safely.
    assert not rules.ignored("a" * 64)
    # Given an exhausted search budget, then admission fails instead of silently allowing content.
    monkeypatch.setattr(module, "_pattern_seconds", lambda: 0)
    rules = GitIgnoreRules([(".gitignore", "a*" * 18 + "b\n")])
    with pytest.raises(ValueError, match="incomplete"):
        rules.ignored("a" * 64)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_pattern_budget_fails_before_admission(monkeypatch, value):
    # Given an invalid operator configuration, then no rule set becomes an allow-all fallback.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_PATTERN_SECONDS", value)
    with pytest.raises(ValueError, match="positive finite"):
        GitIgnoreRules([(".gitignore", "*.asset")])


def test_bounded_pattern_factory_preserves_upstream_gitignore_tristate_semantics():
    from pathspec import GitIgnoreSpec

    # Given upstream's complete pattern translation and representative Gitignore syntax.
    lines = [
        "*.asset",
        "!keep.asset",
        "dir/",
        "!dir/keep.asset",
        "/root.txt",
        "foo/**/bar?.[ch]",
        r"\#literal",
        "[!a-c].tmp",
        "name ",
    ]
    expected = GitIgnoreSpec.from_lines(lines, backend="simple")
    bounded = GitIgnoreRules([(".gitignore", "\n".join(lines))])._specs[""]
    # When checking file/directory markers and explicit negations, then include/None priorities agree.
    for path in (
        "x.asset",
        "keep.asset",
        "dir/",
        "dir/keep.asset",
        "root.txt",
        "nested/root.txt",
        "foo/bar1.c",
        "foo/a/b/bar2.h",
        "#literal",
        "d.tmp",
        "a.tmp",
        "name",
        "unknown",
    ):
        assert bounded.check_file(path).include == expected.check_file(path).include


@pytest.mark.asyncio
async def test_rules_automatically_remove_and_reinclude_generated_manual_and_history(setup):
    # Given collected references, a manual declaration and a historical snapshot.
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/Private.prefab", keys=[{"scheme": "resources", "value": "Private"}]
    )
    caller = editor.add(
        2,
        "Assets/Caller.cs",
        'using UnityEngine; class C { void M() { Resources.Load("Private"); } }',
    )
    assert await engine.reconcile()
    store.put_declaration("kept", caller, Edge(target, "manual"), expected_version=0)
    old = workspace.queries().snapshot()["snapshot"]["id"]
    assert len(store.query(caller)["edges"]) == 2
    # When rules change without an asset event, then excluded content disappears everywhere.
    editor.ignore_files[".gitignore"] = b"Assets/Resources/Private.prefab\n"
    editor.ignore_revision += 1
    assert await engine.reconcile()
    assert not store.query(caller)["edges"]
    assert store.declarations()[0]["id"] == "kept"
    assert store.query(target)["excluded"]
    assert workspace.queries().impact(target)["excluded"]
    assert workspace.queries().path(caller, target)["excluded"]
    assert workspace.diagnostics(target)["excluded"]
    assert workspace.counts()["assets"] == 1
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM nodes WHERE id=?", (target,)).fetchone() is None
        assert (
            db.execute("SELECT 1 FROM graph_assets WHERE guid=?", (target[6:],)).fetchone() is None
        )
        assert (
            db.execute(
                "SELECT 1 FROM edges WHERE provider<>'manual' AND target=?", (target,)
            ).fetchone()
            is None
        )
        assert (
            db.execute("SELECT path FROM graph_asset_paths WHERE guid=?", (target[6:],)).fetchone()[
                0
            ]
            == "Assets/Resources/Private.prefab"
        )
    assert not workspace.queries().snapshot()["edges"]
    with pytest.raises(RevisionConflict, match="policy"):
        workspace.queries().diff(old, old)
    # When the rule is removed, then collection and the preserved declaration return.
    editor.ignore_files.clear()
    editor.ignore_revision += 1
    assert await engine.reconcile()
    assert len(store.query(caller)["edges"]) == 2


@pytest.mark.asyncio
async def test_default_rules_prevent_ignored_wrapper_source_reads_and_key_fallback(setup):
    # Given ignored wrapper/target paths and an allowed caller using the same resource key.
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/Private.prefab", keys=[{"scheme": "resources", "value": "Private"}]
    )
    wrapper = editor.add(
        2,
        "Assets/Private/Loader.cs",
        "using UnityEngine; class L { public static void Load(string key) { Resources.Load(key); } }",
    )
    caller = editor.add(3, "Assets/Caller.cs", 'class C { void M() { L.Load("Private"); } }')
    editor.ignore_files[".gitignore"] = b"Assets/Private/\nAssets/Resources/Private.prefab\n"
    assert await engine.reconcile()
    assert not store.query(caller)["edges"]
    assert not any(
        action == "source" and values.get("guid") in {wrapper[6:], target[6:]}
        for action, values in editor.calls
    )
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    event = {
        "sequence": 1,
        "kind": "acquire",
        "scheme": "resources",
        "key": "Private",
        "handle": "handle",
        "owner_path": "Assets/Caller.cs",
        "target_path": "",
        "target_guid": "",
    }
    # When an unresolved runtime observation names a known ignored key, then no fallback node is created.
    runtime.ingest(capture, 0, {"events": [event], "next_offset": 1})
    runtime.ingest(
        capture,
        1,
        {"events": [{"sequence": 2, "kind": "release", "handle": "handle"}], "next_offset": 2},
    )
    assert runtime.status(capture)["events"] == 0
    assert not runtime.handles(capture)["handles"]
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM nodes WHERE kind='runtime_observation'").fetchone()


@pytest.mark.asyncio
async def test_unavailable_or_changed_rule_evidence_never_delivers_old_graph(setup):
    # Given a valid populated graph.
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    caller = editor.add(
        2, "Assets/Caller.cs", 'using UnityEngine; class C { void M() { Resources.Load("One"); } }'
    )
    assert await engine.reconcile()
    assert store.query(caller)["edges"][0]["target"] == target
    native = editor.__call__

    async def unavailable(action, **values):
        result = await native(action, **values)
        if action == "status":
            result.pop("ignore_state")
        return result

    engine._collector = unavailable
    # When the collector cannot provide the policy, then previous evidence is fenced.
    with pytest.raises(RevisionConflict, match="unavailable"):
        await engine.reconcile()
    assert not store.query(caller)["edges"]


def test_external_rules_are_root_contained_nested_and_rechecked_at_read(tmp_path):
    # Given a grant below an unrelated parent ignore file and nested rules.
    (tmp_path / ".gitignore").write_text("*.json\n")
    root = tmp_path / "grant"
    (root / "nested").mkdir(parents=True)
    (root / ".gitignore").write_text("drop.json\n")
    (root / "nested" / ".gitignore").write_text("*.json\n!keep.json\n")
    for name in ("keep.json", "drop.json", "nested/keep.json", "nested/drop.json"):
        (root / name).write_text("{}")
    adapter = ExternalSource({"root": str(root), "include": ["**/*.json"]})
    # When walking or requesting a file directly, then the same current rules apply.
    assert set(adapter.files()) == {"keep.json", "nested/keep.json"}
    with pytest.raises(PermissionError):
        adapter.read("drop.json")
    with pytest.raises(PermissionError):
        adapter.stamp("nested/drop.json")
    disabled = ExternalSource(
        {"root": str(root), "include": ["**/*.json"]}, respect_gitignore=False
    )
    assert len(list(disabled.files())) == 4


@pytest.mark.asyncio
async def test_watcher_fallback_queries_only_validate_warm_rule_candidates(tmp_path, monkeypatch):
    from core.config import config
    from services.reference_graph.service import ReferenceGraphService
    from tests.test_reference_graph_service import Context, Sender

    # Given a warm graph whose host cannot create watchers.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    sender = Sender()

    async def native(instance, values):
        response = await sender(instance, values)
        response["ignore_watchers"] = False
        return response

    service = ReferenceGraphService(tmp_path, send=native)
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    ctx = Context(service)
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        entry.workspace.store.put_declaration(
            "kept", "owner", Edge("target", "manual"), expected_version=0
        )
        sender.calls.clear()
        # When repeatedly querying, then only status and explicit rule validation run.
        for _ in range(5):
            result = await service.execute(ctx, "query", {"node": "owner"})
            assert result["data"]["edges"]
        assert {row[0]["action"] for row in sender.calls} == {"status", "ignore_rules"}
        assert sum(row[0]["action"] == "ignore_rules" for row in sender.calls) == 10
        assert not (await service.execute(ctx, "status", {}))["data"]["graph_status"][
            "native_current"
        ]
    finally:
        await service.aclose()


def test_inventory_admission_compiles_and_reads_policy_per_batch_not_per_path(setup, monkeypatch):
    import services.reference_graph.workspace as module

    # Given two inventory batches and instrumentation around admission construction.
    _, workspace, _, engine = setup
    assert workspace.acquire(engine.worker)
    counts = {"policy_reads": 0, "compiles": 0}
    original_state, original_rules = workspace._state, module.GitIgnoreRules

    def state(connection, key, fallback=None):
        counts["policy_reads"] += key == "policy"
        return original_state(connection, key, fallback)

    def rules(*args, **kwargs):
        counts["compiles"] += 1
        return original_rules(*args, **kwargs)

    monkeypatch.setattr(workspace, "_state", state)
    monkeypatch.setattr(module, "GitIgnoreRules", rules)
    assets = [
        {
            "guid": f"{number:032x}",
            "path": f"Assets/D{number}/a.asset",
            "kind": "asset",
            "source_format": "none",
        }
        for number in range(1000)
    ]
    try:
        # When admitting many paths, then no per-path policy query/compile occurs.
        workspace.record_page(engine.worker, assets, {}, "scan")
        workspace.record_page(engine.worker, assets, {}, "scan", force=False)
        assert counts == {"policy_reads": 2, "compiles": 1}
    finally:
        workspace.release(engine.worker)


@pytest.mark.asyncio
async def test_ignored_guid_targets_are_suppressed_without_hiding_allowed_alias_candidates(setup):
    # Given an ignored and allowed asset sharing the same resource key.
    store, workspace, editor, engine = setup
    ignored = editor.add(
        1, "Assets/Resources/Private.prefab", keys=[{"scheme": "resources", "value": "Shared"}]
    )
    allowed = editor.add(
        2, "Assets/Resources/Public.prefab", keys=[{"scheme": "resources", "value": "Shared"}]
    )
    caller = editor.add(
        3,
        "Assets/Caller.cs",
        'using UnityEngine; class C { void M() { Resources.Load("Shared"); } }',
    )
    dependency = editor.add(4, "Assets/Dependency.prefab")
    editor.ignore_files[".gitignore"] = b"Assets/Resources/Private.prefab\n"
    native = engine._collector

    async def collect(action, **values):
        result = await native(action, **values)
        if action == "dependencies" and values["guid"] == dependency[6:]:
            result["targets"] = [ignored[6:]]
        return result

    engine._collector = collect
    # When collecting GUID dependencies and resolving an ambiguous alias, then only allowed evidence survives.
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {allowed}
    assert not store.query(dependency)["edges"]
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM edges WHERE target=?", (ignored,)).fetchone()


@pytest.mark.asyncio
async def test_external_manual_endpoints_use_own_root_rules_and_reinclude(tmp_path, monkeypatch):
    import json
    from services.reference_graph.reconcile import Reconciler
    from services.reference_graph.service import ReferenceGraphService
    from services.reference_graph.store import GraphScope, GraphStore
    from services.reference_graph.workspace import GraphWorkspace
    from tests.test_reference_graph_reconcile import Editor, allowed

    # Given matching relative names in distinct project/external roots and an authored declaration.
    store = GraphStore.open(tmp_path / "graph", GraphScope("local:test", "editor", "project"))
    workspace, editor = GraphWorkspace(store), Editor()
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    editor.ignore_files[".gitignore"] = b"configs/*\n"
    root = tmp_path / "external"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "allowed.json").write_text('{"key":"One"}')
    workspace.set_source(
        "external:settings",
        {
            "root": str(root),
            "include": ["**/*.json"],
            "mappings": [{"field": "/key", "scheme": "resources"}],
        },
        expected_version=0,
    )
    engine = Reconciler(workspace, editor, allowed)
    assert await engine.reconcile()
    owner = store.query(target, direction="incoming")["edges"][0]["owner"]
    store.put_declaration("authored", owner, Edge(target, "manual"), expected_version=0)
    snapshot = workspace.queries().snapshot()["snapshot"]["id"]
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", json.dumps([str(root)]))
    service = ReferenceGraphService(tmp_path / "service")
    try:
        assert not service._validate_external(workspace, store.query(owner))
        # When the external root's own rules change, then live and unchanged historical manual evidence is fenced.
        (root / ".gitignore").write_text("configs/*\n")
        with pytest.raises(RevisionConflict, match="excluded"):
            service._validate_external(workspace, store.query(owner))
        with pytest.raises(RevisionConflict, match="excluded"):
            service._validate_exploration(workspace, workspace.queries().diff(snapshot, snapshot))
        assert await engine.reconcile()
        assert store.query(owner)["excluded"]
        assert not store.query(target, direction="incoming")["edges"]
        assert store.declarations()[0]["id"] == "authored"
        # When the external rule is removed, then its own generated and authored references return.
        (root / ".gitignore").unlink()
        assert await engine.reconcile()
        assert len(store.query(owner)["edges"]) == 2
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_native_rule_paths_use_unity_utf16_ordinal_order(setup):
    # Given names ordered differently by Unicode scalar and UTF-16 ordinal comparisons.
    store, workspace, editor, engine = setup
    first = editor.add(1, "Assets/\U0001f680/one.asset")
    second = editor.add(2, "Assets/\ue000/two.asset")
    editor.ignore_files = {
        "Assets/\ue000/.gitignore": b"*.asset\n",
        "Assets/\U0001f680/.gitignore": b"*.asset\n",
    }
    # When reading the native manifest, then valid ordinal paging is accepted.
    assert await engine.reconcile()
    assert workspace.counts()["assets"] == 0
    assert store.query(first)["excluded"] and store.query(second)["excluded"]


@pytest.mark.asyncio
async def test_expired_native_ignore_token_fences_then_reacquires_without_manual_rebuild(setup):
    # Given a warm policy and an expired native snapshot token.
    _, workspace, editor, engine = setup
    editor.add(1, "Assets/One.asset")
    assert await engine.reconcile()
    native = engine._collector

    async def expired(action, **values):
        if action == "ignore_rules" and values.get("snapshot"):
            raise RevisionConflict("Ignore snapshot expired")
        return await native(action, **values)

    engine._collector = expired
    # When validation fails, then old admission is blocked and the token is retired.
    with pytest.raises(RevisionConflict, match="expired"):
        await engine.reconcile()
    assert workspace.ignore_status() == {}
    engine._collector = native
    # When normal collection retries, then it acquires a fresh proof automatically.
    assert await engine.reconcile()
    assert workspace.counts()["assets"] == 1
