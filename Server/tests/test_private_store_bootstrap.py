"""Actual private-store interruption, ownership and concurrent startup contracts."""

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from services.execution_receipt_storage import ReceiptFiles, _MARKER
from services.execution_receipt_identity import ReceiptUnavailable
from services.test_wait_journal import JournalUnavailable, WaitJournal
from tests.timing import watchdog_timeout


def open_store(kind, root):
    return ReceiptFiles(root, private_root=True) if kind == "receipt" else WaitJournal(root)


def identity(kind, store):
    return store.store_id if kind == "receipt" else store.installation_id


def marker(kind):
    return _MARKER if kind == "receipt" else WaitJournal._MARKER


def private_write(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.mark.parametrize("kind", ["receipt", "wait"])
@pytest.mark.parametrize(
    "phase",
    [
        "lock",
        "owner_empty",
        "owner_partial",
        "owner_complete",
        "installation_empty",
        "installation_partial",
        "installation_complete",
    ],
)
def test_interrupted_bootstrap_can_reopen_without_resetting_identity(tmp_path, kind, phase):
    root = tmp_path / "owned-store"
    root.mkdir(mode=0o700)
    private_write(root / "writer.lock", b"1")
    expected = "a" * 32
    if phase.startswith("owner_"):
        values = {
            "owner_empty": b"",
            "owner_partial": marker(kind)[:1],
            "owner_complete": marker(kind),
        }
        private_write(root / "owner.stage", values[phase])
    elif phase.startswith("installation_"):
        private_write(root / "owner", marker(kind))
        values = {
            "installation_empty": b"",
            "installation_partial": b"a",
            "installation_complete": (expected + "\n").encode(),
        }
        private_write(root / "installation.stage", values[phase])
    store = open_store(kind, root)
    first = identity(kind, store)
    store.close()
    if phase == "installation_complete":
        assert first == expected
    reopened = open_store(kind, root)
    try:
        assert identity(kind, reopened) == first
        assert {path.name for path in root.iterdir()} == {"owner", "installation", "writer.lock"}
    finally:
        reopened.close()


_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from services.execution_receipt_storage import ReceiptFiles
from services.test_wait_journal import WaitJournal
kind, root, phase = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
real_open, real_mkdir, real_sync, real_replace = os.open, os.mkdir, os.fsync, os.replace
paths = {}
def crash(): os._exit(73)
def opening(path, flags, *args, **kwargs):
    fd = real_open(path, flags, *args, **kwargs)
    paths[fd] = Path(path)
    if flags & os.O_CREAT:
        if Path(path) == root / "writer.lock" and phase == "lock_open": crash()
        for name in ("owner", "installation"):
            if Path(path) == root / (name + ".stage"):
                if name == "installation" and phase.startswith("recovery_"): crash()
                if phase == name + "_open": crash()
                if phase == name + "_partial":
                    os.write(fd, b"u" if name == "owner" else b"a")
                    real_sync(fd)
                    crash()
    return fd
def mkdir(path, *args, **kwargs):
    result = real_mkdir(path, *args, **kwargs)
    if Path(path) == root and phase == "directory": crash()
    return result
def sync(fd):
    result = real_sync(fd)
    for name in ("owner", "installation"):
        if paths.get(fd) == root / (name + ".stage") and phase == name + "_flushed": crash()
    return result
def replace(source, target):
    if Path(target) == root / "installation" and phase == "recovery_before": crash()
    result = real_replace(source, target)
    if Path(target) == root / "installation" and phase == "recovery_after": crash()
    for name in ("owner", "installation"):
        if Path(target) == root / name and phase == name + "_published": crash()
    return result
os.open, os.mkdir, os.fsync, os.replace = opening, mkdir, sync, replace
if os.name == "nt":
    import msvcrt
    lock = msvcrt.locking
    def locking(fd, mode, count):
        result = lock(fd, mode, count)
        if phase == "locked" and mode == msvcrt.LK_NBLCK: crash()
        return result
    msvcrt.locking = locking
else:
    import fcntl
    lock = fcntl.flock
    def locking(fd, flags):
        result = lock(fd, flags)
        if phase == "locked" and flags & fcntl.LOCK_EX: crash()
        return result
    fcntl.flock = locking
store = ReceiptFiles(root, private_root=True) if kind == "receipt" else WaitJournal(root)
store.close()
"""


@pytest.mark.parametrize("kind", ["receipt", "wait"])
@pytest.mark.parametrize(
    "phase",
    [
        "directory",
        "lock_open",
        "locked",
        "owner_open",
        "owner_partial",
        "owner_flushed",
        "owner_published",
        "installation_open",
        "installation_partial",
        "installation_flushed",
        "installation_published",
    ],
)
def test_real_process_exit_at_bootstrap_boundaries_is_recoverable(tmp_path, kind, phase):
    root = tmp_path / "crashed-store"
    result = subprocess.run(
        [sys.executable, "-B", "-c", _CHILD, kind, str(root), phase],
        cwd=tmp_path,
        capture_output=True,
        timeout=watchdog_timeout(),
        check=False,
    )
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    previous = None
    for name in ("installation", "installation.stage"):
        path = root / name
        if path.exists() and path.stat().st_size == 33:
            previous = path.read_text().strip()
    store = open_store(kind, root)
    try:
        current = identity(kind, store)
        if previous is not None:
            assert current == previous
        assert len(current) == 32
        assert not list(root.glob("*.stage"))
    finally:
        store.close()
    store = open_store(kind, root)
    try:
        assert identity(kind, store) == current
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["receipt", "wait"])
@pytest.mark.parametrize(
    "foreign",
    ["file", "owner", "owner_stage", "installation", "installation_stage", "lock", "record"],
)
def test_foreign_or_tampered_bootstrap_is_preserved(tmp_path, kind, foreign):
    root = tmp_path / "foreign-store"
    root.mkdir(mode=0o700)
    if foreign in {"installation", "installation_stage", "record"}:
        private_write(root / "owner", marker(kind))
    name, value = {
        "file": ("unrelated.txt", b"preserve"),
        "owner": ("owner", b"u"),
        "owner_stage": ("owner.stage", b"foreign"),
        "installation": ("installation", b"broken"),
        "installation_stage": ("installation.stage", b"not-a-hex-id"),
        "lock": ("writer.lock", b"X"),
        "record": ("a" * 32 + ".json", b"existing-record"),
    }[foreign]
    private_write(root / name, value)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises((ReceiptUnavailable, JournalUnavailable)):
        open_store(kind, root)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


_CONCURRENT = r"""
import json, sys, time
from pathlib import Path
from services.execution_receipt_storage import ReceiptFiles
from services.test_wait_journal import WaitJournal
kind, root, control, number, seconds = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], float(sys.argv[5])
deadline = time.monotonic() + seconds
while not (control / "start").exists():
    if time.monotonic() > deadline: raise TimeoutError("owned startup barrier")
    time.sleep(.01)
store = None
try:
    try:
        store = ReceiptFiles(root, private_root=True) if kind == "receipt" else WaitJournal(root)
        result = {"opened": True, "id": store.store_id if kind == "receipt" else store.installation_id}
    except Exception as error:
        result = {"opened": False, "error": type(error).__name__}
    stage = control / (number + ".stage")
    stage.write_text(json.dumps(result))
    stage.replace(control / (number + ".json"))
    while store is not None and not (control / "release").exists():
        if time.monotonic() > deadline: raise TimeoutError("owned release barrier")
        time.sleep(.01)
finally:
    if store is not None: store.close()
"""


@pytest.mark.parametrize("kind", ["receipt", "wait"])
def test_concurrent_first_processes_keep_one_writer_and_identity(tmp_path, kind):
    root, control = tmp_path / "concurrent-store", tmp_path / "control"
    control.mkdir()
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                _CONCURRENT,
                kind,
                str(root),
                str(control),
                str(i),
                str(watchdog_timeout()),
            ],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(2)
    ]
    try:
        (control / "start").touch()
        deadline = time.monotonic() + watchdog_timeout()
        while not all((control / f"{i}.json").exists() for i in range(2)):
            assert time.monotonic() < deadline, "Owned startup processes did not report"
            time.sleep(0.01)
        outcomes = [json.loads((control / f"{i}.json").read_text()) for i in range(2)]
        winners = [item for item in outcomes if item["opened"]]
        assert len(winners) == 1, outcomes
        expected = winners[0]["id"]
    finally:
        (control / "release").touch()
        for process in processes:
            try:
                _, stderr = process.communicate(timeout=watchdog_timeout())
                assert process.returncode == 0, stderr.decode(errors="replace")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=watchdog_timeout())
    store = open_store(kind, root)
    try:
        assert identity(kind, store) == expected
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["receipt", "wait"])
@pytest.mark.parametrize("phase", ["recovery_before", "recovery_after"])
def test_second_process_exit_during_recovery_preserves_complete_candidate(tmp_path, kind, phase):
    root = tmp_path / "recovering-store"
    root.mkdir(mode=0o700)
    expected = "b" * 32
    private_write(root / "owner", marker(kind))
    private_write(root / "writer.lock", b"1")
    private_write(root / "installation.stage", (expected + "\n").encode())
    result = subprocess.run(
        [sys.executable, "-B", "-c", _CHILD, kind, str(root), phase],
        cwd=tmp_path,
        capture_output=True,
        timeout=watchdog_timeout(),
        check=False,
    )
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    for _ in range(2):
        store = open_store(kind, root)
        try:
            assert identity(kind, store) == expected
        finally:
            store.close()
