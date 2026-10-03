import os
import json
import time
from pathlib import Path


def test_external_changes_scanner_marks_dirty_and_clears(tmp_path, monkeypatch):
    # Ensure the scanner is active for this unit-style test (not gated by PYTEST_CURRENT_TEST).
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.state.external_changes_scanner import ExternalChangesScanner

    # Create a minimal Unity-like layout
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "Packages").mkdir(parents=True)

    inst = "Test@deadbeef"
    s = ExternalChangesScanner(scan_interval_ms=0, max_entries=10000)
    s.set_project_root(inst, str(root))

    # Create a file before baseline so the initial scan establishes a stable reference point.
    p = root / "Assets" / "x.txt"
    p.write_text("hi")

    # Baseline scan: should not be dirty.
    first = s.update_and_get(inst)
    assert first["external_changes_dirty"] is False

    # Touch the file and scan again: should become dirty.
    now = time.time()
    os.utime(p, (now + 10.0, now + 10.0))

    second = s.update_and_get(inst)
    assert second["external_changes_dirty"] is True
    assert isinstance(second["external_changes_last_seen_unix_ms"], int)
    assert isinstance(second["dirty_since_unix_ms"], int)

    # Clear and confirm dirty flag resets.
    s.clear_dirty(inst)
    third = s.update_and_get(inst)
    assert third["external_changes_dirty"] is False
    assert isinstance(third["last_cleared_unix_ms"], int)


def test_external_changes_scanner_includes_file_dependency_roots(tmp_path, monkeypatch):
    # Ensure the scanner is active for this unit-style test (not gated by PYTEST_CURRENT_TEST).
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.state.external_changes_scanner import ExternalChangesScanner

    # Unity project root
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "Packages").mkdir(parents=True)

    # External local package root (outside project root)
    pkg = tmp_path / "ExternalPkg"
    (pkg / "Editor").mkdir(parents=True)
    target = pkg / "Editor" / "Some.cs"
    target.write_text("// v1")

    # manifest.json referencing file: dependency
    manifest = root / "Packages" / "manifest.json"
    manifest.write_text(
        '{\n  "dependencies": {\n    "com.example.pkg": "file:../../ExternalPkg"\n  }\n}\n',
        encoding="utf-8",
    )

    inst = "Test@deadbeef"
    s = ExternalChangesScanner(scan_interval_ms=0, max_entries=10000)
    s.set_project_root(inst, str(root))

    # Baseline scan captures current mtimes across project + external pkg
    baseline = s.update_and_get(inst)
    assert baseline["external_changes_dirty"] is False

    # Touch external package file and scan again -> should mark dirty
    now = time.time()
    os.utime(target, (now + 10.0, now + 10.0))

    changed = s.update_and_get(inst)
    assert changed["external_changes_dirty"] is True


def _project_with_local_package(tmp_path):
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    manifest = root / "Packages" / "manifest.json"
    manifest.write_text(json.dumps({"dependencies": {"com.example.local": "file:../../LocalPkg"}}), encoding="utf-8")
    os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
    return root, manifest, tmp_path / "LocalPkg"


def _write_package_file(package, mtime_ns):
    package.mkdir(exist_ok=True)
    target = package / "package.json"
    target.write_text('{"name":"com.example.local"}', encoding="utf-8")
    os.utime(target, ns=(mtime_ns, mtime_ns))
    return target


def test_cached_manifest_discovers_package_created_later(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from services.state.external_changes_scanner import ExternalChangesScanner

    root, manifest, package = _project_with_local_package(tmp_path)
    reads = []
    original_read = Path.open

    def counted_read(path, *args, **kwargs):
        if path == manifest:
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_read)
    scanner = ExternalChangesScanner(scan_interval_ms=0)
    scanner.set_project_root("Late@one", str(root))
    assert not scanner.update_and_get("Late@one")["external_changes_dirty"]

    _write_package_file(package, 2_000_000_000)
    observed = scanner.update_and_get("Late@one")
    assert observed["external_changes_dirty"] is True
    assert observed["dirty_since_unix_ms"] is not None
    assert len(reads) == 1, "An unchanged manifest should not be reparsed to discover its package."


def test_cached_package_can_disappear_and_reappear(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from services.state.external_changes_scanner import ExternalChangesScanner

    root, _, package = _project_with_local_package(tmp_path)
    target = _write_package_file(package, 2_000_000_000)
    scanner = ExternalChangesScanner(scan_interval_ms=0)
    scanner.set_project_root("Move@one", str(root))
    assert not scanner.update_and_get("Move@one")["external_changes_dirty"]

    detached = tmp_path / "Detached"
    assert package.resolve().is_relative_to(tmp_path.resolve())
    assert detached.resolve().is_relative_to(tmp_path.resolve())
    package.rename(detached)
    assert not scanner.update_and_get("Move@one")["external_changes_dirty"]
    detached.rename(package)
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    assert scanner.update_and_get("Move@one")["external_changes_dirty"] is True
    scanner.clear_dirty("Move@one")
    assert not scanner.update_and_get("Move@one")["external_changes_dirty"]
    os.utime(target, ns=(4_000_000_000, 4_000_000_000))
    assert scanner.update_and_get("Move@one")["external_changes_dirty"] is True


def test_late_package_detection_preserves_throttle_and_instance_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    import services.state.external_changes_scanner as module

    now = [1000]
    monkeypatch.setattr(module, "_now_unix_ms", lambda: now[0])
    first_root, _, first_package = _project_with_local_package(tmp_path / "First")
    second_root, _, _ = _project_with_local_package(tmp_path / "Second")
    scanner = module.ExternalChangesScanner(scan_interval_ms=1500)
    scanner.set_project_root("First@one", str(first_root))
    scanner.set_project_root("Second@two", str(second_root))
    assert not scanner.update_and_get("First@one")["external_changes_dirty"]
    assert not scanner.update_and_get("Second@two")["external_changes_dirty"]
    _write_package_file(first_package, 2_000_000_000)

    now[0] = 2499
    assert not scanner.update_and_get("First@one")["external_changes_dirty"]
    now[0] = 2500
    observed = scanner.update_and_get("First@one")
    assert observed["external_changes_dirty"] is True
    assert observed["dirty_since_unix_ms"] == 2500
    assert not scanner.update_and_get("Second@two")["external_changes_dirty"]
    scanner.clear_dirty("First@one")
    assert not scanner.update_and_get("First@one")["external_changes_dirty"]
    assert scanner.update_and_get("Second@two")["last_cleared_unix_ms"] is None


