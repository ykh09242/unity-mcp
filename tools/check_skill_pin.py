# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate skill metadata; opt in to verifying local Git objects and both content mirrors.

Run: python tools/check_skill_pin.py --repo <checkout> [--verify-git]
The Git check never fetches or changes the pin and needs the referenced commit locally.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
SKILLS: Final = (".claude/skills/unity-mcp-skill", "unity-mcp-skill")
REVISION: Final = re.compile(r"[0-9a-fA-F]{40}\Z")


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *arguments],
        capture_output=True,
        check=False,
    )


def _committed_files(repo: Path, revision: str, folder: str) -> tuple[dict[str, bytes], list[str]]:
    listing = _git(repo, "ls-tree", "-rz", "--full-tree", revision, "--", folder)
    files: dict[str, bytes] = {}
    issues: list[str] = []
    if listing.returncode:
        return files, [f"Cannot inspect committed skill tree: {folder}"]
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, _ = metadata.split(b" ", 2)
        path = raw_path.decode("utf-8")
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            issues.append(f"Skill source is not a regular committed file: {path}")
            continue
        content = _git(repo, "show", f"{revision}:{path}")
        if content.returncode:
            issues.append(f"Cannot read committed skill blob: {path}")
            continue
        files[path.removeprefix(folder + "/")] = content.stdout.replace(b"\r\n", b"\n")
    if "SKILL.md" not in files:
        issues.append(f"Committed skill tree is missing SKILL.md: {folder}")
    return files, issues


def check_pin(repo: Path, *, verify_git: bool = False) -> list[str]:
    """Check declared syntax by default; inspect actual Git history only when requested."""
    try:
        metadata = json.loads((repo / "MCPForUnity/package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"Cannot read package metadata: {error}"]
    revision = metadata.get("mcpSkillRevision") if isinstance(metadata, dict) else None
    if (
        not isinstance(revision, str)
        or REVISION.fullmatch(revision) is None
        or int(revision, 16) == 0
    ):
        return ["mcpSkillRevision must declare a nonzero full 40-hex verified skill commit."]
    if not verify_git:
        return []
    commit = _git(repo, "cat-file", "-t", revision)
    if commit.returncode or commit.stdout.strip() != b"commit":
        return [
            "The skill revision is not an available local commit; provide its history before --verify-git. No fetch was attempted."
        ]
    expected, issues = _committed_files(repo, revision, SKILLS[0])
    for folder in SKILLS:
        committed, errors = _committed_files(repo, revision, folder)
        issues.extend(errors)
        root = repo / folder
        current: dict[str, bytes] = {}
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_symlink():
                    issues.append(f"Skill mirror contains a linked path: {path.relative_to(repo)}")
                elif path.is_file():
                    current[path.relative_to(root).as_posix()] = path.read_bytes().replace(
                        b"\r\n", b"\n"
                    )
        for relative in sorted(expected.keys() | committed.keys() | current.keys()):
            if relative not in expected or committed.get(relative) != expected[relative]:
                issues.append(f"Committed skill mirrors differ: {folder}/{relative}")
            if relative not in expected or current.get(relative) != expected[relative]:
                issues.append(f"Skill pin does not match intended content: {folder}/{relative}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--verify-git",
        action="store_true",
        help="Explicitly inspect locally available commit objects and skill mirrors; never fetch",
    )
    args = parser.parse_args()
    issues = check_pin(args.repo, verify_git=args.verify_git)
    for issue in issues:
        print(issue)
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
