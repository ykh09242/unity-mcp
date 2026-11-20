"""Exercise resumable dependency analysis through the ordinary candidate CLI."""

import json
from hashlib import sha256
from time import perf_counter

from cli.main import cli
from click.testing import CliRunner
from services.reference_graph.queries import GraphQueries, encode_cursor
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from services.verification.input_chunks import source_revision
from tests.test_verification_git_draft import git


def _node(index):
    return "asset:" + f"{index + 1:032x}"


def _assets(project, paths):
    (project / "Assets").mkdir(parents=True)
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    for index, path in enumerate(paths):
        (project / path).write_bytes(path.encode())
        (project / (path + ".meta")).write_bytes(f"guid: {_node(index)[6:]}\n".encode())


def _commit(project):
    git(project, "add", ".")
    git(project, "commit", "-m", "fixture")
    return git(project, "rev-parse", "HEAD").decode().strip()


def _save(directory, key, page):
    path = directory / f"{key}.json"
    path.write_bytes(json.dumps({"success": True, "data": page}).encode())
    return path


def _scan(project, commit, root, db, pages=(), output=None):
    args = [
        "dependency-scan",
        "--project",
        str(project),
        "--commit",
        commit,
        "--root",
        root,
        "--checkpoint",
        str(db),
    ]
    for page in pages:
        args.extend(("--graph-page", str(page)))
    if output:
        args.extend(("--output", str(output)))
    return _run(*args)


def _run(*args):
    return CliRunner().invoke(cli, ["--no-project-context", "verification-candidate", *args])


def _ok(result):
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.output)


def _archive_graph(directory, paths, relations, label, *, excluded=()):
    store = GraphStore.open(directory / "graphs", GraphScope("fixture", label, "project"))
    workspace = GraphWorkspace(store)
    if excluded:
        workspace.set_policy(
            {"respect_gitignore": False, "exclude": list(excluded)}, expected_version=0
        )
    assert workspace.acquire("fixture")
    if excluded:
        workspace.record_page(
            "fixture",
            [{"guid": _node(i)[6:], "path": p} for i, p in enumerate(paths) if p in excluded],
            {},
            "fixture",
        )
        workspace.finish_admission("fixture")
    for index, path in enumerate(paths):
        if path in excluded:
            continue
        generation = store.observe(_node(index), "unity", "fixture", path=path)
        workspace.publish_events(
            "fixture",
            _node(index),
            generation,
            [Edge(_node(target), "unity_dependency") for target in relations.get(index, [])],
            provider="unity",
            coverage="direct_asset_database_dependencies",
        )
    workspace.release("fixture")
    queries = GraphQueries(store)
    cursor = None
    pages = []
    while True:
        page = queries.impact(_node(0), direction="outgoing", limit=3, cursor=cursor)
        pages.append(
            _save(
                directory,
                f"{label}-{len(pages)}",
                {**page, "scope": store.scope.key, "graph_status": {"native_current": True}},
            )
        )
        cursor = page["next_cursor"]
        if not cursor:
            return pages


def _material(index):
    return (
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!21 &2100000\nMaterial:\n"
        f"  m_Texture: {{fileID: 2800000, guid: {_node(index)[6:]}, type: 3}}\n"
    ).encode()


def test_diff_follows_old_and_current_consumers_and_identifies_explicit_retarget(tmp_path, request):
    paths = [
        "Assets/Room.unity",
        "Assets/Door.prefab",
        "Assets/Oak.mat",
        "Assets/Old.png",
        "Assets/New.png",
        "Assets/Unrelated.prefab",
        "Assets/Detached.prefab",
        "Assets/Added.prefab",
    ]
    project = tmp_path / "project"
    _assets(project, paths)
    (project / paths[2]).write_bytes(_material(3))
    baseline_commit = _commit(project)
    (project / paths[2]).write_bytes(_material(4))
    (project / paths[1]).write_bytes(b"removed prefab link")
    candidate_commit = _commit(project)
    baseline = _archive_graph(tmp_path, paths, {0: [1, 5], 1: [2, 6], 2: [3], 6: [3]}, "old")
    candidate = _archive_graph(tmp_path, paths, {0: [1, 5], 1: [2, 7], 2: [4]}, "new")
    old, new = tmp_path / "baseline.db", tmp_path / "candidate.db"
    _ok(_scan(project, baseline_commit, paths[0], old, baseline))
    _ok(_scan(project, candidate_commit, paths[0], new, candidate))
    output = tmp_path / "diff.jsonl"
    started = perf_counter()
    _ok(
        _run(
            "dependency-diff",
            "--baseline",
            str(old),
            "--candidate",
            str(new),
            "--output",
            str(output),
        )
    )
    seconds = perf_counter() - started
    header, *rows = [json.loads(line) for line in output.read_bytes().splitlines()]
    assert header["baseline"]["commit"] == baseline_commit
    assert header["candidate"]["commit"] == candidate_commit
    assert header["baseline"]["graph_commit_binding"] == "unverified"
    changes = {
        (r["owner_path"], r["target_path"], r["change"]) for r in rows if r["kind"] == "relation"
    }
    assert changes == {
        (paths[1], paths[6], "removed"),
        (paths[1], paths[7], "added"),
        (paths[2], paths[3], "removed"),
        (paths[2], paths[4], "added"),
        (paths[6], paths[3], "left_closure"),
    }
    (retarget,) = [r for r in rows if r["kind"] == "retargeted"]
    assert retarget["field"] == ["21", "2100000", "Material", "m_Texture"]
    assert (retarget["baseline_target_path"], retarget["candidate_target_path"]) == (
        paths[3],
        paths[4],
    )
    consumers = {(r["side"], r["owner_path"], r["path"]) for r in rows if r["kind"] == "consumer"}
    assert ("baseline", paths[6], paths[0]) in consumers
    assert ("baseline", paths[6], paths[1]) in consumers
    assert ("candidate", paths[2], paths[0]) in consumers
    assert not any(path == paths[5] for _, _, path in consumers)
    request.node.user_properties.append(
        (
            "dependency_diff_observation",
            json.dumps(
                {
                    "seconds": round(seconds, 6),
                    "baseline": header["baseline"],
                    "candidate": header["candidate"],
                    "relations": sorted(changes),
                    "retarget": retarget,
                    "consumers": sorted(consumers),
                    "output_sha256": sha256(output.read_bytes()).hexdigest(),
                },
                sort_keys=True,
            ),
        )
    )


def test_policy_hidden_target_is_unknown_but_other_visible_changes_remain_comparable(tmp_path):
    paths = [
        "Assets/Main.unity",
        "Assets/Unchanged.mat",
        "Assets/Hidden.png",
        "Assets/Visible.png",
        "Assets/Changed.mat",
    ]
    project = tmp_path / "project"
    _assets(project, paths)
    before = _commit(project)
    (project / paths[4]).write_bytes(b"removed the visible texture")
    after = _commit(project)
    old_pages = _archive_graph(tmp_path, paths, {0: [1, 3, 4], 1: [2], 4: [3]}, "old")
    # Publish the same outgoing native dependency from Unchanged.mat. The
    # effective collection policy filters its target before the query can return it.
    new_pages = _archive_graph(tmp_path, paths, {0: [1, 3, 4], 1: [2]}, "new", excluded=[paths[2]])
    old, new = tmp_path / "old.db", tmp_path / "new.db"
    assert _ok(_scan(project, before, paths[0], old, old_pages))["native_asset_catalog_unfiltered"]
    assert not _ok(_scan(project, after, paths[0], new, new_pages))[
        "native_asset_catalog_unfiltered"
    ]
    out = tmp_path / "diff.jsonl"
    _ok(
        _run(
            "dependency-diff", "--baseline", str(old), "--candidate", str(new), "--output", str(out)
        )
    )
    rows = [json.loads(line) for line in out.read_bytes().splitlines()]
    assert {(r["target_path"], r["change"]) for r in rows if r["kind"] == "relation"} == {
        (paths[2], "target_visibility_unknown"),
        (paths[3], "removed"),
    }
    assert not any(r["kind"] == "consumer" and r["owner_path"] == paths[1] for r in rows)


def _large_pages(folder, paths):
    scope, query = "a" * 64, "b" * 32
    size = len(paths)
    pages = []
    for start in range(0, size, 25):
        rows = []
        for owner in range(start, min(start + 25, size)):
            for distance in range(1, 6):
                target = (owner + distance) % size
                rows.append(
                    {
                        "id": f"{owner}-{target}",
                        "owner": _node(owner),
                        "target": _node(target),
                        "source_path": paths[owner],
                        "target_path": paths[target],
                        "provider": "unity",
                        "kind": "unity_dependency",
                        "confidence": "observed",
                        "target_exists": True,
                        "stale": False,
                    }
                )
        sequence = len(pages) + 1
        page = {
            "scope": scope,
            "revision": 19,
            "query_id": query,
            "node": _node(0),
            "page_sequence": sequence,
            "direction": "outgoing",
            "max_depth": None,
            "graph_status": {"native_current": True},
            "edges": rows,
            "next_cursor": encode_cursor(scope, {"id": query, "sequence": sequence})
            if start + 25 < size
            else None,
        }
        pages.append(_save(folder, f"page-{sequence}", page))
    return pages


def test_interrupted_1000_assets_5000_links_equals_uninterrupted_inventory(tmp_path, request):
    # Controlled retained pages exercise cyclic graphs, including links that are
    # absent from the BFS discovery tree. The source bytes and Git objects are real.
    project = tmp_path / "project"
    paths = [f"Assets/Item-{index:04d}.asset" for index in range(1000)]
    _assets(project, paths)
    commit = _commit(project)
    pages = _large_pages(tmp_path, paths)
    uninterrupted, resumed = tmp_path / "all.db", tmp_path / "resumed.db"
    out_all, out_resumed = tmp_path / "all.jsonl", tmp_path / "resumed.jsonl"
    started = perf_counter()
    first = _ok(_scan(project, commit, paths[0], uninterrupted, pages, out_all))
    uninterrupted_seconds = perf_counter() - started
    started = perf_counter()
    partial = _ok(_scan(project, commit, paths[0], resumed, pages[:19]))
    partial_seconds = perf_counter() - started
    assert partial["state"] == "awaiting_page" and partial["reachable_assets"] is None
    compared = _run(
        "dependency-diff",
        "--baseline",
        str(resumed),
        "--candidate",
        str(uninterrupted),
        "--output",
        str(tmp_path / "partial.jsonl"),
    )
    assert compared.exit_code and "resume baseline" in compared.output
    assert not (tmp_path / "partial.jsonl").exists()

    page = json.loads(pages[19].read_bytes())["data"]
    for changed, error in (
        ({**page, "revision": 20}, "recollect this root"),
        ({**page, "graph_status": {"native_current": False}}, "refresh and recollect"),
        ({**page, "edges": [*page["edges"], page["edges"][-1]]}, "repeated_graph_edge"),
    ):
        invalid = _save(tmp_path, "invalid", changed)
        rejected = _scan(project, commit, paths[0], resumed, [invalid])
        assert rejected.exit_code and error in rejected.output
    skipped = _scan(project, commit, paths[0], resumed, [pages[20]])
    assert skipped.exit_code and "sequence_gap" in skipped.output
    assert _ok(_scan(project, commit, paths[0], resumed)) == partial
    started = perf_counter()
    second = _ok(_scan(project, commit, paths[0], resumed, pages[18:], out_resumed))
    resumed_seconds = partial_seconds + perf_counter() - started
    assert second == first
    assert second["inventory"]["sha256"] == sha256(out_all.read_bytes()).hexdigest()
    assert out_all.read_bytes() == out_resumed.read_bytes()
    assert first["scanned_edges"] == 5000 and first["reachable_assets"] == 1000
    assert first["inventory"]["rows"] == 6001
    expected_hashes = {
        name: sha256((project / name).read_bytes()).hexdigest()
        for path in paths
        for name in (path, path + ".meta")
    }
    assert first["closure_source_revision"] == source_revision(expected_hashes)
    request.node.user_properties.extend(
        (
            ("fixture_commit", commit),
            ("closure_source_revision", first["closure_source_revision"]),
            ("reachable_assets", first["reachable_assets"]),
            ("scanned_edges", first["scanned_edges"]),
            ("pages", first["pages"]),
            ("interrupted_after_pages", partial["pages"]),
            ("uninterrupted_inventory_sha256", first["inventory"]["sha256"]),
            ("resumed_inventory_sha256", second["inventory"]["sha256"]),
            ("inventory_bytes", out_all.stat().st_size),
            ("inventory_rows", first["inventory"]["rows"]),
            ("uninterrupted_seconds", round(uninterrupted_seconds, 3)),
            ("two_scan_calls_seconds", round(resumed_seconds, 3)),
        )
    )
