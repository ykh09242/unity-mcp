"""Verify retained Editor observations against selected Git blobs without a live Editor."""

from hashlib import sha256
from pathlib import Path
import re

from cli.utils.play_scenario_analysis_io import safe_local_path
from services.reference_graph.commit_evidence import COMMIT, page_digest, page_nodes
from services.verification.git_draft import _Git, _blob_ids
from services.verification.input_chunks import validate_input_paths

HEX32 = re.compile(r"[0-9a-f]{32}")
DIGEST = re.compile(r"[0-9a-f]{64}")
GUID_LINE = re.compile(rb"^guid:[ \t]*([0-9a-f]{32})[ \t]*\r?$", re.MULTILINE)


def _page(document):
    if isinstance(document, dict) and document.get("success") is True:
        document = document.get("data")
    if not isinstance(document, dict):
        raise ValueError("graph_commit_invalid_page")
    return document


def capture_guard(document):
    page = _page(document)
    if "commit_evidence" not in page:
        return None
    receipt = page["commit_evidence"]
    if not isinstance(receipt, dict) or not isinstance(receipt.get("before"), dict):
        raise ValueError("graph_commit_invalid_evidence")
    state = receipt["before"]
    return tuple(
        state.get(key)
        for key in (
            "git_head",
            "instance_id",
            "project_id",
            "epoch",
            "revision",
            "build_target",
            "unity_version",
        )
    )


def verify_impact_page(project: Path, commit: str, page: dict) -> bool:
    page = _page(page)
    guard = capture_guard(page)
    if guard is None:
        return False
    receipt = page["commit_evidence"]
    if (
        type(receipt.get("version")) is not int
        or receipt["version"] != 1
        or not isinstance(commit, str)
        or not COMMIT.fullmatch(commit)
        or receipt.get("commit") != commit
        or not isinstance(page.get("scope"), str)
        or not DIGEST.fullmatch(page["scope"])
        or receipt.get("scope") != page["scope"]
        or type(page.get("revision")) is not int
        or page["revision"] < 0
        or type(receipt.get("revision")) is not int
        or receipt["revision"] != page["revision"]
        or receipt.get("page_sha256") != page_digest(page)
    ):
        raise ValueError("graph_commit_page_identity_mismatch")
    before, after = receipt["before"], receipt.get("after")
    if isinstance(after, dict) and before.get("git_head") != after.get("git_head"):
        raise ValueError("graph_commit_checkout_changed_during_capture")
    for state in (before, after):
        if not isinstance(state, dict):
            raise ValueError("graph_commit_missing_observation")
        if type(state.get("dirty_scenes")) is not int or state["dirty_scenes"] < 0:
            raise ValueError("graph_commit_scene_state_unavailable")
        if state["dirty_scenes"]:
            raise ValueError("graph_commit_unsaved_scene")
        if any(
            state.get(key) is not False for key in ("compiling", "importing", "compilation_failed")
        ):
            raise ValueError("graph_commit_import_or_compilation_not_ready")
        if not isinstance(state.get("git_head"), str) or not COMMIT.fullmatch(state["git_head"]):
            raise ValueError("graph_commit_editor_git_head_unavailable")
        if state["git_head"] != commit:
            raise ValueError("graph_commit_editor_checkout_mismatch")
        if (
            type(state.get("revision")) is not int
            or state["revision"] < 0
            or not all(
                isinstance(state.get(k), str) and state[k]
                for k in ("epoch", "instance_id", "project_id", "build_target", "unity_version")
            )
        ):
            raise ValueError("graph_commit_editor_identity_unavailable")
    if before != after:
        raise ValueError("graph_commit_collection_changed")
    if (
        not isinstance(page.get("graph_status"), dict)
        or page["graph_status"].get("native_current") is not True
    ):
        raise ValueError("graph_commit_collection_not_current")
    progress = page["graph_status"].get("progress")
    if (
        not isinstance(progress, dict)
        or progress.get("epoch") != before["epoch"]
        or type(progress.get("after")) is not int
        or progress["after"] != before["revision"]
    ):
        raise ValueError("graph_commit_import_journal_mismatch")

    nodes = page_nodes(page)
    assets, published = before.get("assets"), receipt.get("published")
    if (
        not isinstance(assets, list)
        or len(assets) != len(nodes)
        or not isinstance(published, dict)
        or sorted(published) != nodes
    ):
        raise ValueError("graph_commit_input_set_mismatch")
    hashes, paths = {}, {}
    for node, observed in zip(nodes, assets, strict=True):
        info = published[node]
        if not isinstance(info, dict) or not isinstance(observed, dict):
            raise ValueError("graph_commit_invalid_asset")
        path = observed.get("path")
        if (
            not isinstance(path, str)
            or len(path) > 512
            or not path.startswith("Assets/")
            or path.endswith(".meta")
            or info.get("path") != path
            or observed.get("guid") != node[6:]
            or not isinstance(observed.get("import_hash"), str)
            or not HEX32.fullmatch(observed["import_hash"])
            or observed["import_hash"] == "0" * 32
            or observed["import_hash"] != info.get("import_hash")
        ):
            raise ValueError("graph_commit_published_import_mismatch")
        witness = observed.get("import_witness")
        if not isinstance(witness, dict) or not isinstance(info.get("import_witness"), dict):
            raise ValueError("graph_commit_unobserved_import: " + path)
        if (
            witness != info["import_witness"]
            or not isinstance(witness.get("import_id"), str)
            or not HEX32.fullmatch(witness["import_id"])
            or any(
                witness.get(field) != observed.get(field)
                for field in ("asset_sha256", "meta_sha256", "import_hash", "guid")
            )
        ):
            raise ValueError("graph_commit_imported_source_changed: " + path)
        for field in ("stamp", "meta_stamp"):
            stamp = observed.get(field)
            if (
                not isinstance(stamp, str)
                or not re.fullmatch(r"[0-9]{1,10}:[0-9]{1,19}", stamp)
                or info.get(field) != stamp
            ):
                raise ValueError("graph_commit_collected_file_changed_or_unavailable: " + path)
        paths[node] = path
        for name, field in ((path, "asset_sha256"), (path + ".meta", "meta_sha256")):
            if (
                name in hashes
                or not isinstance(observed.get(field), str)
                or not DIGEST.fullmatch(observed[field])
            ):
                raise ValueError("graph_commit_invalid_source_digest")
            hashes[name] = observed[field]
    validate_input_paths(hashes)
    links = receipt.get("native_links")
    if (
        not isinstance(links, dict)
        or set(links) != set(paths.values())
        or any(
            not isinstance(targets, list)
            or len(targets) > len(paths)
            or not all(isinstance(target, str) and target in links for target in targets)
            for targets in links.values()
        )
    ):
        raise ValueError("graph_commit_missing_native_dependency_observation")
    for edge in page.get("edges", []):
        if paths[edge["owner"]] != edge.get("source_path") or paths[edge["target"]] != edge.get(
            "target_path"
        ):
            raise ValueError("graph_commit_edge_path_mismatch")
        if (
            edge.get("provider") == "unity"
            and edge.get("kind") == "unity_dependency"
            and edge.get("target_path") not in links[edge["source_path"]]
        ):
            raise ValueError("graph_commit_native_dependency_changed")
    git = _Git(safe_local_path(project))
    blobs = _blob_ids(git, commit, sorted(hashes))
    if blobs.keys() != hashes.keys():
        raise ValueError("graph_commit_input_missing_from_commit")
    meta_guids = {path + ".meta": node[6:].encode("ascii") for node, path in paths.items()}
    with git.blobs(blobs) as contents:
        for name, data in contents:
            if sha256(data).hexdigest() != hashes[name]:
                raise ValueError("graph_commit_source_bytes_mismatch: " + name)
            if name in meta_guids and GUID_LINE.findall(data) != [meta_guids[name]]:
                raise ValueError("graph_commit_metadata_guid_mismatch: " + name)
    return True
