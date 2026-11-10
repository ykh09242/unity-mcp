#!/usr/bin/env python3
"""Update independently versioned Unity or Python distribution metadata.

This script updates the version in all files that need it:
- MCPForUnity/package.json (Unity package version)
- manifest.json (MCP bundle manifest)
- Server/pyproject.toml (Python package version)
- Server/uv.lock (the project's own entry, so `uv sync --locked` keeps passing)
- Server/README.md (version references)
- README.md (fixed version examples)
- docs/i18n/README-zh.md (fixed version examples)

Usage:
    python3 tools/update_versions.py [--component unity|server|all] [--dry-run] [--version VERSION]

Options:
    --dry-run: Show what would be updated without making changes
    --version: Specify version to use (auto-detected from package.json if not provided)
    --component: Update Unity (default), server, or the legacy synchronized release

Examples:
    # Update Unity bundle metadata to match package.json without changing the server
    python3 tools/update_versions.py

    # Update the server independently; its source pin is changed only after a commit
    python3 tools/update_versions.py --component server --version 1.0.1

    # Explicitly retain the upstream synchronized release behavior
    python3 tools/update_versions.py --component all --version 9.2.0

    # Dry run to see what would be updated
    python3 tools/update_versions.py --dry-run
"""

import argparse
from dataclasses import dataclass
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = REPO_ROOT / "MCPForUnity" / "package.json"
MANIFEST_JSON = REPO_ROOT / "manifest.json"
PYPROJECT_TOML = REPO_ROOT / "Server" / "pyproject.toml"
UV_LOCK = REPO_ROOT / "Server" / "uv.lock"
SERVER_README = REPO_ROOT / "Server" / "README.md"
ROOT_README = REPO_ROOT / "README.md"
ZH_README = REPO_ROOT / "docs" / "i18n" / "README-zh.md"


def load_package_version() -> str:
    """Load version from package.json."""
    if not PACKAGE_JSON.exists():
        raise FileNotFoundError(f"Package file not found: {PACKAGE_JSON}")

    package_data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    version = package_data.get("version")

    if not version:
        raise ValueError("No version found in package.json")

    return version


def update_package_json(new_version: str, dry_run: bool = False) -> bool:
    """Update version in MCPForUnity/package.json."""
    return _update_one(_prepare_json(PACKAGE_JSON, new_version), dry_run)


def update_manifest_json(new_version: str, dry_run: bool = False) -> bool:
    """Update version in manifest.json."""
    return _update_one(_prepare_json(MANIFEST_JSON, new_version), dry_run)


def update_pyproject_toml(new_version: str, dry_run: bool = False) -> bool:
    """Update version in Server/pyproject.toml."""
    return _update_one(_prepare_pyproject(new_version), dry_run)


# uv.lock records the project's own version next to its resolved dependencies. Bumping
# pyproject.toml without this line makes `uv sync --locked` fail and left the lock stale
# for whole release cycles (10.1.0 in the lock while pyproject said 10.2.0). The block is
# the only place the project's version appears, so a targeted rewrite is equivalent to
# `uv lock` here without requiring uv on the release runner.
_UV_LOCK_SELF_VERSION = re.compile(
    r'(^\[\[package\]\]\s*\nname = "ykh09242-unity-mcp-server"\s*\nversion = ")([^"]+)(")',
    re.MULTILINE,
)


def _display(path: Path) -> str:
    """Repo-relative for the normal case; absolute when tests point at a temp file."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def update_uv_lock(new_version: str, dry_run: bool = False) -> bool:
    """Update the fork distribution entry in Server/uv.lock."""
    return _update_one(_prepare_lock(new_version), dry_run)


@dataclass(frozen=True, slots=True)
class VersionChange:
    path: Path
    before: bytes
    after: bytes


def _server_version(value: str) -> str:
    """Accept PEP 440 versions without whitespace or unsafe TOML interpolation."""
    # Unity-only updates and source-pin routing retain their existing dependency surface.
    from packaging.version import Version

    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("Server version must follow PEP 440 without whitespace")
    Version(value)
    return value


def _prepare_json(path: Path, version: str) -> VersionChange:
    before = path.read_bytes()
    data = json.loads(before)
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise ValueError(f"Missing version in {_display(path)}")
    if data["version"] == version:
        return VersionChange(path, before, before)
    data["version"] = version
    after = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return VersionChange(path, before, after)


def _prepare_pyproject(version: str) -> VersionChange:
    _server_version(version)
    before = PYPROJECT_TOML.read_bytes()
    content = before.decode("utf-8")
    expected = tomllib.loads(content)
    project = expected.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError(f"Missing project version in {_display(PYPROJECT_TOML)}")
    if project["version"] == version:
        return VersionChange(PYPROJECT_TOML, before, before)
    project["version"] = version
    # Regex locates complete single-line string candidates, not TOML ownership. Parsing
    # each candidate proves that only project.version changes, even inside multiline text.
    field = re.compile(
        r"""(?m)^[ \t]*(?:version|"version"|'version')[ \t]*=[ \t]*"""
        r"""(?P<value>"(?:[^"\\\r\n]|\\.)*"|'[^'\r\n]*')"""
    )
    for candidate in field.finditer(content):
        after = (
            content[: candidate.start("value")]
            + json.dumps(version)
            + content[candidate.end("value") :]
        )
        try:
            parsed = tomllib.loads(after)
        except tomllib.TOMLDecodeError:
            continue
        if parsed == expected:
            return VersionChange(PYPROJECT_TOML, before, after.encode("utf-8"))
    raise ValueError(f"Unsupported project version field in {_display(PYPROJECT_TOML)}")


def _prepare_lock(version: str) -> VersionChange:
    _server_version(version)
    before = UV_LOCK.read_bytes()
    content = before.decode("utf-8")
    expected = tomllib.loads(content)
    packages = expected.get("package", [])
    entries = [entry for entry in packages if entry.get("name") == "ykh09242-unity-mcp-server"]
    matches = list(_UV_LOCK_SELF_VERSION.finditer(content))
    if (
        len(entries) != 1
        or not isinstance(entries[0].get("version"), str)
        or len(matches) != 1
        or matches[0][2] != entries[0]["version"]
    ):
        raise ValueError(f"Missing or ambiguous fork distribution entry in {_display(UV_LOCK)}")
    after = _UV_LOCK_SELF_VERSION.sub(lambda match: match[1] + version + match[3], content, count=1)
    entries[0]["version"] = version
    if tomllib.loads(after) != expected:
        raise ValueError(f"Unsupported fork distribution version field in {_display(UV_LOCK)}")
    return VersionChange(UV_LOCK, before, after.encode("utf-8"))


def _publish(prepared: list[VersionChange]) -> None:
    """Stage all bytes, then publish; keep recovery backups if rollback cannot finish."""
    for change in prepared:
        if change.path.read_bytes() != change.before:
            raise ValueError(f"Concurrent changes preserved: {_display(change.path)}")
    changes = [change for change in prepared if change.before != change.after]
    if not changes:
        return
    stage = Path(tempfile.mkdtemp(prefix=".version-update-", dir=REPO_ROOT))
    published: list[tuple[int, VersionChange]] = []
    preserve = False
    try:
        for index, change in enumerate(changes):
            for suffix, data in (("before", change.before), ("after", change.after)):
                with (stage / f"{index}.{suffix}").open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        # Backup names stay meaningful if an interrupted operation requires manual recovery.
        with (stage / "targets.json").open("xb") as stream:
            stream.write(
                json.dumps([str(change.path) for change in changes], indent=2).encode("utf-8")
            )
            stream.flush()
            os.fsync(stream.fileno())
        for index, change in enumerate(changes):
            if change.path.read_bytes() != change.before:
                raise ValueError(f"Concurrent changes preserved: {_display(change.path)}")
            published.append((index, change))
            os.replace(stage / f"{index}.after", change.path)
        for change in prepared:
            if change.path.read_bytes() != change.after:
                raise ValueError(f"Concurrent changes preserved: {_display(change.path)}")
    except (OSError, ValueError, KeyboardInterrupt):
        # Retain each backup until all restoration succeeds, including uncertain I/O outcomes.
        try:
            for index, change in reversed(published):
                current = change.path.read_bytes()
                if current == change.before:
                    continue
                if current != change.after:
                    raise ValueError(f"Concurrent changes preserved: {_display(change.path)}")
                restore = stage / f"{index}.restore"
                restore.write_bytes(change.before)
                os.replace(restore, change.path)
        except (OSError, ValueError, KeyboardInterrupt) as recovery_error:
            preserve = True
            raise OSError(
                f"Version rollback incomplete; recovery backups: {stage}"
            ) from recovery_error
        raise
    finally:
        if not preserve:
            shutil.rmtree(stage)


def _update_one(change: VersionChange, dry_run: bool) -> bool:
    if change.before == change.after:
        print(f"✓ {_display(change.path)} already current")
        return False
    print(f"Updating {_display(change.path)}")
    if not dry_run:
        _publish([change])
    return True


def update_server_readme(new_version: str, dry_run: bool = False) -> bool:
    """Update version references in Server/README.md."""
    return _update_one(_prepare_server_readme(new_version), dry_run)


def _prepare_server_readme(version: str) -> VersionChange:
    before = SERVER_README.read_bytes()
    pattern = (
        r"git\+https://github\.com/CoplayDev/unity-mcp@v[0-9]+\.[0-9]+\.[0-9]+#subdirectory=Server"
    )
    replacement = f"git+https://github.com/CoplayDev/unity-mcp@v{version}#subdirectory=Server"
    after = re.sub(pattern, lambda _: replacement, before.decode("utf-8")).encode("utf-8")
    return VersionChange(SERVER_README, before, after)


def update_root_readme(new_version: str, dry_run: bool = False) -> bool:
    """Update fixed version examples in README.md."""
    if not ROOT_README.exists():
        print(f"Warning: {ROOT_README.relative_to(REPO_ROOT)} not found")
        return False

    content = ROOT_README.read_text(encoding="utf-8")

    # Pattern to match git URLs with fixed version tags
    pattern = (
        r"https://github\.com/CoplayDev/unity-mcp\.git\?path=/MCPForUnity#v[0-9]+\.[0-9]+\.[0-9]+"
    )
    replacement = f"https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#v{new_version}"

    if not re.search(pattern, content):
        print(f"✓ {ROOT_README.relative_to(REPO_ROOT)} has no version references to update")
        return False

    print(f"Updating version references in {ROOT_README.relative_to(REPO_ROOT)}")

    if not dry_run:
        content = re.sub(pattern, replacement, content)
        ROOT_README.write_text(content, encoding="utf-8")

    return True


def update_zh_readme(new_version: str, dry_run: bool = False) -> bool:
    """Update fixed version examples in docs/i18n/README-zh.md."""
    if not ZH_README.exists():
        print(f"Warning: {ZH_README.relative_to(REPO_ROOT)} not found")
        return False

    content = ZH_README.read_text(encoding="utf-8")

    # Pattern to match git URLs with fixed version tags
    pattern = (
        r"https://github\.com/CoplayDev/unity-mcp\.git\?path=/MCPForUnity#v[0-9]+\.[0-9]+\.[0-9]+"
    )
    replacement = f"https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#v{new_version}"

    if not re.search(pattern, content):
        print(f"✓ {ZH_README.relative_to(REPO_ROOT)} has no version references to update")
        return False

    print(f"Updating version references in {ZH_README.relative_to(REPO_ROOT)}")

    if not dry_run:
        content = re.sub(pattern, replacement, content)
        ZH_README.write_text(content, encoding="utf-8")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update Unity or server versions independently",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )
    parser.add_argument(
        "--version",
        help="Version to set (auto-detected from package.json if not provided)",
    )
    parser.add_argument(
        "--component",
        choices=("unity", "server", "all"),
        default="unity",
        help="Metadata to update (default: unity; all preserves legacy synchronization)",
    )

    parser.add_argument(
        "--server-commit", help="Reviewed local full server commit; pair with --skill-commit"
    )
    parser.add_argument(
        "--skill-commit", help="Reviewed local full skill commit; pair with --server-commit"
    )
    parser.add_argument(
        "--recover-pins",
        action="store_true",
        help="Roll back an interrupted source pin transaction",
    )
    args = parser.parse_args()
    if args.server_commit or args.skill_commit or args.recover_pins:
        if args.version is not None or args.component != "unity":
            parser.error("Source pins and version-number updates must be separate operations")
        if args.recover_pins and (args.server_commit or args.skill_commit or args.dry_run):
            parser.error("--recover-pins cannot be combined with pin selection or --dry-run")
        if not args.recover_pins and not (args.server_commit and args.skill_commit):
            parser.error("Both --server-commit and --skill-commit are required")
        if __package__:
            from tools import update_source_pins as pins
        else:
            import update_source_pins as pins
        try:
            if args.recover_pins:
                print(json.dumps({"recovered_files": pins.recover(REPO_ROOT)}))
            else:
                plan = pins.prepare(REPO_ROOT, args.server_commit, args.skill_commit)
                result = plan.describe()
                result["dry_run"] = args.dry_run
                if not args.dry_run:
                    result["applied_files"] = pins.apply(plan)
                print(json.dumps(result, indent=2))
            return 0
        except (OSError, ValueError, TypeError) as error:
            print("Source pin update refused: " + str(error), file=sys.stderr)
            return 1
    if args.component == "server" and not args.version:
        parser.error("--component server requires --version; it cannot inherit the Unity version")

    try:
        # Determine version
        if args.version is not None:
            version = args.version
            print(f"Using specified version: {version}")
        else:
            version = load_package_version()
            print(f"Auto-detected version from package.json: {version}")

        if args.component in ("unity", "all"):
            if __package__:
                from .check_release_evidence import unity_package_version
            else:
                from check_release_evidence import unity_package_version
            unity_package_version(version)

        if args.component in ("server", "all"):
            _server_version(version)

        unity_updates = [
            lambda: _prepare_json(PACKAGE_JSON, version),
            lambda: _prepare_json(MANIFEST_JSON, version),
        ]
        server_updates = [
            lambda: _prepare_pyproject(version),
            lambda: _prepare_lock(version),
        ]
        updaters = {
            "unity": unity_updates,
            "server": server_updates,
            "all": unity_updates + server_updates + [lambda: _prepare_server_readme(version)],
        }[args.component]
        # Every required input is parsed before creating a stage or modifying a target.
        prepared = [updater() for updater in updaters]
        changes = [change for change in prepared if change.before != change.after]
        updates_made = [_display(change.path) for change in changes]
        for change in prepared:
            print(
                f"{'Updating' if change in changes else 'Already current:'} {_display(change.path)}"
            )
        if not args.dry_run:
            _publish(prepared)

        # Summary
        if args.dry_run:
            print("\nDry run complete. No files were modified.")
        else:
            if updates_made:
                print(f"\nUpdated {len(updates_made)} files: {', '.join(updates_made)}")
            else:
                print("\nAll files already at the correct version.")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
