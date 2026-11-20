"""Build a committed input draft and consume retained Reference Graph query output."""

import json
from hashlib import sha256
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from cli.main import cli
from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from services.verification.git_draft import git_draft
from services.verification.input_chunks import source_revision
from services.verification.reproduce import _check_closure
from tests.test_verification_git_draft import git


@pytest.mark.parametrize(
    ("extra_owner", "reason"),
    [
        (None, "dynamic_key"),
        ("external:other", "unsupported_edge_owner"),
        ("asset:" + "a" * 32, "unreachable_edge_owner"),
    ],
)
def test_committed_scene_closure_preserves_unresolved_evidence(tmp_path, extra_owner, reason):
    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    paths = ["Assets/Room.unity", "Assets/Door.prefab", "Assets/Door.mat", "Assets/Oak.png"]
    nodes = ["asset:" + f"{index + 1:032x}" for index in range(len(paths))]
    for path, node in zip(paths, nodes, strict=True):
        (project / path).write_bytes(path.encode())
        (project / (path + ".meta")).write_text("guid: " + node[6:] + "\n", encoding="utf-8")
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    git(project, "add", ".")
    git(project, "commit", "-m", "baseline")
    (project / paths[0]).write_bytes(b"changed scene")
    git(project, "add", ".")
    git(project, "commit", "-m", "candidate")
    draft = git_draft(project, "HEAD~1", "committed", candidate_ref="HEAD")
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    store = GraphStore.open(tmp_path / "graphs", GraphScope("fixture", "editor", "project"))
    workspace = GraphWorkspace(store)
    assert workspace.acquire("fixture")
    for index, node in enumerate(nodes):
        version = store.observe(node, "unity", "fixture", path=paths[index])
        edges = [Edge(nodes[index + 1], "unity_dependency")] if index + 1 < len(nodes) else []
        workspace.publish_events(
            "fixture",
            node,
            version,
            edges,
            provider="unity",
            coverage="direct_asset_database_dependencies",
        )
    if extra_owner is None:
        generation = store.observe(nodes[0], "code", "fixture", path=paths[0])
        workspace.publish_events(
            "fixture", nodes[0], generation, [{"reason": "dynamic_key", "line": 25}]
        )
    workspace.release("fixture")
    queries = GraphQueries(store)
    args = [
        "--no-project-context",
        "verification-candidate",
        "dependency-closure",
        "--project",
        str(project),
        "--draft",
        str(draft_path),
        "--root",
        paths[0],
    ]

    def archive(name, page):
        path = tmp_path / (name + ".json")
        data = {**page, "scope": store.scope.key, "graph_status": {"native_current": True}}
        path.write_text(json.dumps({"success": True, "data": data}), encoding="utf-8")
        return str(path)

    cursor = None
    page_files = []
    for index in range(16):
        result = queries.impact(nodes[0], direction="outgoing", limit=1, cursor=cursor)
        if extra_owner and result["edges"] and result["edges"][0]["depth"] == 2:
            result["edges"].append({**result["edges"][0], "owner": extra_owner, "id": "extra-edge"})
        page_files.append(archive(f"page{index}", result))
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert not cursor and len(page_files) >= 3
    diagnostic_args = []
    for index, node in enumerate(nodes):
        diag = {"node": node, **workspace.diagnostics(node)}
        diagnostic_args.extend(["--diagnostics", node, archive(f"issues{index}", diag)])

    def run(pages):
        options = [value for name in pages for value in ("--graph-page", name)]
        return CliRunner().invoke(cli, args + options + diagnostic_args)

    full = run(page_files)
    assert full.exit_code == 0, full.output
    report = json.loads(full.output)
    rows = report["proposed_inputs"]
    assert [row["path"] for row in rows] == paths[1:]
    assert [row["source_path"] for row in rows] == paths[:-1]
    assert [row["via"] for row in rows] == [None, rows[0]["edge_id"], rows[1]["edge_id"]]
    gap = (
        {"path": paths[0], "reason": reason, "line": 25}
        if extra_owner is None
        else {"edge_id": "extra-edge", "reason": reason}
    )
    if reason == "unreachable_edge_owner":
        gap["owner"] = extra_owner
    assert report["unresolved"] == [gap]
    expected = {
        p: sha256((project / p).read_bytes()).hexdigest()
        for asset in paths[1:]
        for p in (asset, asset + ".meta")
    }
    assert report["input_hashes"] == expected
    assert report["expanded_source_revision"] == source_revision(
        {**draft["candidate"]["inputs"], **expected}
    )
    raw = full.output.encode()
    side = SimpleNamespace(
        closure_sha256=sha256(raw).hexdigest(),
        project_commit=report["candidate_commit"],
        source_revision=report["expanded_source_revision"],
    )
    manifest = SimpleNamespace(inputs={**draft["candidate"]["inputs"], **expected})
    with pytest.raises(ValueError, match="^reproduction_dependency_closure_unresolved$"):
        _check_closure(raw, side, manifest)
    assert run(page_files[:1] + page_files[2:]).exit_code != 0
