"""Exercise portable saved evidence export with the checker's synthetic records."""

import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tracemalloc
from types import ModuleType

import pytest

from tools.tests.test_check_release_evidence import Bundle, bundle  # noqa: F401


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return importlib.import_module("export_release_evidence")


def test_export_remains_valid_after_original_records_are_removed(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    destination = tmp_path / "retained"
    exporter.export_bundle(
        exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
    )
    for source in tmp_path.glob("*.txt"):
        source.unlink()
    bundle.evidence.unlink()
    notes.unlink()
    exporter.validate_bundle(bundle.repo, destination)
    manifest = json.loads((destination / "bundle.json").read_bytes())
    assert manifest["components"] == {
        "unity": bundle.package,
        "server": bundle.server,
        "skill": bundle.skill,
    }


@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_damaged_input_is_rejected_before_destination_creation(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path, damage: str
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    source = tmp_path / "workflow.txt"
    if damage == "missing":
        source.unlink()
    else:
        source.write_bytes(b"modified")
    destination = tmp_path / "retained"
    with pytest.raises(exporter.checker.EvidenceError):
        exporter.export_bundle(
            exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
        )
    assert not destination.exists()


@pytest.mark.parametrize("filename", ["notes.md", "evidence.json", "record"])
@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_retained_file_damage_fails_offline_verification(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path, filename: str, damage: str
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    destination = tmp_path / "retained"
    exporter.export_bundle(
        exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
    )
    victim = next(destination.glob("*.bin")) if filename == "record" else destination / filename
    if damage == "missing":
        victim.unlink()
    else:
        victim.write_bytes(b"modified")
    with pytest.raises(exporter.checker.EvidenceError):
        exporter.validate_bundle(bundle.repo, destination)


def test_destination_is_never_overwritten(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    destination = tmp_path / "retained"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_bytes(b"original")
    with pytest.raises(exporter.checker.EvidenceError, match="already exists"):
        exporter.export_bundle(
            exporter.ExportRequest(
                bundle.repo, bundle.package, tmp_path / "missing", bundle.evidence, destination
            )
        )
    assert sentinel.read_bytes() == b"original"
    assert list(destination.iterdir()) == [sentinel]


def test_parent_traversal_destination_is_rejected(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    destination = tmp_path / ".." / "escaped"
    with pytest.raises(exporter.checker.EvidenceError, match="traversal"):
        exporter.export_bundle(
            exporter.ExportRequest(
                bundle.repo, bundle.package, tmp_path / "missing", bundle.evidence, destination
            )
        )
    assert not (tmp_path.parent / "escaped").exists()


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_linked_destination_parent_is_rejected(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path, link_kind: str
) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    linked = tmp_path / "linked"
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction regression")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(target)],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
    else:
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"Directory symlinks unavailable: {error}")
    with pytest.raises(exporter.checker.EvidenceError, match="Linked"):
        exporter.export_bundle(
            exporter.ExportRequest(
                bundle.repo,
                bundle.package,
                tmp_path / "missing",
                bundle.evidence,
                linked / "escaped",
            )
        )
    assert list(target.iterdir()) == []


def test_missing_required_job_records_are_not_reconstructed(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    index = json.loads(bundle.evidence.read_bytes())
    del index["evidence"]["proof"]["files"]["job"]
    bundle.evidence.write_text(json.dumps(index), encoding="utf-8")
    destination = tmp_path / "retained"
    with pytest.raises(exporter.checker.EvidenceError, match="Required saved file roles"):
        exporter.export_bundle(
            exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
        )
    assert not destination.exists()


def test_component_substitution_fails_against_committed_pins(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    destination = tmp_path / "retained"
    exporter.export_bundle(
        exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
    )
    manifest_path = destination / "bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["components"]["server"] = bundle.skill
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(exporter.checker.EvidenceError, match="committed package pins"):
        exporter.validate_bundle(bundle.repo, destination)


def test_cli_exports_and_verifies_without_network(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "prepared.md"
    notes.write_text(bundle.notes(), encoding="utf-8")
    destination = tmp_path / "retained"
    assert (
        exporter.main(
            [
                "export",
                "--repo",
                str(bundle.repo),
                "--package-commit",
                bundle.package,
                "--notes",
                str(notes),
                "--evidence-index",
                str(bundle.evidence),
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    assert exporter.main(["verify", "--repo", str(bundle.repo), "--bundle", str(destination)]) == 0
    assert "no release was published" in capsys.readouterr().out


def test_export_memory_does_not_grow_with_total_saved_log_bytes(
    bundle: Bundle, exporter: ModuleType, tmp_path: Path
) -> None:
    # These are valid independent saved records, not mocked checker/copy internals.
    template = json.loads(bundle.evidence.read_bytes())["evidence"]["proof"]
    original_log = (tmp_path / template["files"]["log"]["path"]).read_bytes()
    entries = {}
    for number in range(24):
        log = (
            original_log
            + f"2026-10-16T12:00:02Z record {number} ".encode()
            + b"x" * (2 * 1024 * 1024)
            + b"\n"
        )
        log_path = tmp_path / f"job-{number}.txt"
        log_path.write_bytes(log)
        entry = json.loads(json.dumps(template))
        pointer = {"path": log_path.name, "sha256": hashlib.sha256(log).hexdigest()}
        entry["files"]["log"] = pointer
        entry["files"]["checkout"] = pointer
        entries[f"proof-{number}"] = entry
    del log
    bundle.evidence.write_text(
        json.dumps({"schema_version": 1, "evidence": entries}), encoding="utf-8"
    )
    prepared = bundle.notes()
    row = next(line for line in prepared.splitlines() if line.startswith("| Fixture capability"))
    prepared = prepared.replace(
        row, "\n".join(row.replace("[proof]", f"[proof-{number}]") for number in range(24))
    )
    notes = tmp_path / "prepared.md"
    notes.write_text(prepared, encoding="utf-8")
    destination = tmp_path / "retained"
    tracemalloc.start()
    try:
        exporter.export_bundle(
            exporter.ExportRequest(bundle.repo, bundle.package, notes, bundle.evidence, destination)
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Total unique source logs exceed 48 MiB; working memory should cover one job.
    assert peak < 36 * 1024 * 1024, f"Export retained aggregate log memory: {peak} bytes"
    exporter.validate_bundle(bundle.repo, destination)
