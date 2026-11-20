"""Review-only Git selection for a Unity project. Never stages files or registers a Player."""

from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess
from threading import Timer
from time import monotonic

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from services.verification.player_candidate import PlayerCandidateManifest, source_revision


_BLOB_LIMIT = 4 * 1024 * 1024
_LIST_LIMIT = 512 * 1024
_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_GUID = re.compile(rb"^guid:[ \t]*([a-f0-9]{32})[ \t]*\r?$", re.MULTILINE)
_DEVICE = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[1-9¹²³])", re.IGNORECASE)
_SCOPE = (
    "Assets",
    "Packages",
    ":(exclude,icase,glob)Assets/Resources/GameData/**",
    ":(exclude,icase,glob)**/.env*",
)


class _Git:
    def __init__(self, project):
        self.project = project
        self.deadline = monotonic() + 60

    def read(self, *args, limit=_LIST_LIMIT):
        # A repository can sit inside a larger checkout. Keep pathnames relative to
        # the selected project and never invoke a diff driver, hook, pager or smudge filter.
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ValueError("candidate_git_deadline")
        env = {key: val for key, val in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
        with subprocess.Popen(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", *args],
            cwd=self.project,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            timer = Timer(remaining, process.kill)
            timer.start()
            try:
                data = process.stdout.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("candidate_git_output_limit")
                if process.wait() != 0:
                    raise ValueError(f"candidate_git_{args[0]}_failed")
                return data
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()


def _records(data):
    return data.rstrip(b"\0").split(b"\0") if data else []


def _eligible(path):
    if (
        len(path) > 512
        or path.casefold().startswith("assets/resources/gamedata/")
        or any(_DEVICE.fullmatch(part.partition(".")[0].rstrip(" ")) for part in path.split("/"))
    ):
        return False
    try:
        PlayerCandidateManifest.allowed_inputs({path: ""})
        return True
    except ValueError:
        return False


def _blob_ids(git, commit, paths):
    if not paths:
        return {}
    # --literal-pathspecs prevents brackets and other valid filenames from
    # broadening the exact path selection. ls-tree and ls-files preserve cwd.
    args = ["ls-tree", "-z", commit] if commit else ["ls-files", "--stage", "-z"]
    result = {}
    records = []
    # A maximum-length selection can exceed Windows' CreateProcess command line limit.
    for start in range(0, len(paths), 16):
        batch = paths[start : start + 16]
        records.extend(_records(git.read("--literal-pathspecs", *args, "--", *batch)))
    for record in records:
        header, name = record.split(b"\t", 1)
        mode, middle, last = header.split(b" ")
        path = name.decode("utf-8")
        oid = last if commit else middle
        if (
            path not in paths
            or mode not in (b"100644", b"100755")
            or not _OID.fullmatch(oid)
            or (not commit and last != b"0")
        ):
            raise ValueError("candidate_git_nonregular_or_unmerged_input")
        result[path] = oid.decode("ascii")
    return result


def _snapshot(git, paths, blobs):
    inputs, assets = {}, {}
    total = 0
    for path in paths:
        if blobs is None:
            try:
                data = read_bytes(git.project / path, _BLOB_LIMIT)
            except FileNotFoundError:
                continue
        else:
            if path not in blobs:
                continue
            data = git.read("cat-file", "blob", blobs[path], limit=_BLOB_LIMIT)
        total += len(data)
        if total > 32 * 1024 * 1024 or len(inputs) >= 128:
            raise ValueError("candidate_draft_selection_limit")
        inputs[path] = sha256(data).hexdigest()
        if path.endswith(".meta"):
            guids = _GUID.findall(data)
            if len(guids) > 1:
                raise ValueError("candidate_draft_ambiguous_metadata")
            if guids:
                assets[path.removesuffix(".meta")] = guids[0].decode("ascii")
    assets = {path: guid for path, guid in assets.items() if path in inputs}
    if len(set(assets.values())) != len(assets) or len(assets) > 64:
        raise ValueError("candidate_draft_ambiguous_or_excess_asset_identities")
    return inputs, assets


def git_draft(project: Path, baseline: str, selection: str):
    project = safe_local_path(project)
    if selection not in {"staged", "working-tree"}:
        raise ValueError("candidate_git_unknown_selection")
    if not baseline or len(baseline) > 256 or baseline.startswith("-") or ":" in baseline:
        raise ValueError("candidate_git_invalid_baseline")
    git = _Git(project)
    commit = git.read("rev-parse", "--verify", "--end-of-options", baseline + "^{commit}").strip()
    if not _OID.fullmatch(commit):
        raise ValueError("candidate_git_invalid_baseline")
    commit = commit.decode("ascii")
    changed = _records(
        git.read(
            "diff",
            "--cached",
            "--relative",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--no-renames",
            "--name-only",
            "-z",
            commit,
            "--",
            *_SCOPE,
        )
    )
    if selection == "working-tree":
        changed.extend(
            _records(git.read("ls-files", "-z", "-m", "-o", "--exclude-standard", "--", *_SCOPE))
        )
    bases = set()
    skipped = 0
    for raw in set(changed):
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        if _eligible(path):
            bases.add(path.removesuffix(".meta"))
        else:
            skipped += 1
    paths = sorted(path for base in bases for path in (base, base + ".meta"))
    if (
        len(paths) > 256
        or any(len(path) > 512 for path in paths)
        or len({path.casefold() for path in paths}) != len(paths)
    ):
        raise ValueError("candidate_draft_selection_limit_or_path_collision")
    before, old_assets = _snapshot(git, paths, _blob_ids(git, commit, paths))
    staged = _blob_ids(git, None, paths)
    after, new_assets = _snapshot(git, paths, staged if selection == "staged" else None)
    changes = []
    for path in paths:
        if before.get(path) == after.get(path):
            continue
        kind = (
            "modified"
            if path in before and path in after
            else "added"
            if path in after
            else "deleted"
        )
        changes.append({"path": path, "kind": kind})
    affected = {row["path"].removesuffix(".meta") for row in changes}

    def evidence(inputs, assets):
        selected = {p: d for p, d in inputs.items() if p.removesuffix(".meta") in affected}
        return {
            "inputs": selected,
            "source_revision": source_revision(selected),
            "assets": {p: guid for p, guid in assets.items() if p in affected},
        }

    previous = {guid: path for path, guid in old_assets.items()}
    return {
        "schema_version": 1,
        "kind": "git_candidate_draft",
        "baseline_commit": commit,
        "selection": selection,
        "baseline": evidence(before, old_assets),
        "candidate": evidence(after, new_assets),
        "changed_paths": changes,
        "asset_moves": [
            {"guid": guid, "from": previous[guid], "to": path}
            for path, guid in new_assets.items()
            if guid in previous and previous[guid] != path
        ],
        "asset_replacements": [
            {"path": path, "old_guid": old_assets[path], "new_guid": new_assets[path]}
            for path in sorted(old_assets.keys() & new_assets.keys())
            if old_assets[path] != new_assets[path]
        ],
        "asset_deletions": [
            {"path": path, "guid": guid}
            for path, guid in old_assets.items()
            if path not in after and guid not in new_assets.values()
        ],
        "skipped_unsupported_path_count": skipped,
    }
