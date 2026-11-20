"""Compute the anticipated merge in a disposable Git object database."""

from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from cli.utils.play_scenario_analysis_io import safe_local_path
from services.verification.git_draft import _Git, _OID, _merge_base, _resolve_commit
from services.verification.merge_resources import compare_resources


_PROTECTED_PATHS = (
    ":(top,icase,glob)**/Assets/Resources/GameData",
    ":(top,icase,glob)**/Assets/Resources/GameData/**",
    ":(top,icase,glob)**/.env*",
    ":(top,icase,glob)**/.env*/**",
)


@contextmanager
def _isolated_git(source, target):
    object_format = source.read("rev-parse", "--show-object-format").strip().decode("ascii")
    if object_format not in {"sha1", "sha256"}:
        raise ValueError("candidate_anticipated_merge_object_format")
    objects = source.read("rev-parse", "--path-format=absolute", "--git-path", "objects")
    objects = Path(os.fsdecode(objects.removesuffix(b"\n"))).as_posix()
    if "\n" in objects or "\r" in objects:
        raise ValueError("candidate_anticipated_merge_object_path")
    with TemporaryDirectory(prefix="unity-mcp-merge-") as directory:
        repo = Path(directory)
        isolated = _Git(
            repo,
            environment={
                "GIT_DIR": str(repo),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_ATTR_SOURCE": target,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.attributesFile",
                "GIT_CONFIG_VALUE_0": os.devnull,
                "LC_ALL": "C",
            },
        )
        isolated.read("init", "--bare", "--template=", f"--object-format={object_format}")
        (repo / "objects/info/alternates").write_bytes(os.fsencode(objects) + b"\n")
        yield isolated


def _visible_path(path):
    folded = path.lower()
    return b"/assets/resources/gamedata/" not in b"/" + folded + b"/" and not any(
        part.startswith(b".env") for part in folded.split(b"/")
    )


def _merge_result(git, target, head):
    # The stdin protocol carries a separate status even for conflicts with no index stages.
    with TemporaryFile() as request:
        request.write(f"{target} {head}\n".encode("ascii"))
        request.seek(0)
        raw = git.read("merge-tree", "--write-tree", "--stdin", "--name-only", stdin=request)
    fields = raw.split(b"\0")
    if len(fields) < 4 or fields[-2:] != [b"", b""] or not _OID.fullmatch(fields[1]):
        raise ValueError("candidate_anticipated_merge_invalid_git_result")
    status, tree = fields[:2]
    if status == b"1" and len(fields) == 4:
        return tree.decode("ascii"), None
    if status != b"0":
        raise ValueError("candidate_anticipated_merge_invalid_git_result")

    # Use only the stable conflict type and the NUL-delimited paths from Git's messages.
    split = fields.index(b"", 2)
    filenames = fields[2:split]
    paths = sorted(
        path.decode("utf-8", "backslashreplace") for path in filenames if _visible_path(path)
    )
    rows = []
    cursor = split + 1
    while cursor < len(fields) - 2:
        count = fields[cursor]
        if not count.isdigit() or len(count) > 6:
            raise ValueError("candidate_anticipated_merge_invalid_git_result")
        end = cursor + 1 + int(count)
        if end + 2 > len(fields) - 2:
            raise ValueError("candidate_anticipated_merge_invalid_git_result")
        kind = fields[end]
        if kind.startswith(b"CONFLICT"):
            rows.append(
                {
                    "type": kind.decode("utf-8", "backslashreplace"),
                    "paths": [
                        path.decode("utf-8", "backslashreplace")
                        for path in fields[cursor + 1 : end]
                        if _visible_path(path)
                    ],
                }
            )
        cursor = end + 2
    return tree.decode("ascii"), {
        "paths": paths[:1024],
        "path_count": len(filenames),
        "details": rows[:1024],
        "detail_count": len(rows),
    }


def anticipate_merge(project: Path, target_ref: str, head_ref: str) -> dict:
    """Inspect all four immutable trees and report Resources failures unique to the merge."""
    report = {"schema_version": 1, "kind": "git_anticipated_merge", "status": "unavailable"}
    try:
        source = _Git(safe_local_path(project))
        target = _resolve_commit(source, target_ref, "target")
        head = _resolve_commit(source, head_ref, "head")
        base = _merge_base(source, target, head)
        commits = {"base": base, "target": target, "head": head}
        report["commits"] = commits
        trees = {
            name: source.read("rev-parse", "--verify", commit + "^{tree}").strip().decode("ascii")
            for name, commit in commits.items()
        }
        report["trees"] = trees
        for tip in sorted({target, head} - {base}):
            if source.read(
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=none",
                "--name-only",
                "-r",
                "-z",
                base,
                tip,
                "--",
                *_PROTECTED_PATHS,
            ):
                raise ValueError("candidate_anticipated_merge_protected_paths_changed")
        prefix = source.read("rev-parse", "--show-prefix").removesuffix(b"\n").decode("utf-8")
        with _isolated_git(source, target) as isolated:
            tree, conflicts = _merge_result(isolated, target, head)
            if conflicts is not None:
                return {**report, "status": "conflict", "conflicts": conflicts}
            trees["merged"] = tree
            resources = compare_resources(isolated, trees, prefix)
            report["resources"] = resources
            report["status"] = "regression" if resources["regressions"] else "clean"
    except (OSError, ValueError) as error:
        report["reason"] = str(error)[:1024]
    return report
