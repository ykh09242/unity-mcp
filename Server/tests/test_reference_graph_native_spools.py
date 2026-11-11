"""Native capture fixtures never touch retained captures in the real user profile."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from services.reference_graph import maintenance, native_spools as spools
from services.reference_graph import native_spool_paths as paths


EDITOR_A = "a" * 32
EDITOR_B = "b" * 32
CAPTURE = "c" * 64


def windows_token_api():
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = wintypes.HANDLE
    pointer = ctypes.c_void_p
    signatures = {
        "OpenProcessToken": [handle, wintypes.DWORD, ctypes.POINTER(handle)],
        "OpenThreadToken": [handle, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(handle)],
        "DuplicateTokenEx": [
            handle,
            wintypes.DWORD,
            pointer,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(handle),
        ],
        "GetTokenInformation": [
            handle,
            ctypes.c_int,
            pointer,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ],
        "SetTokenInformation": [handle, ctypes.c_int, pointer, wintypes.DWORD],
        "SetThreadToken": [ctypes.POINTER(handle), handle],
        "EqualSid": [pointer, pointer],
    }
    for name, arguments in signatures.items():
        function = getattr(security, name)
        function.argtypes = arguments
        function.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = handle
    kernel.GetCurrentThread.restype = handle
    kernel.CloseHandle.argtypes = [handle]
    kernel.CloseHandle.restype = wintypes.BOOL
    return security, kernel


def token_information(security, token, kind):
    size = wintypes.DWORD(0)
    security.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not security.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer


@contextmanager
def windows_user_owner():
    """Give only this thread's new fixtures a user owner, even on elevated CI."""
    security, kernel = windows_token_api()
    with ExitStack() as handles:
        primary = wintypes.HANDLE()
        # Query/duplicate the primary token; never request ADJUST_DEFAULT on it.
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0xA, ctypes.byref(primary)):
            raise ctypes.WinError(ctypes.get_last_error())
        handles.callback(kernel.CloseHandle, primary)
        previous = wintypes.HANDLE()
        if security.OpenThreadToken(kernel.GetCurrentThread(), 0xC, True, ctypes.byref(previous)):
            handles.callback(kernel.CloseHandle, previous)
        elif ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
            raise ctypes.WinError(ctypes.get_last_error())
        duplicate = wintypes.HANDLE()
        # QUERY | IMPERSONATE | ADJUST_DEFAULT; SecurityImpersonation, TokenImpersonation.
        if not security.DuplicateTokenEx(primary, 0x8C, None, 2, 2, ctypes.byref(duplicate)):
            raise ctypes.WinError(ctypes.get_last_error())
        handles.callback(kernel.CloseHandle, duplicate)
        user = token_information(security, primary, 1)  # TokenUser
        owner = ctypes.c_void_p.from_buffer(user)
        if not security.SetTokenInformation(
            duplicate, 4, ctypes.byref(owner), ctypes.sizeof(owner)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not security.SetThreadToken(None, duplicate):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not security.SetThreadToken(None, previous):
                raise ctypes.WinError(ctypes.get_last_error())


@pytest.fixture(autouse=True)
def user_owned_fixtures():
    # Runs before tmp_path: export destinations also require a user-owned parent.
    # The primary token and other threads remain untouched. Group-owned retained
    # spools still fail the real production check; this is only fixture creation.
    if os.name == "nt":
        with windows_user_owner():
            yield
    else:
        yield


def seed(root: Path, editor: str = EDITOR_A, *, legacy: bool = False) -> Path:
    directory = root / editor
    directory.mkdir(parents=True, exist_ok=True)
    (root / ".spool.lock").touch()
    location = directory / CAPTURE
    row = json.dumps({"sequence": 1, "kind": "load", "key": "retained"}).encode()
    location.with_suffix(".events").write_bytes(struct.pack("<i", len(row)) + row)
    metadata = {"runtime_session": "d" * 32, "count": 1, "state": "editor_quit", "error": None}
    if not legacy:
        metadata.update(
            format=spools.FORMAT,
            version=1,
            editor_id=editor,
            capture_id=CAPTURE,
            lease_protocol="byte-range-v1",
        )
        location.with_suffix(".lease").touch()
    location.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8-sig")
    return location


@contextmanager
def child_lease(path: Path):
    script = """
import os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
if os.name=='nt':
 import msvcrt
 msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
else:
 import fcntl
 fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,1,0)
print('locked',flush=True)
sys.stdin.readline()
os.close(fd)
"""
    with subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout.readline().strip() == "locked"
        try:
            yield
        finally:
            process.communicate("release\n", timeout=10)
            assert process.returncode == 0


def test_identity_turnover_inventory_export_then_explicit_fresh_reclaim(tmp_path):
    root = tmp_path / "spools"
    old = seed(root)
    live = seed(root, EDITOR_B)
    old_bytes = old.with_suffix(".events").read_bytes()
    with child_lease(live.with_suffix(".lease")):
        result = spools.inventory(root)
        assert [item["status"] for item in result["captures"]] == ["retained", "active"]
        assert result["stored_bytes"] > len(old_bytes)
        preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
        assert preview["outcome"] == "preview"
        destination = tmp_path / "recovered"
        assert (
            spools.export_capture(root, EDITOR_A, CAPTURE, destination=destination)["outcome"]
            == "preview"
        )
        assert not destination.exists()
        exported = spools.export_capture(
            root, EDITOR_A, CAPTURE, destination=destination, apply=True
        )
        assert exported["outcome"] == "exported"
        assert (destination / (CAPTURE + ".events")).read_bytes() == old_bytes
        assert old.with_suffix(".events").read_bytes() == old_bytes
        with pytest.raises(ValueError, match="active"):
            spools.reclaim_capture(root, EDITOR_B, CAPTURE, apply=True)
        with pytest.raises(ValueError, match="fingerprint"):
            spools.reclaim_capture(root, EDITOR_A, CAPTURE, apply=True)
        reclaimed = spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
        assert reclaimed["outcome"] == "reclaimed"
        assert not old.with_suffix(".json").exists()
        assert not old.with_suffix(".events").exists()
        assert not old.with_suffix(".lease").exists()
        assert live.with_suffix(".events").exists()
        assert (destination / (CAPTURE + ".events")).read_bytes() == old_bytes


def test_missing_owner_proof_and_live_root_lock_refuse_mutation(tmp_path, monkeypatch):
    root = tmp_path / "spools"
    location = seed(root)
    preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
    with child_lease(root / ".spool.lock"), pytest.raises(ValueError, match="active"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    real = paths._windows_owner

    def refuse(path):
        raise ValueError("owner unavailable")

    if os.name == "nt":
        monkeypatch.setattr(paths, "_windows_owner", refuse)
    else:
        monkeypatch.setattr(paths.os, "getuid", lambda: os.stat(root).st_uid + 1)
    with pytest.raises(ValueError, match="owner|owned"):
        spools.inspect_capture(root, EDITOR_A, CAPTURE)
    assert location.with_suffix(".events").exists()
    assert real


def test_stale_or_changed_freshness_proof_preserves_all_evidence(tmp_path):
    root = tmp_path / "spools"
    location = seed(root)
    preview = spools.inspect_capture(root, EDITOR_A, CAPTURE)
    with pytest.raises(ValueError, match="stale"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=int(time.time()) - spools.FRESH_SECONDS - 1,
            apply=True,
        )
    events = location.with_suffix(".events")
    events.write_bytes(events.read_bytes() + b"interrupted")
    with pytest.raises(ValueError, match="changed"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    assert location.with_suffix(".json").exists()


def test_replaced_file_inode_refuses_even_when_bytes_match(tmp_path):
    root = tmp_path / "spools"
    location = seed(root)
    preview = spools.inspect_capture(root, EDITOR_A, CAPTURE)
    original = location.with_suffix(".events")
    renamed = original.with_name("retained-original")
    original.rename(renamed)
    original.write_bytes(renamed.read_bytes())
    with pytest.raises(ValueError, match="changed"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    assert original.exists() and renamed.exists()


def test_linked_capture_and_directory_swap_are_refused(tmp_path):
    root = tmp_path / "spools"
    location = seed(root)
    preview = spools.inspect_capture(root, EDITOR_A, CAPTURE)
    directory = root / EDITOR_A
    renamed = root / "retained"
    directory.rename(renamed)
    try:
        directory.symlink_to(renamed, target_is_directory=True)
    except OSError:
        # Windows junctions require no symbolic-link privilege.
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(directory), str(renamed)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="linked"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    assert (renamed / location.with_suffix(".events").name).exists()


def test_legacy_evidence_requires_stopped_assertion_and_cannot_be_reclaimed(tmp_path):
    root = tmp_path / "spools"
    location = seed(root, legacy=True)
    assert spools.inventory(root)["captures"][0]["status"] == "legacy_unverified"
    with pytest.raises(ValueError, match="all-editors-stopped"):
        spools.export_capture(root, EDITOR_A, CAPTURE, destination=tmp_path / "export")
    exported = spools.export_capture(
        root,
        EDITOR_A,
        CAPTURE,
        destination=tmp_path / "export",
        all_editors_stopped=True,
        apply=True,
    )
    assert not exported["reclaim_supported"]
    with pytest.raises((ValueError, FileNotFoundError)):
        spools.reclaim_capture(root, EDITOR_A, CAPTURE, apply=True)
    assert location.with_suffix(".events").exists()


def test_metadata_identity_and_hardlinks_are_not_capture_ownership(tmp_path):
    root = tmp_path / "spools"
    location = seed(root)
    metadata = location.with_suffix(".json")
    payload = json.loads(metadata.read_text(encoding="utf-8-sig"))
    payload["editor_id"] = EDITOR_B
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        spools.inspect_capture(root, EDITOR_A, CAPTURE)
    payload["editor_id"] = EDITOR_A
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    os.link(location.with_suffix(".events"), tmp_path / "hardlink")
    with pytest.raises(ValueError, match="unlinked"):
        spools.inspect_capture(root, EDITOR_A, CAPTURE)


def test_cli_inventory_and_preview_are_json_read_only(tmp_path, capsys):
    root = tmp_path / "spools"
    location = seed(root)
    assert maintenance.main(["native-list", "--directory", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["captures"][0]["editor"] == EDITOR_A
    assert (
        maintenance.main(
            ["native-reclaim", "--directory", str(root), "--editor", EDITOR_A, "--capture", CAPTURE]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "preview" and result["fingerprint"]
    assert location.with_suffix(".events").exists()


def test_full_inspection_keeps_capture_lease_exclusive_to_other_processes(tmp_path):
    root = tmp_path / "spools"
    location = seed(root)
    probe = """
import os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try:
 if os.name=='nt':
  import msvcrt
  msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
 else:
  import fcntl
  fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,1,0)
except OSError:
 print('busy')
else:
 print('admitted')
finally:
 os.close(fd)
"""
    with paths.lease(location.with_suffix(".lease")):
        spools._inspect(spools.Selection(root, EDITOR_A, CAPTURE))
        result = subprocess.run(
            [sys.executable, "-c", probe, str(location.with_suffix(".lease"))],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "busy"


def test_incidental_legacy_lease_does_not_bypass_stopped_editor_assertion(tmp_path):
    root = tmp_path / "spools"
    location = seed(root, legacy=True)
    location.with_suffix(".lease").touch()
    with pytest.raises(ValueError, match="all-editors-stopped"):
        spools.inspect_capture(root, EDITOR_A, CAPTURE)
    with pytest.raises(ValueError, match="all-editors-stopped"):
        spools.export_capture(root, EDITOR_A, CAPTURE, destination=tmp_path / "export", apply=True)
    assert not (tmp_path / "export").exists()
    assert (
        spools.inspect_capture(root, EDITOR_A, CAPTURE, all_editors_stopped=True)[
            "reclaim_supported"
        ]
        is False
    )


def test_reclaim_hashing_does_not_hold_the_global_producer_fence(tmp_path, monkeypatch):
    root = tmp_path / "spools"
    seed(root)
    real = spools._inspect

    def inspect_while_other_producer_can_claim(selection):
        with paths.lease(root / ".spool.lock"):
            return real(selection)

    monkeypatch.setattr(spools, "_inspect", inspect_while_other_producer_can_claim)
    preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
    assert (
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )["outcome"]
        == "reclaimed"
    )


def test_reclaim_prunes_empty_owners_before_reduced_inventory_limit_is_exhausted(
    tmp_path, monkeypatch
):
    root = tmp_path / "spools"
    monkeypatch.setattr(spools, "MAX_FILES", 8)
    for index in range(12):
        editor = f"{index:032x}"
        seed(root, editor)
        assert len(spools.inventory(root)["captures"]) == 1
        preview = spools.reclaim_capture(root, editor, CAPTURE)
        spools.reclaim_capture(
            root,
            editor,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
        assert not (root / editor).exists()
    assert spools.inventory(root)["captures"] == []


@pytest.mark.parametrize("kind", ["file", "directory", "capture"])
def test_reclaim_preserves_other_same_owner_contents(tmp_path, kind):
    root = tmp_path / "spools"
    seed(root)
    retained = root / EDITOR_A / ("unknown" if kind != "capture" else "e" * 64 + ".events")
    if kind == "directory":
        retained.mkdir()
    else:
        retained.write_bytes(b"retained")
    preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
    spools.reclaim_capture(
        root,
        EDITOR_A,
        CAPTURE,
        fingerprint=preview["fingerprint"],
        inspected_at=preview["inspected_at"],
        apply=True,
    )
    assert retained.exists()
    if kind != "directory":
        assert retained.read_bytes() == b"retained"


def test_empty_owner_pruning_keeps_root_fence_until_removal(tmp_path, monkeypatch):
    root = tmp_path / "spools"
    seed(root)
    directory = root / EDITOR_A
    preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
    rmdir, observed = Path.rmdir, []
    probe = """
import sys
from pathlib import Path
from services.reference_graph.native_spool_paths import lease
root = Path(sys.argv[1])
try:
    with lease(root / '.spool.lock'):
        owner = root / sys.argv[2]
        owner.mkdir(exist_ok=True)
        (owner / 'new-capture').write_bytes(b'new')
except ValueError as error:
    if 'active or maintenance' not in str(error):
        raise
    print('busy')
else:
    print('created')
"""

    def competing_producer():
        result = subprocess.run(
            [sys.executable, "-B", "-c", probe, str(root), EDITOR_A],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()

    def remove(path):
        if path == directory:
            observed.append(competing_producer())
        return rmdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rmdir", remove)
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    assert observed == ["busy"]
    assert competing_producer() == "created"
    assert (directory / "new-capture").read_bytes() == b"new"


def test_owner_replacement_is_rejected_before_empty_directory_probe(tmp_path, monkeypatch):
    root = tmp_path / "spools"
    seed(root)
    directory = root / EDITOR_A
    preview = spools.reclaim_capture(root, EDITOR_A, CAPTURE)
    prune, scandir, probed = spools._prune_empty_owner, spools.os.scandir, []

    def inspect(path):
        if Path(path) == directory:
            probed.append(path)
        return scandir(path)

    def replaced(selection, report):
        directory.rename(root / "retained-original")
        directory.mkdir()
        probed.clear()
        return prune(selection, report)

    monkeypatch.setattr(spools, "_prune_empty_owner", replaced)
    monkeypatch.setattr(spools.os, "scandir", inspect)
    with pytest.raises(ValueError, match="directory changed"):
        spools.reclaim_capture(
            root,
            EDITOR_A,
            CAPTURE,
            fingerprint=preview["fingerprint"],
            inspected_at=preview["inspected_at"],
            apply=True,
        )
    assert not probed
    assert directory.exists() and (root / "retained-original").exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows token and file ownership")
def test_fixture_owner_is_user_and_nested_scope_restores_existing_token(tmp_path):
    security, kernel = windows_token_api()
    primary, thread = wintypes.HANDLE(), wintypes.HANDLE()
    assert security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(primary))
    try:
        assert security.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(thread))
        try:
            primary_before = token_information(security, primary, 4)
            thread_before = token_information(security, thread, 10)  # TokenStatistics includes ID.
            with pytest.raises(RuntimeError, match="fixture body failed"):
                with windows_user_owner():
                    created = tmp_path / "owned"
                    created.write_bytes(b"fixture")
                    paths._windows_owner(
                        created
                    )  # Real GetNamedSecurityInfoW/TokenUser comparison.
                    raise RuntimeError("fixture body failed")
            current = wintypes.HANDLE()
            assert security.OpenThreadToken(
                kernel.GetCurrentThread(), 8, True, ctypes.byref(current)
            )
            try:
                assert token_information(security, current, 10).raw[:8] == thread_before.raw[:8]
            finally:
                kernel.CloseHandle(current)
            primary_after = token_information(security, primary, 4)
            assert security.EqualSid(
                ctypes.c_void_p.from_buffer(primary_before),
                ctypes.c_void_p.from_buffer(primary_after),
            )
        finally:
            kernel.CloseHandle(thread)
    finally:
        kernel.CloseHandle(primary)


@pytest.mark.parametrize(
    "owner", [0x111, 0x222, 0x333], ids=["user", "default-group", "other-user"]
)
def test_windows_owner_requires_token_user_not_default_owner(tmp_path, monkeypatch, owner):
    security, kernel = Mock(), Mock()

    def get_owner(_path, _kind, _flags, owner_out, *_unused):
        owner_out._obj.value = owner
        _unused[-1]._obj.value = 0x999
        return 0

    def open_token(_process, _access, token_out):
        token_out._obj.value = 0x777
        return True

    def get_information(_token, kind, buffer, _size, size_out):
        # Even if TokenOwner matches a default group, it is not individual ownership.
        size_out._obj.value = ctypes.sizeof(ctypes.c_void_p)
        if buffer is None:
            return False
        ctypes.c_void_p.from_buffer(buffer).value = 0x111 if kind == 1 else 0x222
        return True

    security.GetNamedSecurityInfoW.side_effect = get_owner
    security.OpenProcessToken.side_effect = open_token
    security.GetTokenInformation.side_effect = get_information
    security.EqualSid.side_effect = lambda left, right: left.value == right.value
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **_kwargs: security if name == "advapi32" else kernel,
        raising=False,
    )
    evidence = tmp_path / "evidence"
    evidence.write_bytes(b"retained")
    if owner == 0x111:
        paths._windows_owner(evidence)
    else:
        with pytest.raises(ValueError, match="current OS user"):
            paths._windows_owner(evidence)
    assert evidence.read_bytes() == b"retained"
    assert all(call.args[1] == 1 for call in security.GetTokenInformation.call_args_list)
    assert kernel.CloseHandle.call_args.args[0].value == 0x777
    assert kernel.LocalFree.call_args.args[0].value == 0x999


@pytest.mark.parametrize(
    "previous", [None, 0x333], ids=["no-thread-token", "existing-thread-token"]
)
@pytest.mark.parametrize("failure", [None, "duplicate", "set-owner", "set-thread", "body"])
def test_fixture_token_cleanup_on_setup_and_body_failures(monkeypatch, previous, failure):
    security, kernel = Mock(), Mock()
    error = [1008]  # ERROR_NO_TOKEN when no prior impersonation exists.

    def open_handle(value, output):
        if value is None:
            return False
        output._obj.value = value
        return True

    def duplicate(*arguments):
        error[0] = 5
        return False if failure == "duplicate" else open_handle(0x222, arguments[-1])

    security.OpenProcessToken.side_effect = lambda *args: open_handle(0x111, args[-1])
    security.OpenThreadToken.side_effect = lambda *args: open_handle(previous, args[-1])
    security.DuplicateTokenEx.side_effect = duplicate
    security.SetTokenInformation.return_value = failure != "set-owner"
    security.SetThreadToken.side_effect = lambda _thread, token: (
        failure != "set-thread" or token.value != 0x222
    )
    buffer = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p))
    ctypes.c_void_p.from_buffer(buffer).value = 0x444
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "windows_token_api", lambda: (security, kernel))
    monkeypatch.setattr(module, "token_information", lambda *_args: buffer)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "fixture failure"), raising=False
    )

    def exercise():
        with windows_user_owner():
            if failure == "body":
                raise RuntimeError("fixture body failed")

    if failure in {"duplicate", "set-owner", "set-thread"}:
        with pytest.raises(OSError, match="fixture failure"):
            exercise()
    elif failure == "body":
        with pytest.raises(RuntimeError, match="fixture body failed"):
            exercise()
    else:
        exercise()
    expected_closed = {0x111}
    if previous:
        expected_closed.add(previous)
    if failure != "duplicate":
        expected_closed.add(0x222)
    assert {call.args[0].value for call in kernel.CloseHandle.call_args_list} == expected_closed
    if failure in {None, "body"}:
        assert security.SetThreadToken.call_args.args[1].value == previous
    for call in security.SetTokenInformation.call_args_list:
        assert call.args[0].value == 0x222  # Only the duplicate token may be adjusted.
