"""Native capture fixtures never touch retained captures in the real user profile."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

import pytest

from services.reference_graph import maintenance, native_spools as spools
from services.reference_graph import native_spool_paths as paths


EDITOR_A = "a" * 32
EDITOR_B = "b" * 32
CAPTURE = "c" * 64


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
