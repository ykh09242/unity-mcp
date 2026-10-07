"""Targeted status reads preserve suffix identity and newest-file semantics."""

import json
import os
from pathlib import Path

import pytest

import transport.legacy.unity_connection as uc


@pytest.fixture
def status_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(uc.Path, "home", lambda: tmp_path)
    directory = tmp_path / ".unity-mcp"
    directory.mkdir()
    return uc.read_status_file, directory


def write_status(directory, hash_value, data, timestamp):
    path = directory / f"unity-mcp-status-{hash_value}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path


def test_unique_target_does_not_stat_unrelated_editor_statuses(status_reader, monkeypatch):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    for number in range(10):
        write_status(directory, f"{number:08x}", {"reloading": False}, number + 10)
    stat_calls = []
    original_stat = Path.stat

    def tracked_stat(path, *args, **kwargs):
        if path.parent == directory:
            stat_calls.append(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", tracked_stat)
    assert read_status("deadbeef") == {"reloading": True}
    assert stat_calls == [], f"Target read statted {len(stat_calls)} editor statuses"


@pytest.mark.parametrize("selector", ["deadbeef", "beef"])
def test_newer_matching_legacy_suffix_keeps_precedence(status_reader, selector):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": False}, 1)
    write_status(directory, "cafefeeddeadbeef", {"reloading": True}, 2)
    write_status(directory, "ffffffff", {"reloading": False}, 3)
    assert read_status(selector) == {"reloading": True}


def test_untargeted_read_still_selects_newest_editor(status_reader):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    write_status(directory, "other", {"reloading": False}, 2)
    assert read_status() == {"reloading": False}


@pytest.mark.parametrize("selector", ["missing", "../outside", "*", "?", "[deadbeef]"])
def test_missing_or_unsafe_target_never_reads_another_editor(status_reader, selector):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    assert read_status(selector) is None


def test_malformed_selected_status_does_not_fall_back_to_another_editor(status_reader):
    read_status, directory = status_reader
    (directory / "unity-mcp-status-deadbeef.json").write_text("{", encoding="utf-8")
    write_status(directory, "other", {"reloading": True}, 3)
    assert read_status("deadbeef") is None
