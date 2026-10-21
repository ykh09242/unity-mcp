"""Map an explicit local upstream range to fork contracts and optional current checks."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tools/fork-compatibility.json"


def git(root, *args):
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )


def revision(root, ref):
    return (
        git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        .decode("ascii")
        .strip()
    )


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_path(root, value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError("Inventory paths must be repository-relative")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Inventory path leaves the repository")
    return resolved


def load_inventory(root=ROOT, path=INVENTORY):
    inventory = json.loads(Path(path).read_text(encoding="utf-8"))
    if inventory.get("schema_version") != 1:
        raise ValueError("Unsupported compatibility inventory")
    checks = inventory["checks"]
    if (
        not checks
        or not inventory["invariants"]
        or any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in checks)
    ):
        raise ValueError("Empty inventory or invalid check identifier")
    ids = set()
    for invariant in inventory["invariants"]:
        if invariant["id"] in ids or not invariant["owners"] or not invariant["checks"]:
            raise ValueError("Duplicate or empty invariant")
        ids.add(invariant["id"])
        for owner in invariant["owners"]:
            local_path(root, owner)
        for check in invariant["checks"]:
            if check not in checks:
                raise ValueError("Invariant references an unknown check")
        for evidence in invariant["version_evidence"]:
            if not local_path(root, evidence).is_file():
                raise ValueError("Missing version-policy evidence: " + evidence)
    for check in checks.values():
        if check["kind"] == "pytest":
            if not check["targets"]:
                raise ValueError("Empty test selection")
            for target in check["targets"]:
                if (
                    target.startswith("-")
                    or not local_path(root, target.split("::", 1)[0]).is_file()
                ):
                    raise ValueError("Missing mapped test: " + target)
        elif check["kind"] == "generator":
            if not local_path(root, check["path"]).is_file():
                raise ValueError("Missing mapped generator")
        elif check["kind"] != "components":
            raise ValueError("Unknown check kind")
    return inventory


def select(inventory, paths):
    selected, covered = [], set()
    for invariant in inventory["invariants"]:
        touched = sorted(
            {p for p in paths if any(fnmatchcase(p, owner) for owner in invariant["owners"])}
        )
        if touched:
            selected.append(
                {
                    "id": invariant["id"],
                    "contract": invariant["contract"],
                    "paths": touched,
                    "checks": invariant["checks"],
                }
            )
            covered.update(touched)
    return selected, sorted(set(paths) - covered)


def inspect_range(root, base, head, inventory, inventory_path):
    start, end = revision(root, base), revision(root, head)
    # Reject ambiguous/non-ancestral inputs rather than silently choosing a merge base.
    subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), "merge-base", "--is-ancestor", start, end],
        check=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    commits = git(root, "rev-list", "--reverse", start + ".." + end).decode("ascii").splitlines()
    paths = set()
    for commit in commits:
        # Include both sides of renames and edits later reverted within the range.
        raw = git(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-m",
            "--no-renames",
            "-z",
            commit,
        )
        paths.update(p.decode("utf-8", errors="surrogateescape") for p in raw.split(b"\0") if p)
    impacts, unclassified = select(inventory, paths)
    version_paths = sorted({p for row in inventory["invariants"] for p in row["version_evidence"]})
    checkout = revision(root, "HEAD")
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=normal"))
    return {
        "schema_version": 1,
        "upstream_range": {"base": start, "head": end, "commits": commits},
        "fork_checkout": {"commit": checkout, "dirty": dirty},
        "inventory_sha256": digest(inventory_path),
        "version_policy_inputs": [{"path": p, "sha256": digest(root / p)} for p in version_paths],
        "impacts": impacts,
        "unclassified_paths": unclassified,
        "check_ids": sorted({c for row in impacts for c in row["checks"]}),
        "validation": {"status": "not_run", "checks": []},
        "limitations": [
            "Path selection identifies review scope, not semantic compatibility.",
            "Checks execute the current fork checkout; selected upstream commits are never executed or merged.",
            "Version inputs describe supported policies, not new native execution evidence.",
            "Native compile/Editor/Player results and unclassified paths require separate review.",
        ],
    }


def run_checks(root, inventory, report, output):
    if report["fork_checkout"]["dirty"]:
        raise ValueError("Mapped checks require a clean committed fork checkout")
    if not report["check_ids"]:
        raise ValueError("No mapped checks; this range needs manual review")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="compatibility-", dir=output)).resolve()
    results = []
    for check_id in report["check_ids"]:
        check = inventory["checks"][check_id]
        log = run_dir / (check_id + ".log")
        if check["kind"] == "components":
            from tools.check_release_evidence import components

            try:
                value = components(root, report["fork_checkout"]["commit"])
                log.write_text(repr(value) + "\n", encoding="utf-8")
                code = 0
            except (ValueError, subprocess.CalledProcessError) as error:
                log.write_text(str(error) + "\n", encoding="utf-8")
                code = 1
            command = ["tools.check_release_evidence.components", report["fork_checkout"]["commit"]]
        else:
            if check["kind"] == "pytest":
                command = [
                    sys.executable,
                    "-B",
                    "-m",
                    "pytest",
                    *[str(root / p) for p in check["targets"]],
                    "-q",
                    "-W",
                    "error",
                    "--junitxml=" + str(run_dir / (check_id + ".xml")),
                    "--basetemp=" + str(run_dir / (check_id + "-tmp")),
                ]
                cwd = root / "Server"
            else:
                command = [sys.executable, "-B", str(root / check["path"]), "--check"]
                cwd = root
            with log.open("wb") as stream:
                code = subprocess.run(
                    command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=False
                ).returncode
        results.append(
            {
                "id": check_id,
                "returncode": code,
                "command": command,
                "log": str(log),
                "log_sha256": digest(log),
            }
        )
    unchanged = revision(root, "HEAD") == report["fork_checkout"]["commit"] and not git(
        root, "status", "--porcelain", "--untracked-files=normal"
    )
    report["validation"] = {
        "status": "passed"
        if unchanged and all(r["returncode"] == 0 for r in results)
        else "failed",
        "checkout_unchanged": unchanged,
        "checks": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-checks", action="store_true")
    args = parser.parse_args()
    inventory = load_inventory()
    report = inspect_range(ROOT, args.base, args.head, inventory, INVENTORY)
    if args.run_checks:
        run_checks(ROOT, inventory, report, args.output.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if report["validation"]["status"] == "failed" else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
