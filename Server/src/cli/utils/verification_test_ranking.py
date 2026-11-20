"""Rank exact NUnit checks using committed changes, mapped scenes and prior outcomes."""

import re
from collections import defaultdict

from .play_scenario_analysis_io import AnalysisError
from .verification_test_budget import MAX_HISTORY, MAX_RANKED

OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def items(document, key, limit):
    rows = document.get(key) if isinstance(document, dict) else None
    if not isinstance(rows, list) or len(rows) > limit:
        raise AnalysisError(f"Expected at most {limit} entries in {key}")
    return rows


def label(value, field, limit=512):
    if not isinstance(value, str) or not 0 < len(value) <= limit or value.strip() != value:
        raise AnalysisError(f"Invalid {field}")
    return value


def propose_ranking(draft, mappings, closures, history):
    if (
        draft.get("kind") != "git_candidate_draft"
        or draft.get("selection") != "committed"
        or not all(
            OID.fullmatch(str(draft.get(k))) for k in ("candidate_commit", "baseline_commit")
        )
        or not isinstance(draft.get("candidate"), dict)
    ):
        raise AnalysisError("Expected a committed Git candidate draft")
    revision = label(draft["candidate"].get("source_revision"), "candidate source revision")
    if not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise AnalysisError("Expected a candidate source SHA-256")
    changes = set()
    for row in items(draft, "changed_paths", 4096):
        changes.add(label(row.get("path") if isinstance(row, dict) else None, "changed path"))

    if len(closures) > 32 or len(history) > MAX_HISTORY:
        raise AnalysisError("At most 32 scene closures and 16 prior executions are supported")
    if len({run.fixture.context for run in history}) > 1 or len(
        {run.fixture.execution_id for run in history}
    ) != len(history):
        raise AnalysisError("Prior runs must be independent and share one execution context")
    by_path = defaultdict(set)
    for row in items(mappings, "mappings", 4096):
        path = label(row.get("path") if isinstance(row, dict) else None, "mapping path")
        for name in items(row, "tests", MAX_RANKED) if "tests" in row else []:
            by_path[path].add(label(name, "mapped NUnit name", 1024))

    # A closure traverses outward from its entry scene. Reverse the lookup to find
    # the scene that reaches each changed asset, preserving the original edge receipt.
    scenes = defaultdict(list)
    graph = []
    scene_names = set()
    for closure in closures:
        if (
            closure.get("kind") != "verification_dependency_closure"
            or closure.get("candidate_source_revision") != revision
            or any(closure.get(k) != draft[k] for k in ("candidate_commit", "baseline_commit"))
        ):
            raise AnalysisError("Scene closure and committed draft identities differ")
        scene = label(closure.get("root"), "entry scene")
        if not scene.endswith(".unity"):
            raise AnalysisError("Expected a Unity scene closure root")
        if scene in scene_names:
            raise AnalysisError("Duplicate scene closure would count mapping evidence twice")
        scene_names.add(scene)
        parents = {scene: (None, 0)}
        for edge in items(closure, "traversed_edges", 1024):
            if not isinstance(edge, dict):
                raise AnalysisError("Expected a dependency closure edge")
            target = label(edge.get("path"), "target path")
            owner = label(edge.get("source_path"), "owner path")
            edge_id = label(edge.get("edge_id"), "edge ID", 128)
            if (
                edge.get("provider") != "unity"
                or edge.get("kind") != "unity_dependency"
                or target in parents
                or owner not in parents
                or (edge.get("via"), edge.get("depth"))
                != (parents[owner][0], parents[owner][1] + 1)
            ):
                raise AnalysisError(
                    "Expected an ordered native dependency closure rooted in the scene"
                )
            parents[target] = edge_id, edge["depth"]
            scenes[target].append((scene, edge_id))
        graph.append(
            {
                "scene": scene,
                "graph_scope": closure.get("graph_scope"),
                "graph_revision": closure.get("graph_revision"),
                "coverage": closure.get("coverage"),
                "graph_commit_binding": closure.get("graph_commit_binding"),
                "unresolved": items(closure, "unresolved", 4096),
            }
        )

    evidence = defaultdict(list)
    unmapped = []
    count = 0
    for changed in sorted(changes):
        path = changed.removesuffix(".meta")
        matches = [(changed, None)]
        if path != changed and not by_path.get(changed):
            matches.append((path, None))
        matches.extend(sorted(scenes.get(path, [])))
        mapped = False
        for mapped_path, edge in matches:
            for test in sorted(by_path.get(mapped_path, ())):
                count += 1
                if count > 8192:
                    raise AnalysisError("Ranking exceeds 8192 individual mapping observations")
                evidence[test].append({"changed": changed, "mapping": mapped_path, "edge": edge})
                mapped = True
        if not mapped:
            unmapped.append(changed)

    known = set(evidence)
    for run in history:
        known.update(run.fixture.expected_cases)
    if len(known) > MAX_RANKED:
        raise AnalysisError("Ranking inventory exceeds the budget planner limit")
    rows = []
    for name in sorted(known):
        hits = evidence[name]
        direct = sum(hit["edge"] is None for hit in hits)
        failures = [run.fixture.execution_id for run in history if run.cases.get(name) == "Failed"]
        if hits:
            hit = next((h for h in hits if h["edge"] is None), hits[0])
            reason = f"Changed {hit['changed']} maps via {hit['mapping']}"
            if hit["edge"]:
                reason += f" (scene dependency {hit['edge']})"
        else:
            prior = next(run for run in history if name in run.fixture.expected_cases)
            reason = f"Declared in prior NUnit execution {prior.fixture.execution_id}"
        if failures:
            reason += f"; failed in prior execution {failures[-1]}"
        rows.append(
            (
                (0 if direct else 1 if hits else 2, -len(failures), -direct, -len(hits), name),
                {
                    "name": name,
                    "reason": reason[:512],
                    "mappings": hits,
                    "prior_failed_runs": failures,
                },
            )
        )
    skipped = draft.get("skipped_unsupported_path_count", 0)
    if type(skipped) is not int or skipped < 0:
        raise AnalysisError("Invalid skipped Git path count")
    return {
        "schema": "verification_test_ranking@1",
        "state": "incomplete" if not rows or unmapped or skipped else "proposed",
        "candidate_commit": draft["candidate_commit"],
        "baseline_commit": draft["baseline_commit"],
        "tests": [row for _, row in sorted(rows, key=lambda pair: pair[0])],
        "unmapped_paths": unmapped,
        "skipped_unsupported_path_count": skipped,
        "scene_closures": graph,
        "history": [{"execution_id": r.fixture.execution_id, "sha256": r.digest} for r in history],
    }
