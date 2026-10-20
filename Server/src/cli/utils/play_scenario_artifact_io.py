"""Guarded publication of a report pair with rollback on an interrupted write."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

from cli.utils.play_scenario_analysis_io import safe_local_path

COMPLETION_MANIFEST = "report.complete.json"


def _identity(path: Path) -> tuple[int, int, int, int] | None:
    safe_local_path(path)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Report target is not a regular file: {path.name}")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def preflight_report_output(directory: Path, names: tuple[str, ...]) -> Path:
    """Check final entries and writability before starting expensive native work."""
    directory = safe_local_path(directory)
    for name in (*names, COMPLETION_MANIFEST):
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("Report names must be plain filenames")
        _identity(directory / name)
    directory.mkdir(parents=True, exist_ok=True)
    safe_local_path(directory)
    descriptor, probe = tempfile.mkstemp(prefix=".mcp-write-check-", dir=directory)
    os.close(descriptor)
    safe_local_path(Path(probe)).unlink()
    return directory


def publish_reports(
    directory: Path, reports: dict[str, bytes], *, preserved: dict[str, bytes] | None = None
) -> None:
    """Serialize writers and restore the previous pair if staging or publication fails.

    A recovery failure retains the unique stage, including any original files. The
    completion manifest is published last. Concurrent readers must use
    read_completed_reports, which rejects incomplete or changed generations.
    """
    preserved = preserved or {}
    if COMPLETION_MANIFEST in reports or set(preserved) & set(reports):
        raise ValueError("Reserved or duplicate report name")
    directory = preflight_report_output(directory, tuple(reports) + tuple(preserved))
    manifest = {
        "schema_version": 1,
        "generation": uuid4().hex,
        "files": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in {**reports, **preserved}.items()
        },
    }
    reports = {
        **reports,
        COMPLETION_MANIFEST: (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"),
    }
    lock = safe_local_path(directory / ".mcp-report-export.lock")
    with lock.open("xb") as lock_stream:
        lock_identity = os.fstat(lock_stream.fileno())
        stage = None
        keep_recovery = False
        try:
            stage = Path(tempfile.mkdtemp(prefix=".mcp-report-", dir=directory))
            before = {name: _identity(directory / name) for name in reports}
            for name, content in reports.items():
                if safe_local_path(stage / name).write_bytes(content) != len(content):
                    raise OSError("Incomplete staged report write")
            for name in reports:
                if _identity(directory / name) != before[name]:
                    raise OSError("Report changed while preparing export")
            moved: list[str] = []
            published: dict[str, tuple[int, int, int, int] | None] = {}
            try:
                # Withdraw completion first and all old members before any new member.
                # Fixed filenames can temporarily be absent, but never form a mixed pair.
                for name in (
                    COMPLETION_MANIFEST,
                    *(key for key in reports if key != COMPLETION_MANIFEST),
                ):
                    target = safe_local_path(directory / name)
                    if _identity(target) != before[name]:
                        raise OSError("Report changed before publication")
                    if before[name] is not None:
                        os.replace(target, safe_local_path(stage / (name + ".previous")))
                        moved.append(name)
                for name in reports:
                    target = safe_local_path(directory / name)
                    if _identity(target) is not None:
                        raise OSError("Report target was recreated during publication")
                    if name == COMPLETION_MANIFEST:
                        for retained, content in preserved.items():
                            if safe_local_path(directory / retained).read_bytes() != content:
                                raise OSError("Native report changed during publication")
                    os.replace(safe_local_path(stage / name), target)
                    published[name] = _identity(target)
            except BaseException:
                try:
                    for name in reversed(tuple(reports)):
                        target = safe_local_path(directory / name)
                        if name in published:
                            if _identity(target) != published[name]:
                                raise OSError("Published report was changed by another writer")
                            target.unlink()
                    for name in (
                        *[key for key in reports if key != COMPLETION_MANIFEST],
                        COMPLETION_MANIFEST,
                    ):
                        target = safe_local_path(directory / name)
                        if name in moved:
                            if _identity(target) is not None:
                                raise OSError("Report target was recreated during rollback")
                            os.replace(safe_local_path(stage / (name + ".previous")), target)
                except (OSError, ValueError) as recovery_error:
                    keep_recovery = True
                    raise OSError(
                        f"Report rollback failed; original files retained at {stage}"
                    ) from recovery_error
                raise
        finally:
            if stage is not None and not keep_recovery:
                # Delete only exact entries this transaction created, never recurse.
                for name in reports:
                    for owned in (stage / name, stage / (name + ".previous")):
                        safe_local_path(owned).unlink(missing_ok=True)
                safe_local_path(stage).rmdir()
            # Close before unlink for Windows; never remove a replaced lock entry.
            lock_stream.close()
            info = safe_local_path(lock).stat()
            if (info.st_dev, info.st_ino) == (lock_identity.st_dev, lock_identity.st_ino):
                lock.unlink()


def read_completed_reports(directory: Path) -> dict[str, bytes]:
    """Read one verified generation, raising OSError while publication is incomplete."""
    directory = safe_local_path(directory)
    lock = safe_local_path(directory / ".mcp-report-export.lock")
    marker = safe_local_path(directory / COMPLETION_MANIFEST)
    if lock.exists():
        raise OSError("Report publication is in progress")
    _identity(marker)
    content = marker.read_bytes()
    try:
        manifest = json.loads(content)
        if manifest.get("schema_version") != 1 or not manifest.get("generation"):
            raise ValueError("Invalid report completion manifest")
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("Empty report completion manifest")
        result = {}
        for name, digest in files.items():
            if Path(name).name != name or name in ("", ".", "..", COMPLETION_MANIFEST):
                raise ValueError("Invalid report name")
            path = safe_local_path(directory / name)
            _identity(path)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise OSError("Report content differs from the completed generation")
            result[name] = data
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise OSError("Invalid report completion manifest") from exc
    if lock.exists() or marker.read_bytes() != content:
        raise OSError("Report generation changed while reading; retry")
    return result
