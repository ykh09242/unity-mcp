"""Real filesystem coverage for scanner path and per-project state contracts."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture
def scanner_module(tmp_path, monkeypatch):
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    # Load the complete production module without importing unrelated services.
    source = Path(__file__).resolve().parents[2] / "src/services/state/external_changes_scanner.py"
    spec = importlib.util.spec_from_file_location("scanner_targeting_under_test", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _write_at(path, timestamp):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, ns=(timestamp, timestamp))


def _project(path, dependency):
    (path / "Assets").mkdir(parents=True)
    manifest = path / "Packages/manifest.json"
    _write_at(manifest, 1_000_000_000)
    manifest.write_text(json.dumps({"dependencies": {"local": dependency}}), encoding="utf-8")
    os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
    return path


def _scanner(module, *, scan_interval_ms=0):
    # pytest updates this variable again after fixture setup; activate only our scanner calls.
    os.environ.pop("PYTEST_CURRENT_TEST", None)
    return module.ExternalChangesScanner(scan_interval_ms=scan_interval_ms)


@pytest.mark.parametrize("form", ["uri", "native", "relative"])
def test_local_package_path_forms_detect_changes(tmp_path, scanner_module, form):
    # Windows native paths and all relative paths must retain literal percent escapes.
    name = (
        "Package with spaces %20 literal"
        if form != "native" or os.name == "nt"
        else "Package with spaces % literal"
    )
    package = tmp_path / name
    target = package / "package.json"
    _write_at(target, 2_000_000_000)
    if form == "uri":
        dependency = package.as_uri()
    elif form == "native":
        dependency = "file:" + str(package)
    else:
        dependency = "file:../../" + package.name
    project = _project(tmp_path / "Project", dependency)
    scanner = _scanner(scanner_module)
    scanner.set_project_root("one", str(project))
    assert scanner.update_and_get("one")["external_changes_dirty"] is False
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("one")["external_changes_dirty"] is True


@pytest.mark.skipif(os.name == "nt", reason="Absolute single-slash native paths use POSIX syntax")
def test_posix_native_package_path_preserves_literal_percent_escape(tmp_path, scanner_module):
    package = tmp_path / "Package%20literal"
    target = package / "package.json"
    _write_at(target, 2_000_000_000)
    project = _project(tmp_path / "Project", "file:" + str(package))
    scanner = _scanner(scanner_module)
    scanner.set_project_root("one", str(project))
    assert scanner.update_and_get("one")["external_changes_dirty"] is False
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("one")["external_changes_dirty"] is True


def test_empty_file_dependency_does_not_add_manifest_directory(tmp_path, scanner_module):
    project = _project(tmp_path / "Project", "file: ")
    scanner = _scanner(scanner_module)
    scanner.set_project_root("one", str(project))
    scanner.update_and_get("one")
    assert scanner._get_state("one").extra_roots == []


def test_changed_project_replaces_cache_and_mtime_baseline(tmp_path, scanner_module):
    old_package = tmp_path / "OldPackage"
    new_package = tmp_path / "NewPackage"
    _write_at(old_package / "package.json", 9_000_000_000)
    target = new_package / "package.json"
    _write_at(target, 2_000_000_000)
    old_project = _project(tmp_path / "Old", "file:../../OldPackage")
    new_project = _project(tmp_path / "New", "file:../../NewPackage")
    scanner = _scanner(scanner_module)
    scanner.set_project_root("one", str(old_project))
    scanner.update_and_get("one")
    scanner.set_project_root("one", str(new_project))
    assert scanner.update_and_get("one")["external_changes_dirty"] is False
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("one")["external_changes_dirty"] is True
    assert [Path(p) for p in scanner._get_state("one").extra_roots] == [new_package.resolve()]


def test_changed_root_resets_dirty_timestamps_and_throttle(tmp_path, scanner_module, monkeypatch):
    old_project = _project(tmp_path / "Old", "file:")
    new_project = _project(tmp_path / "New", "file:")
    target = old_project / "Assets/asset.txt"
    _write_at(target, 2_000_000_000)
    now = [1000]
    monkeypatch.setattr(scanner_module, "_now_unix_ms", lambda: now[0])
    scanner = _scanner(scanner_module, scan_interval_ms=1500)
    scanner.set_project_root("one", str(old_project))
    scanner.update_and_get("one")
    scanner.clear_dirty("one")
    now[0] = 2500
    scanner.update_and_get("one")
    now[0] = 4000
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("one")["external_changes_dirty"] is True
    scanner.set_project_root("one", str(new_project))
    observed = scanner.update_and_get("one")
    assert observed == {
        "external_changes_dirty": False,
        "external_changes_last_seen_unix_ms": None,
        "dirty_since_unix_ms": None,
        "last_cleared_unix_ms": None,
    }
    assert scanner._get_state("one").last_seen_mtime_ns == 1_000_000_000


def test_same_root_and_missing_root_preserve_dirty_state_and_throttle(
    tmp_path, scanner_module, monkeypatch
):
    project = _project(tmp_path / "Project", "file:")
    target = project / "Assets/asset.txt"
    _write_at(target, 2_000_000_000)
    now = [1000]
    monkeypatch.setattr(scanner_module, "_now_unix_ms", lambda: now[0])
    scanner = _scanner(scanner_module, scan_interval_ms=1500)
    scanner.set_project_root("one", str(project))
    scanner.update_and_get("one")
    now[0] = 2500
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("one")["external_changes_dirty"] is True
    before = vars(scanner._get_state("one")).copy()
    before.pop("project_root")
    for root in (str(project), str(project) + os.sep, None, ""):
        scanner.set_project_root("one", root)
        scanner.update_and_get("one")
        after = vars(scanner._get_state("one")).copy()
        assert Path(after.pop("project_root")) == project
        assert after == before


def test_root_reassignment_does_not_change_other_instance(tmp_path, scanner_module):
    first = _project(tmp_path / "First", "file:")
    second = _project(tmp_path / "Second", "file:")
    scanner = _scanner(scanner_module)
    scanner.set_project_root("one", str(first))
    scanner.set_project_root("two", str(first))
    scanner.update_and_get("two")
    before = vars(scanner._get_state("two")).copy()
    scanner.set_project_root("one", str(second))
    assert vars(scanner._get_state("two")) == before
