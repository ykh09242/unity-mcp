"""Tests for tools/update_versions.py.

The release bump used to leave Server/uv.lock behind: pyproject.toml moved to 10.2.0
while the lock still recorded 10.1.0, which `uv sync --locked` rejects. These tests pin
the lock updater to the lock format uv actually writes for this repo, so a format change
surfaces here rather than in a release run.
"""

import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import update_versions  # noqa: E402

REAL_LOCK = _TOOLS_DIR.parent / "Server" / "uv.lock"
REAL_PYPROJECT = _TOOLS_DIR.parent / "Server" / "pyproject.toml"

SAMPLE_LOCK = (
    "version = 1\n"
    "revision = 3\n"
    'requires-python = ">=3.10"\n'
    "\n"
    "[[package]]\n"
    'name = "click"\n'
    'version = "8.3.1"\n'
    'source = { registry = "https://pypi.org/simple" }\n'
    "\n"
    "[[package]]\n"
    'name = "ykh09242-unity-mcp-server"\n'
    'version = "10.1.0"\n'
    'source = { editable = "." }\n'
    "dependencies = [\n"
    '    { name = "click" },\n'
    "]\n"
    "\n"
    "[[package]]\n"
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


def test_update_uv_lock_missing_entry_is_a_refusal(tmp_path, monkeypatch):
    path = tmp_path / "uv.lock"
    path.write_bytes(b'version = 1\n\n[[package]]\nname = "click"\nversion = "8.3.1"\n')
    monkeypatch.setattr(update_versions, "UV_LOCK", path)
    with pytest.raises(ValueError):
        update_versions.update_uv_lock("10.2.0")
    assert b'version = "8.3.1"' in path.read_bytes()


def test_update_uv_lock_missing_file_is_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_versions, "UV_LOCK", tmp_path / "absent.lock")
    with pytest.raises(FileNotFoundError):
        update_versions.update_uv_lock("10.2.0")


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


def test_readme_version_update_preserves_immutable_server_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given fork and upstream server references pinned to an immutable commit.
    commit = "910fce0e" + "a" * 32
    references = [
        f"git+https://github.com/{owner}/unity-mcp@{commit}#subdirectory=Server"
        for owner in ("ykh09242", "CoplayDev")
    ]
    repo = tmp_path / "checkout"
    path = repo / "Server" / "README.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(references)
        + "\ngit+https://github.com/CoplayDev/unity-mcp@v10.1.0#subdirectory=Server\n",
        encoding="utf-8",
    )
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
        "PACKAGE_JSON": (
            "MCPForUnity/package.json",
            json.dumps({"version": "3.2.1", "mcpServerSource": pin, "mcpSkillRevision": "c" * 40}),
        ),
        "MANIFEST_JSON": (
            "manifest.json",
            json.dumps({"version": "3.2.1", "server": {"source": pin}}),
        ),
        "PYPROJECT_TOML": ("Server/pyproject.toml", '[project]\nversion = "10.1.0"\n'),
        "UV_LOCK": ("Server/uv.lock", SAMPLE_LOCK),
        "SERVER_README": (
            "Server/README.md",
            "git+https://github.com/CoplayDev/unity-mcp@v10.1.0#subdirectory=Server\n",
        ),
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
    version_checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str | None,
) -> None:
    # Given: Unity and server have different versions and a commit-pinned source.
    before = {
        path.relative_to(version_checkout): path.read_bytes()
        for path in version_checkout.rglob("*")
        if path.is_file()
    }
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
    changed = {
        path
        for path, content in before.items()
        if (version_checkout / path).read_bytes() != content
    }
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


@pytest.mark.parametrize("version", ["2.3", "01.2.3", "2.3.4-beta.01", "2.3.4+", "2.3.4\n", ""])
def test_invalid_unity_version_leaves_all_files_untouched(version_checkout, monkeypatch, version):
    before = {path: path.read_bytes() for path in version_checkout.rglob("*") if path.is_file()}
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "all", "--version", version]
    )
    assert update_versions.main() != 0
    assert all(path.read_bytes() == content for path, content in before.items())


def test_prerelease_with_build_metadata_is_valid_for_unity(version_checkout, monkeypatch):
    version = "2.3.5-beta.1+build.007"
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "unity", "--version", version]
    )
    assert update_versions.main() == 0
    for relative in ("MCPForUnity/package.json", "manifest.json"):
        assert json.loads((version_checkout / relative).read_bytes())["version"] == version


def test_server_version_requires_an_explicit_value(
    version_checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the server version must not be inferred from the Unity package.
    before = {path: path.read_bytes() for path in version_checkout.rglob("*") if path.is_file()}
    monkeypatch.setattr(sys, "argv", ["update_versions.py", "--component", "server"])
    # When: the server update omits its version.
    with pytest.raises(SystemExit) as result:
        update_versions.main()
    # Then: argument validation fails without writing either component.
    assert result.value.code == 2
    assert all(path.read_bytes() == content for path, content in before.items())


def checkout_bytes(repo: Path) -> dict[Path, bytes]:
    return {path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()}


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("version", ["not-a-version", '1.2.3"', "1.2.3\n", "1.2.3\nkey = 1", " "])
def test_invalid_server_version_refuses_without_writes(
    version_checkout, monkeypatch, version, dry_run
):
    # Given an isolated checkout and an invalid Python distribution version.
    before = checkout_bytes(version_checkout)
    argv = ["update_versions.py", "--component", "server", "--version", version]
    monkeypatch.setattr(sys, "argv", argv + (["--dry-run"] if dry_run else []))
    # When the real CLI entrypoint validates the request.
    result = update_versions.main()
    # Then the refusal is nonzero and the complete file tree is unchanged.
    assert result != 0
    assert checkout_bytes(version_checkout) == before


@pytest.mark.parametrize("version", ["1.2.3rc1", "1.2.3a2", "1.2.3.dev4", "1.2.3-beta.1"])
def test_server_prerelease_updates_both_parseable_targets(version_checkout, monkeypatch, version):
    # Given an isolated checkout and a valid Python prerelease.
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "server", "--version", version]
    )
    # When both server metadata files are published.
    result = update_versions.main()
    # Then both parsed metadata values agree with the requested version.
    assert result == 0
    project = tomllib.loads(
        (version_checkout / "Server/pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((version_checkout / "Server/uv.lock").read_text(encoding="utf-8"))
    assert project["project"]["version"] == version
    assert [
        entry["version"]
        for entry in lock["package"]
        if entry["name"] == "ykh09242-unity-mcp-server"
    ] == [version]


@pytest.mark.parametrize("component", ["server", "all"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "damage",
    ["missing_entry", "missing_version", "malformed", "duplicate", "absent_lock", "absent_project"],
)
def test_preflight_refuses_incomplete_server_metadata(
    version_checkout, monkeypatch, component, dry_run, damage
):
    # Given a selected metadata target that cannot safely participate in a version bump.
    lock = version_checkout / "Server/uv.lock"
    if damage == "absent_project":
        (version_checkout / "Server/pyproject.toml").unlink()
    elif damage == "absent_lock":
        lock.unlink()
    else:
        damaged = {
            "missing_entry": SAMPLE_LOCK.replace("ykh09242-unity-mcp-server", "other-project"),
            "missing_version": SAMPLE_LOCK.replace('version = "10.1.0"\n', ""),
            "malformed": SAMPLE_LOCK + 'invalid = "unterminated\n',
            "duplicate": SAMPLE_LOCK
            + '\n[[package]]\nname = "ykh09242-unity-mcp-server"\nversion = "10.1.0"\n',
        }[damage]
        lock.write_bytes(damaged.encode("utf-8"))
    before = checkout_bytes(version_checkout)
    argv = ["update_versions.py", "--component", component, "--version", "4.5.6"]
    monkeypatch.setattr(sys, "argv", argv + (["--dry-run"] if dry_run else []))
    # When preflight evaluates every selected target, including dry runs.
    result = update_versions.main()
    # Then no file was added, deleted or changed before refusing.
    assert result != 0
    assert checkout_bytes(version_checkout) == before


@pytest.mark.parametrize("component", ["unity", "server", "all"])
def test_later_publication_failure_restores_all_files(version_checkout, monkeypatch, component):
    # Given a real filesystem whose second selected replacement fails once.
    before = checkout_bytes(version_checkout)
    replace = os.replace
    replacements = 0

    def fail_second(source, target):
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected second publication failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", component, "--version", "4.5.6"]
    )
    # When publication fails after the first target has changed.
    result = update_versions.main()
    # Then the operation fails and every original byte is restored with no staging files.
    assert result != 0
    assert replacements >= 2
    assert checkout_bytes(version_checkout) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_already_current_server_is_a_successful_noop(version_checkout, monkeypatch, dry_run):
    # Given both server metadata targets already carry the requested version.
    before = checkout_bytes(version_checkout)
    argv = ["update_versions.py", "--component", "server", "--version", "10.1.0"]
    monkeypatch.setattr(sys, "argv", argv + (["--dry-run"] if dry_run else []))
    # When the complete selected metadata is checked.
    result = update_versions.main()
    # Then a genuine no-op succeeds without creating any transaction files.
    assert result == 0
    assert checkout_bytes(version_checkout) == before


@pytest.mark.parametrize(
    "target", ["MCPForUnity/package.json", "manifest.json", "Server/README.md"]
)
def test_all_preflight_requires_every_selected_target(version_checkout, monkeypatch, target):
    # Given a missing Unity or documentation target in a legacy synchronized update.
    (version_checkout / target).unlink()
    before = checkout_bytes(version_checkout)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "all", "--version", "4.5.6"]
    )
    # When every selected target is preflighted.
    result = update_versions.main()
    # Then no earlier metadata can be published and the missing target stays missing.
    assert result != 0
    assert checkout_bytes(version_checkout) == before


def test_uncertain_replacement_outcome_is_rolled_back(version_checkout, monkeypatch):
    # Given a replacement that succeeds on disk before reporting an I/O error.
    before = checkout_bytes(version_checkout)
    replace = os.replace
    attempts = 0

    def replace_then_fail(source, target):
        nonlocal attempts
        attempts += 1
        replace(source, target)
        if attempts == 2:
            raise OSError("injected uncertain replacement outcome")

    monkeypatch.setattr(os, "replace", replace_then_fail)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "server", "--version", "4.5.6"]
    )
    # When publication reports failure after changing both targets.
    result = update_versions.main()
    # Then both targets are restored, including the uncertain second outcome.
    assert result != 0
    assert checkout_bytes(version_checkout) == before


def test_failed_rollback_keeps_original_bytes_for_recovery(version_checkout, monkeypatch, capsys):
    # Given a later publication failure followed by a persistent restoration failure.
    before = checkout_bytes(version_checkout)
    replace = os.replace
    attempts = 0

    def fail_publication_and_restore(source, target):
        nonlocal attempts
        attempts += 1
        if attempts >= 2:
            raise OSError("injected publication and rollback failures")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_publication_and_restore)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "server", "--version", "4.5.6"]
    )
    # When automatic restoration cannot complete.
    result = update_versions.main()
    # Then failure exposes a complete original-byte mapping for manual recovery.
    assert result != 0
    stages = list(version_checkout.glob(".version-update-*"))
    assert len(stages) == 1
    stage = stages[0]
    assert str(stage) in capsys.readouterr().err
    targets = json.loads((stage / "targets.json").read_text(encoding="utf-8"))
    assert targets == [
        str(version_checkout / path) for path in ("Server/pyproject.toml", "Server/uv.lock")
    ]
    for index, target in enumerate(targets):
        assert (stage / f"{index}.before").read_bytes() == before[
            Path(target).relative_to(version_checkout)
        ]
    actual = checkout_bytes(version_checkout)
    changed = {path for path, data in before.items() if actual[path] != data}
    assert changed == {Path("Server/pyproject.toml")}
    assert all(path in before or path.parts[0] == stage.name for path in actual)


@pytest.mark.parametrize("updater", ["update_pyproject_toml", "update_uv_lock"])
def test_direct_server_updater_refuses_unsafe_version(version_checkout, updater):
    # Given the same unsafe version submitted directly to either public helper.
    before = checkout_bytes(version_checkout)
    # When a caller bypasses CLI argument processing.
    with pytest.raises(ValueError):
        getattr(update_versions, updater)('1.2.3"\n')
    # Then helper refusal remains distinct from an already-current False result.
    assert checkout_bytes(version_checkout) == before


def test_concurrent_edit_to_already_current_target_refuses_publication(
    version_checkout, monkeypatch
):
    # Given an already-current project plus an outdated lock, edited during preparation.
    project = version_checkout / "Server/pyproject.toml"
    project.write_bytes(b'[project]\nversion = "4.5.6"\n')
    prepare_lock = update_versions._prepare_lock
    concurrent = b'[project]\nversion = "5.6.7"\n'

    def prepare_then_edit(version):
        change = prepare_lock(version)
        project.write_bytes(concurrent)
        return change

    before = checkout_bytes(version_checkout)
    before[Path("Server/pyproject.toml")] = concurrent
    monkeypatch.setattr(update_versions, "_prepare_lock", prepare_then_edit)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "server", "--version", "4.5.6"]
    )
    # When publication sees a concurrent edit to the selected no-op input.
    result = update_versions.main()
    # Then it refuses, preserves that edit and never updates the lock.
    assert result != 0
    assert checkout_bytes(version_checkout) == before


@pytest.mark.parametrize("entrypoint", ["helper", "server", "all"])
@pytest.mark.parametrize(
    "project_text",
    [
        '[project]\ndescription = """\nversion = "example"\n"""\nversion = "10.1.0"\n',
        '[project]\nversion = "1.2.3\\"old"\ndescription = "Keep this exactly"\n',
        "[project]\ndescription = '''\n[project]\nversion = \"fake\"\n'''\nversion = \"10.1.0\"\n",
    ],
)
def test_project_version_rewrite_preserves_other_toml_values(
    version_checkout, monkeypatch, entrypoint, project_text
):
    # Given valid TOML with version-like string content or an escaped old version.
    project = version_checkout / "Server/pyproject.toml"
    project.write_bytes(project_text.encode("utf-8"))
    expected = tomllib.loads(project_text)
    expected["project"]["version"] = "4.5.6"
    # When the public helper or CLI prepares and publishes a version bump.
    if entrypoint == "helper":
        assert update_versions.update_pyproject_toml("4.5.6") is True
    else:
        monkeypatch.setattr(
            sys, "argv", ["update_versions.py", "--component", entrypoint, "--version", "4.5.6"]
        )
        assert update_versions.main() == 0
    # Then parsed semantics change only the project version; all other text survives.
    assert tomllib.loads(project.read_text(encoding="utf-8")) == expected
    expected_text = project_text.replace('version = "10.1.0"', 'version = "4.5.6"').replace(
        'version = "1.2.3\\"old"', 'version = "4.5.6"'
    )
    assert project.read_bytes() == expected_text.encode("utf-8")
    if entrypoint != "helper":
        lock = tomllib.loads((version_checkout / "Server/uv.lock").read_text(encoding="utf-8"))
        assert [
            entry["version"]
            for entry in lock["package"]
            if entry["name"] == "ykh09242-unity-mcp-server"
        ] == ["4.5.6"]


def test_lock_rewrite_refuses_a_match_inside_multiline_text(version_checkout, monkeypatch):
    # Given a regex-shaped block inside string data and an unsupported real version spelling.
    lock = version_checkout / "Server/uv.lock"
    decoy = (
        "description = '''\n[[package]]\n"
        'name = "ykh09242-unity-mcp-server"\nversion = "10.1.0"\n\'\'\'\n'
    )
    lock.write_bytes(
        (decoy + SAMPLE_LOCK.replace('version = "10.1.0"', "version = '10.1.0'")).encode("utf-8")
    )
    before = checkout_bytes(version_checkout)
    monkeypatch.setattr(
        sys, "argv", ["update_versions.py", "--component", "server", "--version", "4.5.6"]
    )
    # When preparing the lock would change description data instead of its package entry.
    result = update_versions.main()
    # Then semantic validation refuses before either selected target can change.
    assert result != 0
    assert checkout_bytes(version_checkout) == before
