"""Tests for tools/update_versions.py.

The release bump used to leave Server/uv.lock behind: pyproject.toml moved to 10.2.0
while the lock still recorded 10.1.0, which `uv sync --locked` rejects. These tests pin
the lock updater to the lock format uv actually writes for this repo, so a format change
surfaces here rather than in a release run.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import update_versions  # noqa: E402

REAL_LOCK = _TOOLS_DIR.parent / "Server" / "uv.lock"
REAL_PYPROJECT = _TOOLS_DIR.parent / "Server" / "pyproject.toml"

SAMPLE_LOCK = (
    'version = 1\n'
    'revision = 3\n'
    'requires-python = ">=3.10"\n'
    '\n'
    '[[package]]\n'
    'name = "click"\n'
    'version = "8.3.1"\n'
    'source = { registry = "https://pypi.org/simple" }\n'
    '\n'
    '[[package]]\n'
    'name = "ykh09242-unity-mcp-server"\n'
    'version = "10.1.0"\n'
    'source = { editable = "." }\n'
    'dependencies = [\n'
    '    { name = "click" },\n'
    ']\n'
    '\n'
    '[[package]]\n'
    'name = "mcp"\n'
    'version = "1.26.0"\n'
    'source = { registry = "https://pypi.org/simple" }\n'
)


@pytest.fixture
def lock_file(tmp_path, monkeypatch):
    path = tmp_path / "uv.lock"
    path.write_bytes(SAMPLE_LOCK.encode("utf-8"))
    monkeypatch.setattr(update_versions, "UV_LOCK", path)
    return path


def test_update_uv_lock_rewrites_only_the_project_entry(lock_file):
    assert update_versions.update_uv_lock("10.2.0") is True
    updated = lock_file.read_bytes().decode("utf-8")
    assert 'name = "ykh09242-unity-mcp-server"\nversion = "10.2.0"' in updated
    assert 'name = "click"\nversion = "8.3.1"' in updated
    assert 'name = "mcp"\nversion = "1.26.0"' in updated
    assert updated.count('version = "10.2.0"') == 1


def test_update_uv_lock_is_a_noop_when_already_current(lock_file):
    update_versions.update_uv_lock("10.2.0")
    before = lock_file.read_bytes()
    assert update_versions.update_uv_lock("10.2.0") is False
    assert lock_file.read_bytes() == before


def test_update_uv_lock_dry_run_does_not_write(lock_file):
    assert update_versions.update_uv_lock("10.2.0", dry_run=True) is True
    assert 'version = "10.1.0"' in lock_file.read_bytes().decode("utf-8")


def test_update_uv_lock_preserves_crlf_line_endings(tmp_path, monkeypatch):
    """A core.autocrlf checkout must not be rewritten to LF (or vice versa) by a version bump."""
    path = tmp_path / "uv.lock"
    path.write_bytes(SAMPLE_LOCK.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(update_versions, "UV_LOCK", path)
    assert update_versions.update_uv_lock("10.2.0") is True
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert b'name = "ykh09242-unity-mcp-server"\r\nversion = "10.2.0"' in raw


def test_update_uv_lock_missing_entry_is_reported_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "uv.lock"
    path.write_bytes(b'version = 1\n\n[[package]]\nname = "click"\nversion = "8.3.1"\n')
    monkeypatch.setattr(update_versions, "UV_LOCK", path)
    assert update_versions.update_uv_lock("10.2.0") is False
    assert b'version = "8.3.1"' in path.read_bytes()


def test_update_uv_lock_missing_file_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(update_versions, "UV_LOCK", tmp_path / "absent.lock")
    assert update_versions.update_uv_lock("10.2.0") is False


def test_update_uv_lock_matches_the_checked_in_lock_format(tmp_path, monkeypatch):
    """The regex has to keep matching whatever uv writes for this repo."""
    path = tmp_path / "uv.lock"
    shutil.copy(REAL_LOCK, path)
    monkeypatch.setattr(update_versions, "UV_LOCK", path)
    assert update_versions.update_uv_lock("0.0.0.dev0") is True
    assert re.search(
        r'^\[\[package\]\]\s*\nname = "ykh09242-unity-mcp-server"\s*\nversion = "0\.0\.0\.dev0"',
        path.read_bytes().decode("utf-8"),
        re.MULTILINE,
    )


def test_checked_in_lock_agrees_with_pyproject_version():
    """Guards the drift that `uv sync --locked` now rejects in CI."""
    pyproject_version = re.search(
        r'^version = "([^"]+)"', REAL_PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE
    ).group(1)
    lock_version = update_versions._UV_LOCK_SELF_VERSION.search(
        REAL_LOCK.read_bytes().decode("utf-8")
    ).group(2)
    assert lock_version == pyproject_version


def test_readme_version_update_preserves_immutable_server_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given fork and upstream server references pinned to an immutable commit.
    commit = "910fce0e" + "a" * 32
    references = [
        f"git+https://github.com/{owner}/unity-mcp@{commit}#subdirectory=Server"
        for owner in ("ykh09242", "CoplayDev")
    ]
    repo = tmp_path / "checkout"
    path = repo / "Server" / "README.md"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(references) + "\ngit+https://github.com/CoplayDev/unity-mcp@v10.1.0#subdirectory=Server\n", encoding="utf-8")
    monkeypatch.setattr(update_versions, "REPO_ROOT", repo)
    monkeypatch.setattr(update_versions, "SERVER_README", path)

    # When existing tag examples receive a version bump.
    changed = update_versions.update_server_readme("10.4.0")

    # Then commit references keep the immutable source selected by the maintainer.
    assert changed
    updated = path.read_text(encoding="utf-8")
    assert all(reference in updated for reference in references)
    assert "@v10.4.0#subdirectory=Server" in updated


@pytest.fixture
def version_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "checkout"
    pin = "git+https://github.com/ykh09242/unity-mcp.git@" + "a" * 40 + "#subdirectory=Server"
    contents = {
        "PACKAGE_JSON": ("MCPForUnity/package.json", json.dumps({"version": "3.2.1", "mcpServerSource": pin})),
        "MANIFEST_JSON": ("manifest.json", json.dumps({"version": "3.2.1", "server": {"source": pin}})),
        "PYPROJECT_TOML": ("Server/pyproject.toml", '[project]\nversion = "10.1.0"\n'),
        "UV_LOCK": ("Server/uv.lock", SAMPLE_LOCK),
        "SERVER_README": ("Server/README.md", "git+https://github.com/CoplayDev/unity-mcp@v10.1.0#subdirectory=Server\n"),
    }
    for constant, (relative, content) in contents.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(update_versions, constant, path)
    monkeypatch.setattr(update_versions, "REPO_ROOT", repo)
    return repo


@pytest.mark.parametrize("component", [None, "unity", "server", "all"])
def test_component_version_updates_do_not_synchronize_unselected_packages(
    version_checkout: Path, monkeypatch: pytest.MonkeyPatch, component: str | None,
) -> None:
    # Given: Unity and server have different versions and a commit-pinned source.
    before = {path.relative_to(version_checkout): path.read_bytes() for path in version_checkout.rglob("*") if path.is_file()}
    argv = ["update_versions.py", "--version", "4.5.6"]
    if component is not None:
        argv.extend(["--component", component])
    monkeypatch.setattr(sys, "argv", argv)
    # When: the version CLI updates the selected component (Unity by default).
    result = update_versions.main()
    # Then: only the selected metadata changes; pins and other component bytes survive.
    assert result == 0
    selected = component or "unity"
    expected = {
        "unity": {Path("MCPForUnity/package.json"), Path("manifest.json")},
        "server": {Path("Server/pyproject.toml"), Path("Server/uv.lock")},
        "all": set(before),
    }[selected]
    changed = {path for path, content in before.items() if (version_checkout / path).read_bytes() != content}
    assert changed == expected
    for relative in (Path("MCPForUnity/package.json"), Path("manifest.json")):
        original = json.loads(before[relative])
        updated = json.loads((version_checkout / relative).read_text(encoding="utf-8"))
        if relative in expected:
            original["version"] = "4.5.6"
        assert updated == original
    if Path("Server/uv.lock") in expected:
        lock = (version_checkout / "Server/uv.lock").read_text(encoding="utf-8")
        assert update_versions._UV_LOCK_SELF_VERSION.search(lock).group(2) == "4.5.6"
        assert 'name = "click"\nversion = "8.3.1"' in lock


def test_server_version_requires_an_explicit_value(version_checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the server version must not be inferred from the Unity package.
    before = {path: path.read_bytes() for path in version_checkout.rglob("*") if path.is_file()}
    monkeypatch.setattr(sys, "argv", ["update_versions.py", "--component", "server"])
    # When: the server update omits its version.
    with pytest.raises(SystemExit) as result:
        update_versions.main()
    # Then: argument validation fails without writing either component.
    assert result.value.code == 2
    assert all(path.read_bytes() == content for path, content in before.items())
