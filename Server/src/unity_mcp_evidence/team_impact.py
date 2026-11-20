"""Share a scoped asset change, its native scene dependency, and selected NUnit cases."""

import re
from collections import defaultdict, deque
from itertools import pairwise

from .play_scenario_analysis_io import AnalysisError
from .play_scenario_report_json import parse_report
from .team_projection import Aliases, choice, digest, fields, items, opaque
from .verification_selection import Cohort

GUID = re.compile(r"[0-9a-f]{32}")
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
MAPPING_CHUNK_BYTES = 64 * 1024
MAX_MAPPING_CHUNKS = 8


def guid(value: str) -> str:
    if not isinstance(value, str) or not GUID.fullmatch(value):
        raise ValueError("Expected a lowercase Unity asset GUID")
    return value


def asset_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("Assets/")
        or len(value) > 512
        or any(c in value for c in "\\:\r\n\0")
        or set(value.split("/")) & {"", ".", ".."}
        or value.endswith(".meta")
    ):
        raise ValueError("Expected an Assets-relative path from the evidence")
    return value


def _route(root, target, links):
    pending = deque([root])
    parents = {root: None}
    outgoing = defaultdict(list)
    for owner, child in links:
        outgoing[owner].append(child)
    while pending and target not in parents:
        owner = pending.popleft()
        for child in outgoing[owner]:
            if child not in parents:
                parents[child] = owner
                pending.append(child)
    if target not in parents:
        return []
    result = [target]
    while (parent := parents[result[-1]]) is not None:
        result.append(parent)
    return result[::-1]


def _source_revision(inputs):
    return digest("".join(f"{path}\n{sha}\n" for path, sha in sorted(inputs.items())).encode())


def project_inventory(raw: bytes, subject: str, aliases: Aliases, *, include_route: bool) -> dict:
    """Check the original inventory binding and retain only the chosen route's inputs."""
    lines = iter(raw.splitlines())
    header = parse_report(next(lines, b"{}"))
    commit = header.get("commit")
    if (
        type(header.get("schema_version")) is not int
        or header["schema_version"] != 1
        or header.get("kind") != "verification_dependency_scan"
        or header.get("state") != "complete"
        or header.get("next_cursor") is not None
        or "next_cursor" not in header
        or not isinstance(commit, str)
        or not COMMIT.fullmatch(commit)
    ):
        raise ValueError("Expected a complete dependency-scan JSONL inventory")
    root = _node(header.get("root_node"))
    root_path = asset_path(header.get("root"))
    if not root_path.lower().endswith(".unity") or root == subject:
        raise ValueError("Expected a consuming scene root distinct from the changed asset")
    assets, paths, edges, edge_ids = {}, set(), [], set()
    for line in lines:
        row = parse_report(line)
        match row.get("kind"):
            case "asset":
                node, path = _node(row.get("node")), asset_path(row.get("path"))
                if node in assets or path in paths:
                    raise ValueError("Ambiguous dependency inventory asset")
                paths.add(path)
                assets[node] = (path, opaque(row.get("source_hash")), opaque(row.get("meta_hash")))
            case "dependency":
                edge_id = row.get("id")
                if not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids:
                    raise ValueError("Repeated or invalid dependency observation")
                edge_ids.add(edge_id)
                edges.append((_node(row.get("owner")), _node(row.get("target"))))
            case "unresolved":
                continue
            case _:
                raise ValueError("Unknown dependency inventory row")
    inputs = {
        name: sha
        for path, blob, meta in assets.values()
        for name, sha in ((path, blob), (path + ".meta", meta))
    }
    revision = _source_revision(inputs)
    if (
        len(assets) != header.get("reachable_assets")
        or root not in assets
        or assets[root][0] != root_path
        or revision != header.get("closure_source_revision")
        or any(owner not in assets or target not in assets for owner, target in edges)
    ):
        raise ValueError("Dependency inventory does not match its recorded input closure")
    route = _route(root, subject, sorted(edges)) if include_route else []
    endpoints = set(route) | ({root, subject} & assets.keys())
    return {
        "commit": aliases.token("commit", commit),
        "scope": aliases.token("graph-scope", opaque(header.get("graph_scope"))),
        "query": aliases.token("graph-query", guid(header.get("query_id"))),
        "tree": aliases.token("source", revision),
        "commit_bound": choice(header.get("graph_commit_binding"), {"verified", "unverified"})
        == "verified",
        "scene": aliases.token("guid", root),
        "assets": {
            aliases.token("guid", node): [
                aliases.token("asset", assets[node][0]),
                aliases.token("blob", assets[node][1]),
                aliases.token("blob", assets[node][2]),
            ]
            for node in endpoints
        },
        "dependencies": [
            [aliases.token("guid", owner), aliases.token("guid", child)]
            for owner, child in pairwise(route)
        ],
    }


def _node(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("asset:"):
        raise ValueError("Expected an asset node in the dependency inventory")
    return guid(value.removeprefix("asset:"))


def _manifest_inputs(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 128:
        raise ValueError("Candidate manifest input table is invalid")
    for path, sha in value.items():
        if len(path) > 512 or any(c in path for c in "\r\n\0"):
            raise ValueError("Candidate manifest contains an invalid input path")
        opaque(sha)
    if len(value) != len({path.casefold() for path in value}):
        raise ValueError("Candidate manifest contains repeated input paths")
    return value


def _mapping_inputs(manifest, chunks):
    if manifest["schema_version"] == 1:
        if chunks:
            raise ValueError("Version-one candidate manifest has inline inputs")
        return _manifest_inputs(manifest.get("inputs"))
    refs = items(manifest.get("input_chunks"))
    if not 1 <= len(refs) <= MAX_MAPPING_CHUNKS:
        raise ValueError("Invalid candidate input chunk inventory")
    expected = {}
    for ref in refs:
        fields(ref, ("sha256", "count", "size_bytes"))
        sha = opaque(ref["sha256"])
        if (
            sha in expected
            or type(ref["count"]) is not int
            or not 1 <= ref["count"] <= 128
            or type(ref["size_bytes"]) is not int
            or not 1 <= ref["size_bytes"] <= MAPPING_CHUNK_BYTES
        ):
            raise ValueError("Invalid candidate input chunk reference")
        expected[sha] = (ref["count"], ref["size_bytes"])
    inputs, seen = {}, set()
    for chunk in chunks:
        sha = digest(chunk)
        rows = _manifest_inputs(fields(parse_report(chunk), ("inputs",))["inputs"])
        if sha in seen or expected.get(sha) != (len(rows), len(chunk)):
            raise ValueError("Candidate input chunk differs from its manifest reference")
        if {path.casefold() for path in inputs} & {path.casefold() for path in rows}:
            raise ValueError("Candidate manifest contains repeated input paths")
        seen.add(sha)
        inputs.update(rows)
    return inputs if seen == set(expected) else None


def project_mappings(raw: list[bytes], aliases: Aliases) -> dict:
    """Project the declared scene-to-test mappings from a candidate manifest."""
    manifest = parse_report(raw[0])
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] not in (1, 2):
        raise ValueError("Expected a Player candidate manifest with explicit asset mappings")
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise AnalysisError("Candidate manifest lacks asset identities")
    revision = opaque(manifest.get("source_revision"))
    inputs = _mapping_inputs(manifest, raw[1:])
    if inputs is not None and _source_revision(inputs) != revision:
        raise ValueError("Candidate manifest source revision differs from its input hashes")
    source_assets = {}
    seen = set()
    for path, node in assets.items():
        node = guid(node)
        if node in seen:
            raise ValueError("Ambiguous asset identity in candidate manifest")
        seen.add(node)
        if inputs is None:
            continue
        if path not in inputs or path + ".meta" not in inputs:
            raise ValueError("Candidate asset lacks captured source or metadata")
        source_assets[aliases.token("guid", node)] = [
            aliases.token("asset", path),
            aliases.token("blob", inputs[path]),
            aliases.token("blob", inputs[path + ".meta"]),
        ]
    scenes = {}
    for row in items(manifest.get("mappings", [])):
        fields(row, ("path",), ("tests", "scenarios"))
        path = row["path"]
        if not isinstance(path, str):
            raise AnalysisError("Invalid path in candidate mapping")
        if (
            not path.startswith("Assets/")
            or not path.lower().endswith(".unity")
            or path not in assets
        ):
            continue
        asset_path(path)
        key = aliases.token("guid", guid(assets[path]))
        scene_path = aliases.token("asset", path)
        if key in scenes and scenes[key][0] != scene_path:
            raise ValueError("Ambiguous scene identity in candidate manifest")
        tests = scenes.setdefault(key, (scene_path, set()))[1]
        for name in items(row.get("tests", [])):
            if not isinstance(name, str) or not name.strip() or len(name) > 512:
                raise ValueError("Invalid scene test mapping")
            tests.add(aliases.token("test", name))
    return {
        "source": aliases.token("source", revision),
        "scenes": {key: [path, sorted(tests)] for key, (path, tests) in scenes.items()},
        "assets": source_assets,
    }


def project_selected(cohort: Cohort, aliases: Aliases) -> dict:
    """Retain the reported cases and their shared fixture input fingerprints."""
    return {
        "execution": aliases.token("execution", cohort.fixture.execution_id),
        "inputs": {
            aliases.token("asset", path): [aliases.token("guid", node), aliases.token("blob", blob)]
            for path, (node, blob) in cohort.fixture.inputs.items()
        },
        "cases": {aliases.token("test", name): state for name, state in cohort.cases.items()},
    }


def limit_impact(inputs: dict, subject: str) -> None:
    """Keep only the scene's mapping and selected case/input observations."""
    graph = inputs.get("candidate") or inputs.get("baseline")
    scene = graph["scene"] if graph else None
    mapping, selected = inputs.get("mappings"), inputs.get("selected")
    if mapping is not None:
        scenes = mapping["scenes"]
        mapping["scenes"] = {scene: scenes[scene]} if scene in scenes else {}
        mapping["assets"] = {
            key: value for key, value in mapping["assets"].items() if key in (scene, subject)
        }
    if selected is not None:
        paths = {
            asset[0]
            for key, asset in (graph["assets"].items() if graph else ())
            if key in (scene, subject)
        }
        cases = set(mapping["scenes"].get(scene, (None, []))[1]) if mapping else set()
        selected["inputs"] = {k: v for k, v in selected["inputs"].items() if k in paths}
        selected["cases"] = {k: v for k, v in selected["cases"].items() if k in cases}


def _identity_rows(value, length):
    if not isinstance(value, dict):
        raise AnalysisError("Expected an identity table in shared impact evidence")
    for key, row in value.items():
        opaque(key)
        if len(items(row)) != length:
            raise ValueError("Invalid shared impact identity")
    return value


def restore_impact(role: str, row: dict) -> dict:
    """Validate the redacted shape before evaluating joins on the teammate's machine."""
    if role in ("baseline", "candidate"):
        fields(
            row,
            ("commit", "scope", "query", "tree", "commit_bound", "scene", "assets", "dependencies"),
        )
        for key in ("commit", "scope", "query", "tree", "scene"):
            opaque(row[key])
        assets = _identity_rows(row["assets"], 3)
        if type(row["commit_bound"]) is not bool or row["scene"] not in assets:
            raise ValueError("Invalid scene dependency provenance")
        for values in assets.values():
            for token in values:
                opaque(token)
        for edge in items(row["dependencies"]):
            if len(items(edge)) != 2 or any(opaque(node) not in assets for node in edge):
                raise ValueError("Dependency endpoint lacks a retained observation")
    elif role == "mappings":
        fields(row, ("source", "scenes", "assets"))
        opaque(row["source"])
        for path, tests in _identity_rows(row["scenes"], 2).values():
            opaque(path)
            for token in items(tests):
                opaque(token)
        for values in _identity_rows(row["assets"], 3).values():
            for token in values:
                opaque(token)
    else:
        fields(row, ("execution", "inputs", "cases"))
        opaque(row["execution"])
        for values in _identity_rows(row["inputs"], 2).values():
            for token in values:
                opaque(token)
        if not isinstance(row["cases"], dict):
            raise ValueError("Invalid selected test case observations")
        for name, state in row["cases"].items():
            opaque(name)
            choice(state, {"Passed", "Failed", "Skipped", "Inconclusive"})
    return row


def assess_impact(inputs: dict, subject: str) -> dict:
    before, after, mappings, selected = (
        inputs[k] for k in ("baseline", "candidate", "mappings", "selected")
    )
    asset = after["assets"].get(subject) if after else None
    prior = before["assets"].get(subject) if before else None
    changed = (
        before is not None
        and after is not None
        and before["commit_bound"]
        and after["commit_bound"]
        and before["scope"] == after["scope"]
        and before["scene"] == after["scene"]
        and before["commit"] != after["commit"]
        and before["query"] != after["query"]
        and asset is not None
        and prior is not None
    )
    scene = after["scene"] if after else None
    route = (
        _route(scene, subject, after["dependencies"])
        if after and after["commit_bound"] and scene != subject
        else []
    )
    scene_input = after["assets"][scene] if route else None
    mapped = mappings["scenes"].get(scene) if mappings and route else None
    tests, test_gap = None, None
    if not route:
        test_gap = "consuming_scene_dependency_required"
    elif not mappings or not mapped:
        test_gap = "scene_test_mapping_required"
    elif (
        mapped[0] != scene_input[0]
        or mappings["assets"].get(scene) != scene_input
        or mappings["assets"].get(subject) != asset
    ):
        test_gap = "mapping_source_does_not_confirm_candidate_scene_and_asset"
    elif not selected:
        test_gap = "selected_nunit_capture_required"
    elif selected["inputs"].get(scene_input[0]) != [scene, scene_input[1]] or selected[
        "inputs"
    ].get(asset[0]) != [subject, asset[1]]:
        test_gap = "selected_fixture_input_identity_mismatch"
    else:
        tests = {
            name: selected["cases"][name]
            for name in mapped[1]
            if selected["cases"].get(name) in {"Passed", "Failed"}
        }
    gaps = {}
    if not changed:
        gaps["asset_change"] = "comparable_commit_bound_asset_observations_required"
    if not route:
        gaps["consuming_scene"] = "commit_bound_scene_to_asset_dependency_required"
    if test_gap:
        gaps["selected_tests"] = test_gap
    change = prior != asset if changed else None
    return {
        "state": "incomplete" if gaps else "reconstructed",
        "asset": subject,
        "asset_changed": change,
        "consuming_scene": scene if route else None,
        "scene_dependency": route or None,
        "selected_tests": tests,
        "provenance": {
            "baseline_commit": before["commit"] if before else None,
            "candidate_commit": after["commit"] if after else None,
            "candidate_graph_query": after["query"] if after else None,
            "mapping_source_revision": mappings["source"] if mappings else None,
            "selected_execution": selected["execution"] if selected else None,
        },
        "unavailable_conclusions": gaps,
    }
