"""Propose additional committed Unity inputs from retained, outgoing graph evidence."""

from collections import defaultdict, deque
from hashlib import sha256
from pathlib import Path
import re

from cli.utils.play_scenario_analysis_io import safe_local_path
from services.reference_graph.queries import decode_cursor
from services.verification.git_draft import _Git, _blob_ids
from services.verification.graph_commit_evidence import capture_guard, verify_impact_page
from services.verification.input_chunks import (
    MAX_INPUT_PATHS,
    source_revision,
    validate_input_paths,
)

MAX_PAGES = 32
MAX_EDGES = 1024
MAX_ASSETS = 128
NODE = re.compile(r"asset:([0-9a-f]{32})")
DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
GUID_LINE = re.compile(rb"^guid:[ \t]*([a-f0-9]{32})[ \t]*\r?$", re.MULTILINE)
IDENTIFIER = re.compile(r"[a-zA-Z0-9_.:-]{1,128}")


def _asset(path):
    if (
        not isinstance(path, str)
        or len(path) > 512
        or not path.startswith("Assets/")
        or path.endswith(".meta")
    ):
        return False
    try:
        validate_input_paths([path])
        return True
    except ValueError:
        return False


def _page(document):
    if document.get("success") is True:
        document = document.get("data")
    if not isinstance(document, dict):
        raise ValueError("candidate_invalid_graph_page")
    scope, revision = document.get("scope"), document.get("revision")
    if (
        not isinstance(scope, str)
        or not DIGEST.fullmatch(scope)
        or type(revision) is not int
        or revision < 0
    ):
        raise ValueError("candidate_invalid_graph_identity")
    return document, (scope, revision)


def _impact_pages(pages):
    edges = []
    seen = set()
    identity = query = None
    gaps = []
    cursor = None
    origin = None
    for index, document in enumerate(pages):
        page, current = _page(document)
        if index and (identity != current or query != page.get("query_id") or not cursor):
            raise ValueError("candidate_mixed_graph_pages")
        identity, query = current, page.get("query_id")
        if "node" in page:
            if (
                not isinstance(page["node"], str)
                or not NODE.fullmatch(page["node"])
                or (index and page["node"] != origin)
            ):
                raise ValueError("candidate_impact_root_mismatch")
            origin = page["node"]
        if "page_sequence" in page:
            if type(page["page_sequence"]) is not int or page["page_sequence"] != index + 1:
                raise ValueError("candidate_out_of_sequence_graph_pages")
        elif index:
            gaps.append({"reason": "archived_page_sequence_unconfirmed"})
        if page.get("max_depth") is not None or "max_depth" not in page:
            gaps.append({"reason": "impact_depth_limited_or_unknown"})
        rows = page.get("edges")
        if (
            not isinstance(query, str)
            or not re.fullmatch(r"[0-9a-f]{32}", query)
            or page.get("direction") != "outgoing"
            or "path_found" in page
            or not isinstance(rows, list)
            or "next_cursor" not in page
        ):
            raise ValueError("candidate_outgoing_impact_required")
        if len(edges) + len(rows) > MAX_EDGES:
            raise ValueError("candidate_dependency_edge_limit")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("candidate_invalid_graph_edge")
        for row in rows:
            edge_id = row.get("id")
            if not isinstance(edge_id, str) or not IDENTIFIER.fullmatch(edge_id) or edge_id in seen:
                raise ValueError("candidate_invalid_or_repeated_graph_edge")
            seen.add(edge_id)
        edges.extend(rows)
        cursor = page["next_cursor"]
        if cursor is not None:
            position = decode_cursor(identity[0], cursor)
            if position.get("id") != query or position.get("sequence") != index + 1:
                raise ValueError("candidate_out_of_sequence_graph_pages")
        if (
            not isinstance(page.get("graph_status"), dict)
            or page["graph_status"].get("native_current") is not True
        ):
            gaps.append({"reason": "graph_collection_not_current"})
    if cursor:
        gaps.append({"reason": "impact_pages_incomplete"})
    return identity, query, origin, edges, gaps


def _diagnostics(diagnostics, identity, paths):
    gaps = []
    next_offsets = {}
    issue_count = 0
    for node, document in diagnostics:
        page, current = _page(document)
        if current != identity or node not in paths or page.get("node", node) != node:
            raise ValueError("candidate_unrelated_graph_diagnostics")
        issues = page.get("issues")
        if (
            not isinstance(issues, list)
            or issue_count + len(issues) > MAX_EDGES
            or "next_cursor" not in page
        ):
            raise ValueError("candidate_invalid_graph_diagnostics")
        issue_count += len(issues)
        if node in next_offsets and next_offsets[node] is None:
            raise ValueError("candidate_repeated_graph_diagnostics")
        if "offset" in page:
            if type(page["offset"]) is not int or page["offset"] != next_offsets.get(node, 0):
                raise ValueError("candidate_out_of_sequence_diagnostics")
        else:
            gaps.append({"path": paths[node], "reason": "archived_diagnostic_offset_unconfirmed"})
        next_offsets[node] = page.get("next_offset") if page["next_cursor"] else None
        if page["next_cursor"] and (
            type(next_offsets[node]) is not int
            or ("offset" in page and next_offsets[node] != page["offset"] + len(issues))
        ):
            raise ValueError("candidate_invalid_diagnostic_continuation")
        if "node" not in page:
            gaps.append({"path": paths[node], "reason": "archived_diagnostic_owner_unconfirmed"})
        for issue in issues:
            if not isinstance(issue, dict):
                raise ValueError("candidate_invalid_graph_issue")
            reason = issue.get("reason")
            row = {
                "path": paths[node],
                "reason": reason
                if isinstance(reason, str) and IDENTIFIER.fullmatch(reason)
                else "unresolved_graph_issue",
            }
            if type(issue.get("line")) is int and issue["line"] >= 0:
                row["line"] = issue["line"]
            gaps.append(row)
        if page.get("excluded"):
            gaps.append({"path": paths[node], "reason": "diagnostic_coverage_partial"})
        coverage = page.get("coverage")
        if (
            not isinstance(coverage, list)
            or not coverage
            or any(not isinstance(row, dict) or row.get("state") != "ready" for row in coverage)
        ):
            gaps.append({"path": paths[node], "reason": "diagnostic_provider_incomplete"})
    for node, offset in next_offsets.items():
        if offset is not None:
            gaps.append({"path": paths[node], "reason": "diagnostic_coverage_partial"})
    for node in sorted(set(paths) - next_offsets.keys()):
        gaps.append({"path": paths[node], "reason": "diagnostics_not_supplied"})
    return gaps


def propose_dependency_closure(project: Path, evidence, root, pages, diagnostics):
    doc = evidence.document
    base, head = doc.get("baseline_commit"), doc.get("candidate_commit")
    if doc.get("selection") != "committed" or not all(
        isinstance(oid, str) and COMMIT.fullmatch(oid) for oid in (base, head)
    ):
        raise ValueError("candidate_committed_draft_required")
    if not _asset(root) or not 1 <= len(pages) <= MAX_PAGES or len(diagnostics) > MAX_PAGES:
        raise ValueError("candidate_dependency_root_or_page_limit")
    identity, query, origin, edges, gaps = _impact_pages(pages)
    retained = [*pages, *(page for _, page in diagnostics)]
    binding = "unverified"
    if any(capture_guard(page) is not None for page in retained):
        guards = set()
        for page in retained:
            if not verify_impact_page(project, head, page):
                raise ValueError("graph_commit_missing_page_evidence")
            guards.add(capture_guard(page))
        if len(guards) != 1:
            raise ValueError("graph_commit_collection_changed")
        binding = "verified"
    by_owner = defaultdict(list)
    paths = {root}
    for edge in edges:
        owner = edge.get("owner")
        if not isinstance(owner, str) or not NODE.fullmatch(owner):
            gaps.append({"edge_id": edge["id"], "reason": "unsupported_edge_owner"})
            continue
        by_owner[owner].append(edge)
        for path in (edge.get("source_path"), edge.get("target_path")):
            if _asset(path):
                paths.add(path)
    if len(paths) > MAX_ASSETS:
        raise ValueError("candidate_dependency_asset_limit")
    files = sorted(p for path in paths for p in (path, path + ".meta"))
    validate_input_paths(files)
    git = _Git(safe_local_path(project))
    old, new = _blob_ids(git, base, files), _blob_ids(git, head, files)
    hashes = {}
    total = 0
    snapshots = {}

    def snapshot(path):
        nonlocal total
        if path not in snapshots:
            contents = {}
            for name in (path, path + ".meta"):
                if name not in new:
                    break
                data = git.read(
                    "cat-file", "blob", new[name], limit=min(4 * 1024**2, 32 * 1024**2 - total)
                )
                total += len(data)
                contents[name] = sha256(data).hexdigest()
                if name.endswith(".meta"):
                    guids = GUID_LINE.findall(data)
                    if len(guids) == 1:
                        snapshots[path] = ("asset:" + guids[0].decode(), contents)
            snapshots.setdefault(path, (None, {}))
        return snapshots[path]

    def admit(contents):
        for name, digest in contents.items():
            if name in evidence.inputs:
                if evidence.inputs[name] != digest:
                    raise ValueError("candidate_dependency_draft_digest_mismatch")
            elif old.get(name) != new.get(name):
                return False
        hashes.update(
            {name: digest for name, digest in contents.items() if name not in evidence.inputs}
        )
        return True

    root_node, contents = snapshot(root)
    if not root_node or not admit(contents):
        raise ValueError("candidate_dependency_root_mismatch")
    first = [row for row in edges if row.get("depth") == 1]
    if (origin and origin != root_node) or (
        edges and (not first or any(row.get("owner") != root_node for row in first))
    ):
        raise ValueError("candidate_impact_root_mismatch")
    if not edges and not origin:
        gaps.append({"reason": "impact_root_unconfirmed"})
    selected = {root_node: root}
    queue = deque([(root_node, None, 0)])
    proposals = []
    traversed = []
    if root not in evidence.inputs or root + ".meta" not in evidence.inputs:
        proposals.append({"path": root, "guid": root_node[6:], "kind": "explicit_root", "depth": 0})
    while queue:
        owner, parent_edge, depth = queue.popleft()
        for edge in sorted(by_owner.pop(owner, ()), key=lambda row: row["id"]):
            source, target, path = selected[owner], edge.get("target"), edge.get("target_path")
            reason = None
            if edge.get("source_path") != source or edge.get("stale") is not False:
                reason = "stale_or_mismatched_source"
            elif edge.get("target_exists") is not True or not _asset(path):
                reason = "unresolved_or_out_of_scope_target"
            elif (
                edge.get("provider") != "unity"
                or edge.get("kind") != "unity_dependency"
                or edge.get("confidence") != "observed"
            ):
                reason = "unconfirmed_reference"
            else:
                node, contents = snapshot(path)
                if not node or node != target:
                    reason = "target_guid_mismatch"
                elif node in selected and selected[node] != path:
                    raise ValueError("candidate_dependency_ambiguous_guid")
                elif not admit(contents):
                    reason = "changed_dependency_missing_from_draft"
                elif node not in selected:
                    selected[node] = path
                    queue.append((node, edge["id"], depth + 1))
                    traversal = {
                        "path": path,
                        "guid": node[6:],
                        "source_path": source,
                        "edge_id": edge["id"],
                        "via": parent_edge,
                        "depth": depth + 1,
                        "kind": edge["kind"],
                        "provider": edge["provider"],
                    }
                    traversed.append(traversal)
                    if path not in evidence.inputs or path + ".meta" not in evidence.inputs:
                        proposals.append(traversal)
            if reason:
                gaps.append({"path": source, "edge_id": edge["id"], "reason": reason})
    for owner in sorted(by_owner):
        for edge in sorted(by_owner[owner], key=lambda row: row["id"]):
            gaps.append({"owner": owner, "edge_id": edge["id"], "reason": "unreachable_edge_owner"})
    gaps.extend(_diagnostics(diagnostics, identity, selected))
    expanded = {**evidence.inputs, **hashes}
    validate_input_paths(expanded)
    if len(expanded) > MAX_INPUT_PATHS:
        raise ValueError("candidate_dependency_expanded_input_limit")
    return {
        "schema_version": 1,
        "kind": "verification_dependency_closure",
        "baseline_commit": base,
        "candidate_commit": head,
        "candidate_source_revision": source_revision(evidence.inputs),
        "expanded_source_revision": source_revision(expanded),
        "root": root,
        "graph_revision": identity[1],
        "graph_scope": identity[0],
        "query_id": query,
        "coverage": "partial_static",
        "graph_commit_binding": binding,
        "proposed_inputs": proposals,
        "traversed_edges": traversed,
        "input_hashes": hashes,
        "unresolved": gaps,
    }
