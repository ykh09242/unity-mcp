"""Versioned native test selections and receipts for owned, reusable QA inputs."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

_EXCLUDED = {"library", "temp", "logs", "obj", ".git", "gamedata", "__pycache__"}


def checked_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Linked QA inputs are prohibited")
    return path.resolve()


def digest(path):
    checked_path(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_files(root, *, excluded_roots=()):
    root = checked_path(root)
    if not root.is_dir():
        raise ValueError("QA input directory is missing")
    result = {}

    def fail(error):
        raise error

    for directory, names, files in os.walk(root, onerror=fail):
        names[:] = sorted(
            name
            for name in names
            if name.lower() not in _EXCLUDED
            and not name.startswith(".env")
            and (Path(directory) / name).relative_to(root).as_posix() not in excluded_roots
        )
        for name in names:
            checked_path(Path(directory) / name)
        for name in sorted(files):
            if name.startswith(".env") or Path(name).suffix.lower() in {
                ".key",
                ".pem",
                ".pfx",
                ".p12",
            }:
                continue
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def load_selection(path, groups, platform):
    raw = checked_path(path).read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("groups"), dict):
        raise ValueError("Unsupported native group manifest")
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Choose nonempty, distinct native groups")
    required = []
    exclusions = {}
    requires_graphics = False
    for name in groups:
        group = manifest["groups"].get(name)
        if not isinstance(group, dict):
            raise ValueError("Unknown native group: " + name)
        if type(group.get("disruptive")) is not bool:
            raise ValueError("Group must declare whether it is disruptive")
        if group["disruptive"] and len(groups) != 1:
            raise ValueError("A disruptive group requires a separate launch")
        graphics = group.get("requires_graphics", False)
        if type(graphics) is not bool:
            raise ValueError("Graphics requirement must be a boolean")
        requires_graphics |= graphics
        excluded = group.get("platform_exclusions")
        if not isinstance(excluded, dict) or any(
            not isinstance(v, str) or not v for v in excluded.values()
        ):
            raise ValueError("Platform exclusions require explicit reasons")
        exclusions[name] = excluded
        if platform in excluded:
            raise ValueError("Excluded native group " + name + ": " + excluded[platform])
        methods = group.get("methods")
        if (
            not isinstance(methods, list)
            or not methods
            or any(not isinstance(method, str) for method in methods)
            or len(methods) != len(set(methods))
        ):
            raise ValueError("Group requires a nonempty distinct method inventory")
        for method in methods:
            if (
                not isinstance(method, str)
                or "." not in method
                or not all(p.isidentifier() for p in method.split("."))
            ):
                raise ValueError("Expected a fully qualified native test method")
            if method not in required:
                required.append(method)
    return {
        "groups": list(groups),
        "required": required,
        "filter": ";".join(required),
        "requiresGraphics": requires_graphics,
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
        "platform": platform,
        "platformExclusions": exclusions,
    }


def capture_inputs(output, prep, editor):
    output = checked_path(output)
    project = checked_path(prep["projectAbsolute"])
    if not project.is_relative_to(output) or project == output:
        raise ValueError("Project must belong to its QA output")
    return {
        "schema_version": 1,
        "source": json.loads((output / "source.json").read_text(encoding="utf-8")),
        "editor": {
            "path": str(checked_path(editor)),
            "sha256": digest(editor),
            "version": prep["unityVersion"],
        },
        "files": tree_files(
            output / "snapshot",
            excluded_roots={
                project.relative_to(output / "snapshot").as_posix() + "/ProjectSettings",
                project.relative_to(output / "snapshot").as_posix() + "/UserSettings",
            },
        ),
    }


def verify_inputs(output, prep, editor, expected):
    actual = capture_inputs(output, prep, editor)
    # Unity may create absent metadata/lock files on first import. Existing ones remain guarded.
    actual["files"] = {
        k: v
        for k, v in actual["files"].items()
        if k in expected["files"]
        or (not k.endswith(".meta") and not k.endswith("/packages-lock.json"))
    }
    if actual != expected:
        raise ValueError(
            "QA source, prepared inputs, packages or Editor changed; refresh before launching"
        )


def refresh_plan(old, new, target, *, exclude=()):
    old, new, target = (checked_path(p) for p in (old, new, target))
    previous, incoming = tree_files(old), tree_files(new)
    plan = []
    for relative in sorted(previous.keys() | incoming.keys()):
        if relative in exclude:
            continue
        destination = checked_path(target / relative)
        if not destination.is_relative_to(target):
            raise ValueError("Refresh target escaped owned root")
        current = digest(destination) if destination.is_file() else None
        if current != previous.get(relative):
            raise ValueError("Refresh conflicts with a changed or unowned file: " + relative)
        if previous.get(relative) != incoming.get(relative):
            plan.append((new / relative if relative in incoming else None, destination))
    return plan


def apply_refresh(plan):
    for source, destination in plan:
        if source is None:
            destination.unlink()
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".qa-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                with source.open("rb") as incoming:
                    while chunk := incoming.read(65536):
                        stream.write(chunk)
            except BaseException:
                stream.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def refresh_tree(old, new, target):
    apply_refresh(refresh_plan(old, new, target))


@contextmanager
def operation_lock(output):
    path = checked_path(output / ".qa-operation.lock")
    with path.open("xb") as stream:
        stream.write(str(os.getpid()).encode("ascii"))
    try:
        yield
    finally:
        path.unlink()
