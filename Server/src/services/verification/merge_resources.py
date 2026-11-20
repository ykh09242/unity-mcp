"""Compare static Unity Resources lookups in immutable Git trees."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from services.reference_graph.analysis import AnalysisIndex, Catalog, Edge, parse_csharp
from services.verification.git_draft import _GUID, _OID, _eligible, _records
from services.verification.player_runner import _resource_key


_FILES_LIMIT = 4096
_FINDINGS_LIMIT = 1024
_LOOKUPS = {
    prefix + method
    for prefix in ("Resources.", "UnityEngine.Resources.")
    for method in ("Load", "LoadAsync", "LoadAll")
}
_FAILURES = {"key_not_found", "ambiguous_key"}


@dataclass(frozen=True, slots=True)
class _Snapshot:
    failures: dict[tuple[str, str, str], dict]
    catalog: Catalog
    asset_paths: dict[str, str]
    scripts: int
    resolved: int


def _tree_files(git, tree, prefix):
    marker = prefix + "ProjectSettings/ProjectVersion.txt"
    roots = git.read("--literal-pathspecs", "ls-tree", "-z", tree, "--", prefix + "Assets", marker)
    root_modes = {
        path: header.split(b" ", 1)[0]
        for header, path in (record.split(b"\t", 1) for record in _records(roots))
    }
    if root_modes.get((prefix + "Assets").encode()) != b"040000" or root_modes.get(
        marker.encode()
    ) not in {b"100644", b"100755"}:
        raise ValueError("candidate_anticipated_merge_project_scope_missing: " + tree)
    records = git.read(
        "--literal-pathspecs",
        "ls-tree",
        "-r",
        "-z",
        tree,
        "--",
        prefix + "Assets",
        prefix + "Packages",
    )
    files = {}
    for record in _records(records):
        header, raw_path = record.split(b"\t", 1)
        path = raw_path.decode("utf-8").removeprefix(prefix)
        parts = path.split("/")
        if (
            parts[0] not in {"Assets", "Packages"}
            or any(part.startswith(".") or part.endswith("~") or part == "Editor" for part in parts)
            or "/assets/resources/gamedata/" in f"/{path.casefold()}/"
        ):
            continue
        mode, _, oid = header.split(b" ")
        files[path] = (mode, oid)
    return files


def _inspect(git, tree, prefix):
    files = _tree_files(git, tree, prefix)
    scripts, resource_metadata = set(), {}
    for path in files:
        extension = PurePosixPath(path).suffix.lower()
        if extension == ".cs" and _eligible(path):
            scripts.add(path)
        elif extension not in {".meta", ".cs", ".asmdef", ".asmref"} and (
            key := _resource_key(path)
        ):
            metadata = path + ".meta"
            if metadata not in files:
                raise ValueError(f"candidate_anticipated_merge_missing_resource_metadata: {path}")
            resource_metadata[metadata] = (path, key)

    selected = scripts | resource_metadata.keys()
    if len(selected) > _FILES_LIMIT or len({path.casefold() for path in selected}) != len(selected):
        raise ValueError("candidate_anticipated_merge_source_limit_or_collision")
    blobs = {}
    for path in selected:
        mode, oid = files[path]
        if path in resource_metadata:
            asset_path, _ = resource_metadata[path]
            if files[asset_path][0] not in {b"100644", b"100755"}:
                raise ValueError(f"candidate_anticipated_merge_nonregular_input: {asset_path}")
        if mode not in {b"100644", b"100755"} or not _OID.fullmatch(oid):
            raise ValueError(f"candidate_anticipated_merge_nonregular_input: {path}")
        blobs[path] = oid.decode("ascii")

    def check():
        git.remaining()

    facts, assets = [], []
    asset_paths = {}
    with git.blobs(blobs) as contents:
        for path, raw in contents:
            if path in scripts:
                try:
                    source = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise ValueError(
                        f"candidate_anticipated_merge_source_encoding: {path}"
                    ) from None
                facts.append(parse_csharp(path, path, source, check=check))
                continue
            asset_path, key = resource_metadata[path]
            guids = _GUID.findall(raw)
            if len(guids) != 1:
                raise ValueError(f"candidate_anticipated_merge_invalid_resource_metadata: {path}")
            guid = guids[0].decode("ascii")
            if guid in asset_paths:
                raise ValueError(f"candidate_anticipated_merge_duplicate_resource_guid: {path}")
            asset_paths[guid] = asset_path
            assets.append(
                {"guid": guid, "path": asset_path, "keys": [{"scheme": "resources", "value": key}]}
            )

    catalog = Catalog(assets)
    index = AnalysisIndex(facts, catalog, check=check)
    failures = {}
    resolved = 0
    for path in sorted(scripts):
        for event in index.events(path):
            if isinstance(event, Edge):
                resolved += event.kind == "resources_load"
                continue
            if (
                event.get("reason") not in _FAILURES
                or event.get("call", "").removeprefix("global::") not in _LOOKUPS
            ):
                continue
            identity = (path, Catalog.canonical_key("resources", event["key"]), event["reason"])
            failures.setdefault(identity, {"path": path, "blob": blobs[path], **event})
            if len(failures) > _FINDINGS_LIMIT:
                raise ValueError("candidate_anticipated_merge_finding_limit")
    return _Snapshot(failures, catalog, asset_paths, len(scripts), resolved)


def _matching_providers(snapshot, row, check):
    key = Catalog.canonical_key("resources", row["key"])
    if row["call"].endswith(".LoadAll"):
        targets, _ = snapshot.catalog.resolve("resources", key, "all", visit=check)
    else:
        # Catalog.resolve rejects an ambiguous single lookup. Retain all of its
        # exact-key providers so the receipt can explain the ambiguity.
        targets = snapshot.catalog.keys.get(("resources", key), ())
    guids = (target.removeprefix("asset:") for target in targets)
    return {(guid, snapshot.asset_paths[guid]) for guid in guids}


def compare_resources(git, trees, prefix):
    snapshots = {}
    for tree in dict.fromkeys(trees.values()):
        snapshots[tree] = _inspect(git, tree, prefix)
    base, target, head, merged = (
        snapshots[trees[name]] for name in ("base", "target", "head", "merged")
    )
    introduced = merged.failures.keys() - (target.failures.keys() | head.failures.keys())
    regressions = []
    for identity in sorted(introduced):
        row = merged.failures[identity]
        former = {
            pair
            for snapshot in (base, target, head)
            for pair in _matching_providers(snapshot, row, git.remaining)
        }
        regressions.append(
            {
                **row,
                "previous_providers": [
                    {"guid": guid, "path": path, "merged_path": merged.asset_paths.get(guid)}
                    for guid, path in sorted(former)
                ],
                "merged_providers": [
                    {"guid": guid, "path": path}
                    for guid, path in sorted(_matching_providers(merged, row, git.remaining))
                ],
            }
        )
    return {
        "coverage": "partial_static_resources",
        "snapshots": {
            name: {
                "scripts": snapshot.scripts,
                "resolved_references": snapshot.resolved,
                "failures": [snapshot.failures[key] for key in sorted(snapshot.failures)],
            }
            for name, tree in trees.items()
            for snapshot in (snapshots[tree],)
        },
        "regressions": regressions,
    }
