"""Guarded publication of a report pair with rollback on an interrupted write."""

import os
from pathlib import Path
import stat
import tempfile

from cli.utils.play_scenario_analysis_io import safe_local_path


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
    for name in names:
        _identity(directory / name)
    directory.mkdir(parents=True, exist_ok=True)
    safe_local_path(directory)
    descriptor, probe = tempfile.mkstemp(prefix=".mcp-write-check-", dir=directory)
    os.close(descriptor)
    safe_local_path(Path(probe)).unlink()
    return directory


def publish_reports(directory: Path, reports: dict[str, bytes]) -> None:
    """Serialize writers and restore the previous pair if staging or publication fails.

    A recovery failure retains the unique stage, including any original files. The
    fixed output names are compatible with existing CI consumers; readers should
    consume the pair after this call completes, not during publication.
    """
    directory = preflight_report_output(directory, tuple(reports))
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
                for name in reports:
                    target = safe_local_path(directory / name)
                    if _identity(target) != before[name]:
                        raise OSError("Report changed before publication")
                    if before[name] is not None:
                        os.replace(target, safe_local_path(stage / (name + ".previous")))
                        moved.append(name)
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
