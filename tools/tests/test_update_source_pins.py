"""Local synthetic history and crash recovery; no providers or publication."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import update_source_pins as pins
from tools import update_versions
from tools.check_release_evidence import components


BASE = "https://github.com/ykh09242/unity-mcp"


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def put(repo, path, text):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def snapshot(repo):
    result = {}
    for p in repo.rglob("*"):
        relative = p.relative_to(repo)
        if relative.parts[0] in (".git", ".work"):
            continue
        if p.is_file():
            result[relative.as_posix()] = p.read_bytes()
    return result


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", BASE + ".git")
    put(
        repo,
        "Server/pyproject.toml",
        '[project]\nname="ykh09242-unity-mcp-server"\nversion="2.0.0"\n',
    )
    for folder in pins.SKILLS:
        put(repo, folder + "/SKILL.md", "Old reviewed skill\n")
        put(repo, folder + "/removed.md", "Old reference\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial components")
    old = git(repo, "rev-parse", "HEAD")
    url = BASE + "/archive/" + old + ".zip#subdirectory=Server"
    put(
        repo,
        "MCPForUnity/package.json",
        json.dumps(
            {
                "name": "com.ykh09242.unity-mcp",
                "version": "9.0.0",
                "mcpServerSource": url,
                "mcpSkillRevision": old,
            }
        )
        + "\n",
    )
    put(
        repo,
        "manifest.json",
        json.dumps({"version": "9.0.0", "server": {"mcp_config": {"args": ["--from", url]}}})
        + "\n",
    )
    for path, count in pins.DOCUMENTS.items():
        put(repo, path, (url + "\n") * count)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "metadata")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "fixture-base")
    put(
        repo,
        "Server/pyproject.toml",
        '[project]\nname="ykh09242-unity-mcp-server"\nversion="20.0.0"\n',
    )
    for folder in pins.SKILLS:
        put(repo, folder + "/SKILL.md", "New reviewed skill\n")
        (repo / folder / "removed.md").unlink()
        put(repo, folder + "/new/reference.md", "New reference\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed components")
    new = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "fixture-base")
    return repo, old, new, base


def test_update_is_complete_idempotent_and_release_checker_agrees(history):
    repo, old, new, _ = history
    before = snapshot(repo)
    plan = pins.prepare(repo, new, new)
    assert snapshot(repo) == before
    assert len(plan.changes) == 12
    assert pins.apply(plan) == 12
    assert pins.apply(pins.prepare(repo, new, new)) == 0
    assert not (repo / pins.JOURNAL).exists()
    package = json.loads((repo / "MCPForUnity/package.json").read_text())
    assert package["version"] == "9.0.0"
    assert package["mcpSkillRevision"] == new
    assert old not in (repo / "Server/README.md").read_text()
    for folder in pins.SKILLS:
        assert not (repo / folder / "removed.md").exists()
        assert (repo / folder / "new/reference.md").read_text() == "New reference\n"
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "generated pins")
    resolved = components(repo, git(repo, "rev-parse", "HEAD"))
    assert (resolved.server, resolved.skill, resolved.unity_version, resolved.server_version) == (
        new,
        new,
        "9.0.0",
        "20.0.0",
    )
    put(
        repo,
        pins.INPUTS,
        json.dumps(
            {
                "schema_version": 1,
                "repository": BASE,
                "server_commit": old,
                "skill_commit": new,
                "server_source": package["mcpServerSource"],
            }
        ),
    )
    git(repo, "add", pins.INPUTS)
    git(repo, "commit", "-qm", "broken generated input")
    with pytest.raises(ValueError, match="source input"):
        components(repo, git(repo, "rev-parse", "HEAD"))


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "remote",
        "package",
        "examples",
        "skill-edit",
        "skill-extra",
        "skill-delete",
        "input-edit",
    ],
)
def test_invalid_inputs_refuse_before_replacement(history, failure):
    repo, _, new, _ = history
    if failure == "remote":
        git(repo, "remote", "set-url", "origin", "https://github.com/unrelated/other.git")
    elif failure == "package":
        p = repo / "MCPForUnity/package.json"
        p.write_text(p.read_text().replace("com.ykh09242.unity-mcp", "com.other.package"))
    elif failure == "examples":
        put(repo, "Server/README.md", "unexpected examples\n")
    elif failure == "skill-edit":
        put(repo, pins.SKILLS[0] + "/SKILL.md", "Unreviewed local edit\n")
    elif failure == "skill-extra":
        put(repo, pins.SKILLS[0] + "/unknown.md", "Keep me\n")
    elif failure == "skill-delete":
        (repo / pins.SKILLS[0] / "SKILL.md").unlink()
    if failure == "input-edit":
        put(repo, pins.INPUTS, '{"personal": true}\n')
    before = snapshot(repo)
    with pytest.raises(ValueError):
        pins.prepare(repo, "f" * 40 if failure == "missing" else new, new)
    assert snapshot(repo) == before
    assert not (repo / pins.JOURNAL).exists()


def test_changed_inputs_and_interrupted_apply_preserve_original_bytes(history, monkeypatch):
    repo, _, new, _ = history
    before = snapshot(repo)
    plan = pins.prepare(repo, new, new)
    real = pins.os.replace
    calls = 0

    def interrupt(source, target):
        nonlocal calls
        real(source, target)
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(pins.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pins.apply(plan)
    assert snapshot(repo) == before
    assert not (repo / pins.JOURNAL).exists()
    put(repo, "Server/README.md", "Concurrent edit\n")
    before = snapshot(repo)
    with pytest.raises(ValueError, match="changed"):
        pins.apply(plan)
    assert snapshot(repo) == before


CRASH = """import os, sys
from pathlib import Path
from tools import update_source_pins as pins
real = os.replace
count = 0
def crash(source, target):
    global count
    real(source, target)
    count += 1
    if count == int(sys.argv[3]): os._exit(73)
os.replace = crash
pins.apply(pins.prepare(Path(sys.argv[1]), sys.argv[2], sys.argv[2]))
"""


@pytest.mark.parametrize("edited_after_crash", [False, True])
@pytest.mark.parametrize("stop_after", [2, 4])
def test_real_process_termination_has_recoverable_journal(history, edited_after_crash, stop_after):
    repo, _, new, _ = history
    before = snapshot(repo)
    result = subprocess.run(
        [sys.executable, "-c", CRASH, str(repo), new, str(stop_after)],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=30,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert (repo / pins.JOURNAL / "journal.json").is_file()
    assert snapshot(repo) != before
    with pytest.raises(ValueError, match="Unfinished"):
        pins.prepare(repo, new, new)
    if edited_after_crash:
        row = json.loads((repo / pins.JOURNAL / "journal.json").read_text())["files"][0]
        put(repo, row["path"], "Subsequent user edit\n")
        changed = snapshot(repo)
        with pytest.raises(ValueError, match="Concurrent"):
            pins.recover(repo)
        assert snapshot(repo) == changed
    else:
        assert pins.recover(repo) == 12
        assert snapshot(repo) == before
        assert not (repo / pins.JOURNAL).exists()


def test_dry_run_entrypoint_and_unsafe_paths(history, monkeypatch, capsys):
    repo, _, new, _ = history
    before = snapshot(repo)
    monkeypatch.setattr(update_versions, "REPO_ROOT", repo)
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_versions.py", "--server-commit", new, "--skill-commit", new, "--dry-run"],
    )
    assert update_versions.main() == 0
    assert json.loads(capsys.readouterr().out)["server_commit"] == new
    assert snapshot(repo) == before
    for relative in ("../outside", "C:/escape", "folder/CON.txt", "folder/a:b", "a//b"):
        with pytest.raises(ValueError):
            pins.safe_path(repo, relative)


def test_symlinked_mirror_refuses_without_touching_destination(history, tmp_path):
    repo, _, new, _ = history
    target = tmp_path / "outside"
    target.mkdir()
    path = repo / pins.SKILLS[0] / "linked"
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation unavailable on this Windows test account")
    with pytest.raises(ValueError, match="Linked"):
        pins.prepare(repo, new, new)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("defect", ["server_identity", "skill_mirror"])
def test_invalid_committed_components_fail_before_publication(history, defect):
    repo, _, new, _ = history
    git(repo, "checkout", "-q", "--detach", new)
    if defect == "server_identity":
        put(repo, "Server/pyproject.toml", '[project]\nname="unrelated-server"\nversion="1.0.0"\n')
    else:
        put(repo, pins.SKILLS[0] + "/SKILL.md", "Mismatched committed mirror\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "invalid reviewed input")
    invalid = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "fixture-base")
    before = snapshot(repo)
    with pytest.raises(ValueError):
        pins.prepare(repo, invalid, invalid)
    assert snapshot(repo) == before
    assert not (repo / pins.JOURNAL).exists()


CLEANUP_CRASH = """import os, sys
from pathlib import Path
from tools import update_source_pins as pins
real = Path.unlink
def crash(path, *args, **kwargs):
    real(path, *args, **kwargs)
    if path.parent.name.startswith('source-pin-transaction'):
        if (sys.argv[4] == 'backup' and path.suffix == '.before') or path.name == sys.argv[4]:
            os._exit(74)
Path.unlink = crash
repo = Path(sys.argv[1])
if sys.argv[3] == 'apply':
    pins.apply(pins.prepare(repo, sys.argv[2], sys.argv[2]))
else:
    pins.recover(repo)
"""


@pytest.mark.parametrize("operation", ["apply", "rollback"])
@pytest.mark.parametrize("stop_after", ["backup", "journal.json"])
def test_cleanup_interruption_preserves_settled_outcome_and_is_restartable(
    history, operation, stop_after
):
    repo, _, new, _ = history
    before = snapshot(repo)
    plan = pins.prepare(repo, new, new)
    expected = dict(before)
    if operation == "apply":
        for change in plan.changes:
            if change.after is None:
                expected.pop(change.path)
            else:
                expected[change.path] = change.after
    else:
        result = subprocess.run(
            [sys.executable, "-c", CRASH, str(repo), new, "2"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 73, result.stderr.decode()
    result = subprocess.run(
        [sys.executable, "-c", CLEANUP_CRASH, str(repo), new, operation, stop_after],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 74, result.stderr.decode()
    assert snapshot(repo) == expected
    with pytest.raises(ValueError, match="Unfinished"):
        pins.prepare(repo, new, new)
    # Finalized cleanup must preserve even a subsequent user edit.
    put(repo, "Server/README.md", "Subsequent user edit\n")
    expected["Server/README.md"] = b"Subsequent user edit\n"
    pins.recover(repo)
    assert snapshot(repo) == expected
    assert not (repo / pins.JOURNAL).exists()
    assert not (repo / (pins.JOURNAL + "-finalized")).exists()
