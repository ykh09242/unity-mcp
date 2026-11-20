"""Recover checkpoint and report writes through the dependency-scan CLI."""

from contextlib import contextmanager
import errno
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.verification import dependency_diff, dependency_scan
from .test_verification_dependency_scan import _assets, _commit, _large_pages, _ok, _scan


@pytest.fixture
def two_pages(tmp_path):
    project = tmp_path / "project"
    paths = [f"Assets/Item-{index}.asset" for index in range(26)]
    _assets(project, paths)
    commit = _commit(project)
    pages = _large_pages(tmp_path, paths)
    checkpoint = tmp_path / "scan.sqlite"
    return (project, commit, paths[0], checkpoint), pages


def test_sqlite_full_rolls_back_only_the_uncommitted_page(two_pages, tmp_path, monkeypatch):
    scan, pages = two_pages
    confirmed = _ok(_scan(*scan, pages[:1]))
    # Force the real pager to request more blocks than the simulated volume allows.
    document = json.loads(pages[1].read_bytes())
    document["data"]["edges"].extend({"id": f"extra-{i}"} for i in range(2000))
    pages[1].write_bytes(json.dumps(document).encode())
    setup = dependency_scan._setup

    def limited_volume(db):
        setup(db)
        count = db.execute("PRAGMA page_count").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={count}")

    output = tmp_path / "inventory.jsonl"
    with monkeypatch.context() as fault:
        fault.setattr(dependency_scan, "_setup", limited_volume)
        result = _scan(*scan, pages[1:], output)
    assert result.exit_code == 1 and "full" in result.output
    assert not output.exists()
    assert _ok(_scan(*scan)) == confirmed
    pages[0].unlink()  # Recovery must not reopen the previously admitted input.
    resumed = _ok(_scan(*scan, pages[1:], output))
    assert resumed["pages"] == 2 and resumed["issues"] == 2000
    assert resumed["inventory"]["sha256"] == sha256(output.read_bytes()).hexdigest()
    repeated = _ok(_scan(*scan, pages[1:]))
    assert repeated == {key: value for key, value in resumed.items() if key != "inventory"}


@pytest.mark.parametrize("failure", ["short_write", "delayed_enospc"])
def test_report_failure_preserves_confirmed_export_and_resumes_without_inputs(
    two_pages, tmp_path, monkeypatch, failure
):
    scan, pages = two_pages
    previous = tmp_path / "confirmed.jsonl"
    complete = _ok(_scan(*scan, pages, previous))
    expected = previous.read_bytes()
    for page in pages:
        page.unlink()
    output = tmp_path / "retry.jsonl"
    original_open = Path.open
    # Keep the real generator reachable across the retry, just as a traceback can.
    retained = []
    inventory = dependency_diff.inventory

    def retained_inventory(checkpoint):
        rows = inventory(checkpoint)
        retained.append(rows)
        return rows

    @contextmanager
    def short_writer(path, mode):
        with original_open(path, mode) as stream:
            yield SimpleNamespace(write=lambda row: stream.write(row[:-1]))

    def open_file(path, mode="r", *args, **kwargs):
        if path.name == "report" and mode == "xb":
            return short_writer(path, mode)
        return original_open(path, mode, *args, **kwargs)

    def full(_fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as fault:
        fault.setattr(dependency_diff, "inventory", retained_inventory)
        if failure == "short_write":
            fault.setattr(Path, "open", open_file)
        else:
            fault.setattr(dependency_diff.os, "fsync", full)
        failed = _scan(*scan, output=output)
    assert failed.exit_code == 1
    assert len(retained) == 1
    assert (
        "Short dependency report write" if failure == "short_write" else "No space left"
    ) in failed.output
    assert not output.exists() and previous.read_bytes() == expected
    assert _ok(_scan(*scan, output=output)) == complete
    assert output.read_bytes() == expected
