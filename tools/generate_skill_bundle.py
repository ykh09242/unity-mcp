"""Build/check the immutable server skill asset from the existing canonical pin.

--write explicitly verifies local Git objects; it never fetches or changes a pin.
Default check mode compares both checkout mirrors and metadata, not Git history.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

import yaml

from check_skill_pin import (
    ROOT,
    SKILLS,
    assert_unlinked_skill_path,
    check_pin,
    verified_skill_snapshot,
)

# Use the same pre-serialization admission as the server, from this checkout.
# Generator execution already requires the existing server development environment.
sys.path.insert(0, str(ROOT / "Server/src"))
from models.response_limits import MAX_RESPONSE_BYTES, response_size

REPOSITORY = "https://github.com/ykh09242/unity-mcp"
NAME = "unity-mcp-orchestrator"
TARGET = Path("Server/src/services/skills_bundle.json")
PATH = re.compile(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md\Z")
MAX_FRONTMATTER_BYTES = MAX_RESPONSE_BYTES - 32_768


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML with duplicate mapping keys refused instead of overwritten."""


def _mapping(loader, node):
    pairs = loader.construct_pairs(node)
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Frontmatter requires unique string mapping keys")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _checkout_files(repo: Path) -> tuple[str, dict[str, bytes]]:
    for folder in SKILLS:
        assert_unlinked_skill_path(repo, repo / folder)
    issues = check_pin(repo)
    if issues:
        raise ValueError("; ".join(issues))
    mirrors = []
    for folder in SKILLS:
        root = repo / folder
        files = {}
        total = 0
        for path in root.rglob("*"):
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Skill source contains a linked/reparse entry")
            if path.is_file():
                total += path.stat().st_size
                if len(files) >= 512 or total > 16 * 1024 * 1024:
                    raise ValueError("Excessive skill source tree")
                files[path.relative_to(root).as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
        mirrors.append(files)
    if not mirrors[0] or mirrors[0] != mirrors[1]:
        raise ValueError("Canonical skill mirrors differ or are empty")
    metadata = json.loads((repo / "MCPForUnity/package.json").read_bytes())
    return metadata["mcpSkillRevision"].lower(), mirrors[0]


def build_bundle(repo: Path, *, verify_git: bool = True) -> bytes:
    """Derive reproducible JSON without modifying source files or declared pins."""
    revision, files = verified_skill_snapshot(repo) if verify_git else _checkout_files(repo)
    paths = sorted(files)
    if (
        "SKILL.md" not in files
        or not 1 <= len(paths) <= 512
        or len({path.casefold() for path in paths}) != len(paths)
        or any(PATH.fullmatch(path) is None for path in paths)
        or sum(map(len, files.values())) > 16 * 1024 * 1024
    ):
        raise ValueError("Invalid, incomplete or excessive skill file set")
    texts = {path: data.decode("utf-8", errors="strict") for path, data in files.items()}
    lines = texts["SKILL.md"].splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("SKILL.md must contain YAML frontmatter")
    end = lines.index("---", 1)
    try:
        frontmatter = yaml.load("\n".join(lines[1:end]), Loader=UniqueLoader)
        # YAML aliases can form a compact acyclic DAG whose JSON expansion is
        # exponential. The shared bounded walk counts expanded visits, depth,
        # encoded bytes and retained copies before this unbounded encoder runs.
        if response_size(frontmatter, max_bytes=MAX_FRONTMATTER_BYTES) is None:
            raise ValueError("Frontmatter exceeds shared response limits")
        # Reject dates, cycles, non-finite values and other non-JSON YAML values.
        serialized = json.dumps(frontmatter, ensure_ascii=False, allow_nan=False)
        frontmatter = json.loads(serialized)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as error:
        raise ValueError("Frontmatter must be an acyclic JSON-compatible mapping") from error
    if (
        not isinstance(frontmatter, dict)
        or frontmatter.get("name") != NAME
        or not isinstance(frontmatter.get("description"), str)
        or not 1 <= len(frontmatter["description"]) <= 1024
    ):
        raise ValueError("Invalid canonical skill frontmatter name/description")
    payload = {
        "format": 1,
        "repository": REPOSITORY,
        "revision": revision,
        "frontmatter": frontmatter,
        "files": [
            {
                "path": path,
                "size": len(files[path]),
                "digest": "sha256:" + hashlib.sha256(files[path]).hexdigest(),
                "data": base64.b64encode(files[path]).decode("ascii"),
            }
            for path in paths
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    record = {"payload": payload, "sha256": hashlib.sha256(canonical).hexdigest()}
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--write", action="store_true", help="Verify pinned local Git blobs and write the asset"
    )
    args = parser.parse_args()
    expected = build_bundle(args.repo, verify_git=args.write)
    target = args.repo / TARGET
    if args.write:
        target.write_bytes(expected)
        return 0
    if not target.is_file() or target.read_bytes() != expected:
        print(
            "Skill bundle is absent/stale; regenerate with --write after explicit local pin verification."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
