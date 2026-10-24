"""Prepare/recover immutable source pin updates; never fetch, commit or publish.

Use update_versions.py --server-commit SHA --skill-commit SHA [--dry-run].
File replacement is atomic individually. A durable journal permits rollback after
process termination; an unfinished transaction blocks another update.
"""

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import tomllib

if __package__:
    from .check_release_evidence import (
        BASE,
        SKILLS,
        committed,
        digest,
        git,
        load_json,
        require,
        skill_files,
    )
else:
    from check_release_evidence import (
        BASE,
        SKILLS,
        committed,
        digest,
        git,
        load_json,
        require,
        skill_files,
    )


DOCUMENTS = {
    "Server/README.md": 3,
    "website/docs/getting-started/clients.md": 1,
    "website/docs/guides/cli.md": 1,
}
INPUTS = "release-source-inputs.json"
METADATA = {"MCPForUnity/package.json", "manifest.json", INPUTS, *DOCUMENTS}
JOURNAL = ".work/source-pin-transaction"
FINALIZED = JOURNAL + "-finalized"
SKILL_BUNDLE = "Server/src/services/skills_bundle.json"


def sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def safe_path(repo: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    require(
        bool(parts) and not relative.startswith("/") and "/".join(parts) == relative,
        "Noncanonical pin path",
    )
    for part in parts:
        require(
            part not in (".", "..")
            and not re.search(r'[<>:"\\|?*\x00-\x1f]', part)
            and part[-1] not in " .",
            "Unsafe pin path",
        )
        require(
            part.split(".")[0].upper()
            not in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            },
            "Reserved pin path",
        )
    current = repo
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        require(
            not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "Linked pin path refused",
        )
    return current


def owned(relative: str) -> bool:
    return relative in METADATA or any(relative.startswith(root + "/") for root in SKILLS)


def content(path: Path) -> bytes | None:
    require(not path.exists() or path.is_file(), "Pin target is not a file")
    return path.read_bytes() if path.exists() else None


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class Change:
    path: str
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class PinPlan:
    repo: Path
    server: str
    skill: str
    changes: tuple[Change, ...]

    def describe(self) -> dict:
        return {
            "server_commit": self.server,
            "skill_commit": self.skill,
            "files": [
                {"path": c.path, "before_sha256": sha(c.before), "after_sha256": sha(c.after)}
                for c in self.changes
            ],
        }


def validate_server_skill_bundle(
    repo: Path, server: str, skill: str, expected: dict[str, bytes]
) -> None:
    """Match packaged provenance and bytes; YAML derivation remains generator-owned."""
    listing = git(repo, "ls-tree", "-z", "--full-tree", server, "--", SKILL_BUNDLE)
    if not listing:
        return  # Older servers did not publish a bundled skill.
    metadata, path = listing.rstrip(b"\0").split(b"\t", 1)
    mode, kind, _ = metadata.split(b" ")
    require(
        mode in (b"100644", b"100755") and kind == b"blob" and path.decode() == SKILL_BUNDLE,
        "Selected server skill bundle must be a regular committed file",
    )
    raw = committed(repo, server, SKILL_BUNDLE)
    require(len(raw) <= 24 * 1024 * 1024, "Selected server skill bundle exceeds 24 MiB")
    record = load_json(raw)
    require(set(record) == {"payload", "sha256"}, "Invalid selected server skill bundle envelope")
    payload = record["payload"]
    require(
        isinstance(payload, dict)
        and set(payload) == {"format", "repository", "revision", "frontmatter", "files"},
        "Invalid selected server skill bundle payload",
    )
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    require(
        hashlib.sha256(canonical).hexdigest() == record["sha256"],
        "Selected server skill bundle checksum differs from its payload",
    )
    require(
        type(payload["format"]) is int
        and payload["format"] == 1
        and payload["repository"] == BASE
        and payload["revision"] == skill,
        "Selected server skill bundle provenance does not match the selected skill revision",
    )
    frontmatter = payload["frontmatter"]
    require(
        isinstance(frontmatter, dict)
        and frontmatter.get("name") == "unity-mcp-orchestrator"
        and isinstance(frontmatter.get("description"), str)
        and 1 <= len(frontmatter["description"]) <= 1024,
        "Invalid selected server skill bundle frontmatter metadata",
    )
    raw_files = {path: committed(repo, skill, SKILLS[0] + "/" + path) for path in expected}
    require(
        1 <= len(raw_files) <= 512
        and len({path.casefold() for path in raw_files}) == len(raw_files)
        and all(re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md", path) for path in raw_files)
        and sum(map(len, raw_files.values())) <= 16 * 1024 * 1024,
        "Selected skill files exceed the supported skill bundle contract",
    )
    files = payload["files"]
    expected_files = [
        {
            "path": path,
            "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii"),
        }
        for path, data in sorted(raw_files.items())
    ]
    require(
        isinstance(files, list)
        and all(isinstance(item, dict) and type(item.get("size")) is int for item in files)
        and files == expected_files,
        "Selected server skill bundle paths, content, sizes or digests differ from the selected skill",
    )


def prepare(repo: Path, server: str, skill: str) -> PinPlan:
    repo = repo.resolve(strict=True)
    require(
        not safe_path(repo, JOURNAL).exists() and not safe_path(repo, FINALIZED).exists(),
        "Unfinished source pin transaction; use --recover-pins first",
    )
    remote = git(repo, "remote", "get-url", "origin").decode().strip()
    require(
        remote
        in (
            BASE,
            BASE + ".git",
            "git@github.com:ykh09242/unity-mcp.git",
            "ssh://git@github.com/ykh09242/unity-mcp.git",
        ),
        "Wrong source repository identity",
    )
    for revision in (server, skill):
        require(
            digest(revision, 40) and git(repo, "cat-file", "-t", revision).strip() == b"commit",
            "Pins require available full lowercase commit IDs",
        )
    project = tomllib.loads(committed(repo, server, "Server/pyproject.toml").decode("utf-8"))
    require(
        project.get("project", {}).get("name") == "ykh09242-unity-mcp-server",
        "Wrong server distribution identity",
    )
    package_path = "MCPForUnity/package.json"
    package_bytes = content(safe_path(repo, package_path))
    package = load_json(package_bytes)
    require(package.get("name") == "com.ykh09242.unity-mcp", "Wrong Unity package identity")
    old_source, old_skill = package.get("mcpServerSource"), package.get("mcpSkillRevision")
    require(
        isinstance(old_source, str)
        and re.fullmatch(
            re.escape(BASE) + r"/archive/[0-9a-f]{40}\.zip#subdirectory=Server", old_source
        )
        is not None,
        "Current source is not a canonical fork archive",
    )
    require(isinstance(old_skill, str) and digest(old_skill, 40), "Invalid previous skill pin")
    expected = skill_files(repo, skill, SKILLS[0])
    require(
        skill_files(repo, skill, SKILLS[1]) == expected,
        "Selected skill commit contains inconsistent mirrors",
    )
    validate_server_skill_bundle(repo, server, skill, expected)
    previous = skill_files(repo, old_skill, SKILLS[0])
    new_source = BASE + "/archive/" + server + ".zip#subdirectory=Server"
    desired: dict[str, bytes | None] = {}
    for old, new in ((old_source, new_source), (old_skill, skill)):
        needle = json.dumps(old).encode()
        require(package_bytes.count(needle) == 1, "Ambiguous package source field")
        package_bytes = package_bytes.replace(needle, json.dumps(new).encode())
    desired[package_path] = package_bytes
    manifest = load_json(content(safe_path(repo, "manifest.json")))
    require(manifest.get("version") == package.get("version"), "Package/manifest versions differ")
    args = manifest.get("server", {}).get("mcp_config", {}).get("args", [])
    require(isinstance(args, list) and args.count("--from") == 1, "Manifest needs one --from")
    position = args.index("--from") + 1
    require(
        position < len(args) and args[position] == old_source, "Manifest pin differs from package"
    )
    before_manifest = content(safe_path(repo, "manifest.json"))
    require(before_manifest.count(old_source.encode()) == 1, "Ambiguous manifest pin")
    desired["manifest.json"] = before_manifest.replace(old_source.encode(), new_source.encode())
    for relative, count in DOCUMENTS.items():
        before = content(safe_path(repo, relative))
        require(
            before is not None and before.count(old_source.encode()) == count,
            "Source example count changed: " + relative,
        )
        desired[relative] = before.replace(old_source.encode(), new_source.encode())
    for folder in SKILLS:
        root = safe_path(repo, folder)
        actual = {}
        # os.walk does not follow directory links; reject them before descent.
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                safe_path(repo, (Path(parent) / name).relative_to(repo).as_posix())
            for name in files:
                p = Path(parent) / name
                actual[p.relative_to(root).as_posix()] = p.read_bytes()
        require(
            not actual.keys() - (previous.keys() | expected.keys()),
            "Unknown skill files would be overwritten",
        )
        for relative in sorted(previous.keys() | expected.keys()):
            path = folder + "/" + relative
            safe_path(repo, path)
            current = actual.get(relative)
            require(
                current is not None or relative not in previous or relative not in expected,
                "Unreviewed skill deletion: " + path,
            )
            require(
                current is None
                or current.replace(b"\r\n", b"\n")
                in (previous.get(relative), expected.get(relative)),
                "Unreviewed skill edits: " + path,
            )
            desired[path] = (
                current
                if current is not None and current.replace(b"\r\n", b"\n") == expected.get(relative)
                else expected.get(relative)
            )
    previous_inputs = content(safe_path(repo, INPUTS))
    if previous_inputs is not None:
        require(
            load_json(previous_inputs)
            == {
                "schema_version": 1,
                "repository": BASE,
                "server_commit": old_source.split("/archive/", 1)[1].split(".zip", 1)[0],
                "skill_commit": old_skill,
                "server_source": old_source,
            },
            "Unreviewed release source input edits",
        )
    desired[INPUTS] = json_bytes(
        {
            "schema_version": 1,
            "repository": BASE,
            "server_commit": server,
            "skill_commit": skill,
            "server_source": new_source,
        }
    )
    require(len({p.casefold() for p in desired}) == len(desired), "Case-colliding source files")
    changes = tuple(
        Change(p, before, data)
        for p, data in sorted(desired.items())
        if (before := content(safe_path(repo, p))) != data
    )
    return PinPlan(repo, server, skill, changes)


def write_durable(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def clean_journal(folder: Path, count: int) -> None:
    names = {
        "journal.json",
        *(f"{i}.{suffix}" for i in range(count) for suffix in ("before", "after")),
    }
    entries = list(folder.iterdir())
    require(
        all(p.name in names and p.is_file() and not p.is_symlink() for p in entries),
        "Unknown transaction files preserved",
    )
    # Keep the descriptor until all blobs are gone; an empty retired directory is valid.
    for path in sorted(entries, key=lambda p: p.name == "journal.json"):
        path.unlink()
    folder.rmdir()


def journal_rows(repo: Path, relative: str) -> list:
    journal = load_json(content(safe_path(repo, relative + "/journal.json")))
    require(
        journal.get("schema_version") == 1 and journal.get("repository") == str(repo),
        "Mismatched source pin transaction",
    )
    rows = journal.get("files")
    require(isinstance(rows, list) and bool(rows), "Invalid source pin transaction")
    require(
        len({row["path"].casefold() for row in rows}) == len(rows), "Repeated transaction targets"
    )
    return rows


def clean_finalized(repo: Path) -> None:
    folder = safe_path(repo, FINALIZED)
    require(folder.is_dir(), "Missing finalized source pin transaction")
    if content(safe_path(repo, FINALIZED + "/journal.json")) is None:
        require(not list(folder.iterdir()), "Unknown transaction files preserved")
        folder.rmdir()
    else:
        rows = journal_rows(repo, FINALIZED)
        clean_journal(folder, len(rows))


def finalize(repo: Path) -> None:
    # Atomic retirement ends rollback eligibility before any backup is deleted.
    # If cleanup stops, recovery only resumes deleting retired transaction files.
    target = safe_path(repo, FINALIZED)
    require(not target.exists(), "Unfinished finalized source pin transaction")
    os.rename(safe_path(repo, JOURNAL), target)
    clean_finalized(repo)


def recover(repo: Path) -> int:
    repo = repo.resolve(strict=True)
    folder = safe_path(repo, JOURNAL)
    if safe_path(repo, FINALIZED).exists():
        require(not folder.exists(), "Conflicting source pin transactions preserved")
        clean_finalized(repo)
        return 0
    require(folder.is_dir(), "No source pin transaction to recover")
    rows = journal_rows(repo, JOURNAL)
    # Validate every target and backup before changing anything during recovery.
    for index, row in enumerate(rows):
        require(owned(row["path"]), "Unowned source pin target")
        path = safe_path(repo, row["path"])
        require(
            sha(content(path)) in (row["before_sha256"], row["after_sha256"]),
            "Concurrent edits preserved; recovery refused",
        )
        backup = content(safe_path(repo, JOURNAL + f"/{index}.before"))
        require(sha(backup) == row["before_sha256"], "Missing or corrupt transaction backup")
    for index, row in enumerate(rows):
        path = safe_path(repo, row["path"])
        if sha(content(path)) == row["before_sha256"]:
            continue
        if row["before_sha256"] is None:
            path.unlink()
        else:
            # Preserve backup until all targets are restored, including a second crash.
            staging = safe_path(repo, JOURNAL + f"/{index}.after")
            if staging.exists():
                staging.unlink()
            write_durable(staging, (folder / f"{index}.before").read_bytes())
            os.replace(staging, path)
    finalize(repo)
    return len(rows)


def apply(plan: PinPlan) -> int:
    if not plan.changes:
        return 0
    repo = plan.repo
    folder = safe_path(repo, JOURNAL)
    require(
        not folder.exists() and not safe_path(repo, FINALIZED).exists(),
        "Unfinished source pin transaction; use --recover-pins first",
    )
    for change in plan.changes:
        require(
            owned(change.path) and content(safe_path(repo, change.path)) == change.before,
            "Pin inputs changed; prepare again",
        )
    folder.parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".source-pins-", dir=folder.parent))
    published = False
    try:
        for index, change in enumerate(plan.changes):
            for suffix, data in (("before", change.before), ("after", change.after)):
                if data is not None:
                    write_durable(stage / f"{index}.{suffix}", data)
        rows = plan.describe()["files"]
        write_durable(
            stage / "journal.json",
            json_bytes({"schema_version": 1, "repository": str(repo), "files": rows}),
        )
        os.rename(stage, folder)
        published = True
        for index, change in enumerate(plan.changes):
            path = safe_path(repo, change.path)
            require(content(path) == change.before, "Concurrent edits preserved; apply stopped")
            if change.after is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(folder / f"{index}.after", path)
        finalize(repo)
        return len(plan.changes)
    except BaseException:
        if published and folder.exists():
            recover(repo)
        elif stage.exists():
            clean_journal(stage, len(plan.changes))
        raise
