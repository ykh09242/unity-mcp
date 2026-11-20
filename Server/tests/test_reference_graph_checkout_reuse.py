"""Reconcile real A→B→A checkouts with a retained production graph and empty controls."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import platform
import sqlite3
from time import perf_counter

import pytest

from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from services.verification.dependency_slots import serialized_slots
from tests.test_reference_graph_reconcile import Editor, allowed
from tests.test_verification_git_draft import git

MATERIAL = "Assets/Mapped.mat"
KEYS = "Assets/Keys.cs"
LOADER = "Assets/Loader.cs"
RED = "Assets/Resources/Red.asset"
BLUE = "Assets/Resources/Blue.asset"


def make_checkout(project: Path):
    project.mkdir()
    git(project, "init", "--initial-branch=fixture-a")
    (project / ".gitattributes").write_bytes(b"* -text\n")
    (project / "Assets/Resources").mkdir(parents=True)
    paths = [MATERIAL, KEYS, LOADER, RED, BLUE, "Assets/Resources/Control.asset"]
    paths += [f"Assets/Stable{index}.cs" for index in range(32)]
    ids = {path: f"{index + 1:032x}" for index, path in enumerate(paths)}
    for path, guid in ids.items():
        (project / (path + ".meta")).write_bytes(f"guid: {guid}\n".encode())
        (project / path).write_bytes(b"constant fixture\n")
    (project / LOADER).write_bytes(
        b"using UnityEngine; class Loader { void Load() { Resources.Load(Keys.Value); } }\n"
    )
    for index, path in enumerate(paths[6:]):
        (project / path).write_bytes(
            f'using UnityEngine; class Stable{index} {{ void Load() {{ Resources.Load("Control"); }} }}\n'.encode()
        )
    commits = []
    for color, path in (("Red", RED), ("Blue", BLUE)):
        if commits:
            git(project, "checkout", "-b", "fixture-b")
        (project / KEYS).write_bytes(
            f'public class Keys {{ public const string Value = "{color}"; }}\n'.encode()
        )
        (project / MATERIAL).write_bytes(
            b"%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!21 &2100000\nMaterial:\n"
            + f"  m_Texture: {{fileID: 2800000, guid: {ids[path]}, type: 3}}\n".encode()
        )
        git(project, "add", "--all")
        git(project, "commit", "-m", f"reference {color}")
        commits.append(git(project, "rev-parse", "HEAD").decode("ascii").strip())
    assert set(git(project, "diff", "--name-only", "-z", *commits).split(b"\0")[:-1]) == {
        KEYS.encode(),
        MATERIAL.encode(),
    }
    return ids, commits


class CheckoutCollector(Editor):
    """Supply fixture protocol records from the currently checked-out bytes."""

    def load(self, project: Path, ids: dict[str, str]) -> set[str]:
        updated = set()
        for path, guid in ids.items():
            raw = (project / path).read_bytes()
            assert (project / (path + ".meta")).read_bytes() == f"guid: {guid}\n".encode()
            if self.sources.get(guid) == raw:
                continue
            keys = (
                [{"scheme": "resources", "value": Path(path).stem}]
                if path.startswith("Assets/Resources/")
                else []
            )
            self.add(int(guid, 16), path, raw.decode("utf-8"), keys=keys)
            updated.add(path)
        return updated

    async def __call__(self, action, **params):
        page = await super().__call__(action, **params)
        if action == "dependencies" and page.get("path") == MATERIAL:
            page["targets"] = sorted(
                {target[6:] for _, target, _ in serialized_slots(self.sources[params["guid"]])}
            )
        return page


class MeasuredWorkspace(GraphWorkspace):
    hydrated: int = 0

    def documents(self, owners=None):
        rows = super().documents(owners)
        self.hydrated += len(rows)
        return rows


class MeasuredReconciler(Reconciler):
    analysis_ms: float = 0.0

    async def _analysis(self) -> None:
        started = perf_counter()
        try:
            await super()._analysis()
        finally:
            self.analysis_ms = (perf_counter() - started) * 1000


async def measure(engine, workspace, collector):
    workspace.hydrated = 0
    collector.calls.clear()
    started = perf_counter()
    assert await engine.reconcile()
    elapsed = (perf_counter() - started) * 1000
    return {
        "reconcile_ms": elapsed,
        "analysis_ms": engine.analysis_ms,
        "collector_calls": dict(Counter(action for action, _ in collector.calls)),
        "hydrated_rows": workspace.hydrated,
    }


def graph_contents(store):
    """Project all published semantic rows, omitting graph history counters."""
    with store._connect() as db:
        return {
            "nodes": list(map(tuple, db.execute("SELECT * FROM nodes ORDER BY id"))),
            "edges": list(
                map(
                    tuple,
                    db.execute("SELECT owner,provider,target,kind,payload FROM edges ORDER BY id"),
                )
            ),
            "coverage": list(
                map(
                    tuple,
                    db.execute(
                        "SELECT owner,provider,coverage FROM graph_analysis ORDER BY owner,provider"
                    ),
                )
            ),
            "issues": list(
                map(tuple, db.execute("SELECT * FROM graph_issues ORDER BY owner,provider,ordinal"))
            ),
            "observations": list(
                map(
                    tuple,
                    db.execute(
                        "SELECT owner,provider,fingerprint,state,error FROM observations ORDER BY owner,provider"
                    ),
                )
            ),
        }


@pytest.mark.asyncio
async def test_retained_graph_matches_empty_rebuild_at_each_git_checkout(tmp_path, request):
    project = tmp_path / "project"
    ids, (a, b) = make_checkout(project)
    scope = GraphScope("checkout-fixture", "editor", "project")
    incremental_store = GraphStore.open(tmp_path / "retained", scope)
    incremental_workspace = MeasuredWorkspace(incremental_store)
    incremental_collector = CheckoutCollector()
    incremental_collector.inventory_page_size = 256
    incremental = MeasuredReconciler(incremental_workspace, incremental_collector, allowed)
    outcomes = []

    for index, (label, commit, target_path) in enumerate(
        (("A", a, RED), ("B", b, BLUE), ("A", a, RED))
    ):
        git(project, "checkout", "--detach", commit)
        assert git(project, "status", "--porcelain=v1", "-z") == b""
        assert git(project, "rev-parse", "HEAD").decode("ascii").strip() == commit
        changed = incremental_collector.load(project, ids)
        assert changed == ({MATERIAL, KEYS} if index else set(ids))
        warm_measurement = await measure(incremental, incremental_workspace, incremental_collector)

        cold_store = GraphStore.open(tmp_path / f"empty-{index}", scope)
        cold_workspace = MeasuredWorkspace(cold_store)
        cold_collector = CheckoutCollector()
        cold_collector.inventory_page_size = 256
        assert cold_collector.load(project, ids) == set(ids)
        cold = MeasuredReconciler(cold_workspace, cold_collector, allowed)
        cold_measurement = await measure(cold, cold_workspace, cold_collector)
        if index:
            assert warm_measurement["collector_calls"]["dependencies"] == len(changed)
            assert warm_measurement["hydrated_rows"] < cold_measurement["hydrated_rows"]

        retained = graph_contents(incremental_store)
        assert retained == graph_contents(cold_store)
        target = "asset:" + ids[target_path]
        for path, provider in ((MATERIAL, "unity"), (LOADER, "code")):
            owner = "asset:" + ids[path]
            assert {edge[2] for edge in retained["edges"] if edge[:2] == (owner, provider)} == {
                target
            }
        outcomes.append(
            {
                "head": label,
                "commit": commit,
                "changed_paths": sorted(changed),
                "expected_target": target,
                "same_graph_as_empty_rebuild": True,
                "graph_sha256": sha256(json.dumps(retained, sort_keys=True).encode()).hexdigest(),
                "published_edges": len(retained["edges"]),
                "retained": warm_measurement,
                "empty_rebuild": cold_measurement,
            }
        )

    first, middle, last = [row["graph_sha256"] for row in outcomes]
    assert first == last != middle
    request.node.user_properties.append(
        (
            "checkout_reuse_observation",
            json.dumps(
                {
                    "scope": "production server reconciliation with Git-derived fixture collector responses",
                    "platform": platform.platform(),
                    "sqlite": sqlite3.sqlite_version,
                    "assets": len(ids),
                    "fixture_guids_by_path": {
                        path: ids[path] for path in (MATERIAL, KEYS, LOADER, RED, BLUE)
                    },
                    "steps": outcomes,
                },
                sort_keys=True,
            ),
        )
    )
