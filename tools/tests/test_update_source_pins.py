"""Local synthetic history and crash recovery; no providers or publication."""

import base64
import hashlib
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

    def fail(error):
        raise error

    for directory, folders, files in os.walk(repo, onerror=fail):
        root = Path(directory)
        if root == repo:
            folders[:] = [name for name in folders if name not in (".git", ".work")]
            files = [name for name in files if name not in (".git", ".work")]
        for name in files:
            path = root / name
            if path.is_file():
                result[path.relative_to(repo).as_posix()] = path.read_bytes()
    return result


def test_snapshot_excludes_unstable_metadata_without_reading_it(tmp_path, monkeypatch):
    put(tmp_path, "src/keep.txt", "kept")
    put(tmp_path, ".git/objects/temporary", "moving Git data")
    put(tmp_path, ".work/temporary", "moving task data")
    scandir = os.scandir
    excluded_reads = []

    def unstable_metadata(path):
        relative = Path(path).relative_to(tmp_path)
        if relative.parts and relative.parts[0] in (".git", ".work"):
            excluded_reads.append(relative)
            raise FileNotFoundError("Transient metadata disappeared")
        return scandir(path)

    monkeypatch.setattr(os, "scandir", unstable_metadata)
    assert snapshot(tmp_path) == {"src/keep.txt": b"kept"}
    assert excluded_reads == []


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


@pytest.fixture
def bundled_history(history):
    repo, _, new, _ = history
    git(repo, "checkout", "-q", "--detach", new)
    for folder in pins.SKILLS:
        put(
            repo,
            folder + "/SKILL.md",
            "---\nname: unity-mcp-orchestrator\ndescription: Reviewed fixture\n---\nSkill guidance\n",
        )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed skill with frontmatter")
    skill = git(repo, "rev-parse", "HEAD")
    files = pins.skill_files(repo, skill, pins.SKILLS[0])
    payload = {
        "format": 1,
        "repository": BASE,
        "revision": skill,
        "frontmatter": {"name": "unity-mcp-orchestrator", "description": "Reviewed fixture"},
        "files": [
            {
                "path": path,
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
            }
            for path, data in sorted(files.items())
        ],
    }
    record = {"payload": payload}
    bundle_checksum(record)
    put(repo, "Server/src/services/skills_bundle.json", json.dumps(record) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "server with immutable skill bundle")
    server = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "fixture-base")
    return repo, server, skill


def bundle_checksum(record):
    canonical = json.dumps(
        record["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    record["sha256"] = hashlib.sha256(canonical).hexdigest()


@pytest.fixture
def crlf_bundled_history(bundled_history):
    repo, server, _ = bundled_history
    git(repo, "checkout", "-q", "--detach", server)
    put(repo, ".gitattributes", "*.md -text\n")
    for folder in pins.SKILLS:
        for path in (repo / folder).rglob("*.md"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed CRLF skill blobs")
    skill = git(repo, "rev-parse", "HEAD")
    path = "Server/src/services/skills_bundle.json"
    record = json.loads((repo / path).read_bytes())
    record["payload"]["revision"] = skill
    for entry in record["payload"]["files"]:
        data = pins.committed(repo, skill, pins.SKILLS[0] + "/" + entry["path"])
        assert b"\r\n" in data
        entry.update(
            data=base64.b64encode(data).decode("ascii"),
            size=len(data),
            digest="sha256:" + hashlib.sha256(data).hexdigest(),
        )
    bundle_checksum(record)
    put(repo, path, json.dumps(record) + "\n")
    git(repo, "add", path)
    git(repo, "commit", "-qm", "server preserves exact CRLF skill bytes")
    server = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "fixture-base")
    return repo, server, skill


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("rewrite_lf", [False, True])
def test_bundle_comparison_preserves_raw_committed_line_endings(
    crlf_bundled_history, monkeypatch, capsys, dry_run, rewrite_lf
):
    # Given: a bundle preserving verified CRLF Git blobs, or a non-exact LF rewrite.
    repo, server, skill = crlf_bundled_history
    if rewrite_lf:
        git(repo, "checkout", "-q", "--detach", server)
        path = "Server/src/services/skills_bundle.json"
        record = json.loads((repo / path).read_bytes())
        for entry in record["payload"]["files"]:
            data = base64.b64decode(entry["data"]).replace(b"\r\n", b"\n")
            entry.update(
                data=base64.b64encode(data).decode("ascii"),
                size=len(data),
                digest="sha256:" + hashlib.sha256(data).hexdigest(),
            )
        bundle_checksum(record)
        put(repo, path, json.dumps(record) + "\n")
        git(repo, "add", path)
        git(repo, "commit", "-qm", "server with non-exact LF rewritten bytes")
        server = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "fixture-base")
    before = snapshot(repo)
    arguments = ["update_versions.py", "--server-commit", server, "--skill-commit", skill]
    if dry_run:
        arguments.append("--dry-run")
    monkeypatch.setattr(update_versions, "REPO_ROOT", repo)
    monkeypatch.setattr(sys, "argv", arguments)
    # When: either CLI mode compares the bundle with its selected immutable skill.
    result = update_versions.main()
    # Then: only the exact raw blob representation is compatible, without preflight writes.
    assert result == int(rewrite_lf)
    if rewrite_lf:
        assert "skill bundle" in capsys.readouterr().err.lower()
    if rewrite_lf or dry_run:
        assert snapshot(repo) == before
        assert not (repo / ".work").exists()
    else:
        assert (
            json.loads((repo / "MCPForUnity/package.json").read_bytes())["mcpSkillRevision"]
            == skill
        )


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        "revision",
        "checksum",
        "content",
        "missing",
        "extra",
        "duplicate",
        "digest",
        "size",
        "envelope",
        "format",
    ],
)
def test_incompatible_server_bundle_refuses_before_any_write(
    bundled_history, monkeypatch, capsys, defect, dry_run
):
    # Given: selected server/skill commits whose mirrors can agree while their bundle does not.
    repo, server, skill = bundled_history
    if defect == "revision":
        skill = server  # Identical skill files do not make the provenance revision interchangeable.
    else:
        git(repo, "checkout", "-q", "--detach", server)
        path = "Server/src/services/skills_bundle.json"
        record = json.loads((repo / path).read_bytes())
        payload = record["payload"]
        if defect == "content":
            data = b"Different guidance\n"
            payload["files"][0].update(
                data=base64.b64encode(data).decode("ascii"),
                size=len(data),
                digest="sha256:" + hashlib.sha256(data).hexdigest(),
            )
        elif defect == "missing":
            payload["files"].pop()
        elif defect == "extra":
            payload["files"].append({**payload["files"][0], "path": "extra.md"})
        elif defect == "duplicate":
            payload["files"].append(payload["files"][0])
        elif defect == "digest":
            payload["files"][0]["digest"] = "sha256:" + "f" * 64
        elif defect == "size":
            payload["files"][0]["size"] += 1
        elif defect == "envelope":
            record["unexpected"] = True
        elif defect == "format":
            payload["format"] = 2
        bundle_checksum(record)
        if defect == "checksum":
            record["sha256"] = "f" * 64
        put(repo, path, json.dumps(record) + "\n")
        git(repo, "add", path)
        git(repo, "commit", "-qm", "incompatible bundled skill input")
        server = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "fixture-base")
    before = snapshot(repo)
    monkeypatch.setattr(update_versions, "REPO_ROOT", repo)
    arguments = ["update_versions.py", "--server-commit", server, "--skill-commit", skill]
    if dry_run:
        arguments.append("--dry-run")
    monkeypatch.setattr(sys, "argv", arguments)
    # When: either CLI mode selects the incompatible pair.
    result = update_versions.main()
    # Then: refusal precedes public files, a journal, and even private staging creation.
    assert result == 1
    assert "skill bundle" in capsys.readouterr().err.lower()
    assert snapshot(repo) == before
    assert not (repo / ".work").exists()


def test_compatible_bundled_selection_is_complete_and_idempotent(bundled_history):
    # Given: a server bundle derived from the exact selected immutable skill revision.
    repo, server, skill = bundled_history
    before = snapshot(repo)
    # When: preparation, application and an identical repeat use that compatible pair.
    plan = pins.prepare(repo, server, skill)
    assert snapshot(repo) == before
    assert pins.apply(plan) > 0
    # Then: selected provenance is truthful and an identical repeat makes no changes.
    assert pins.apply(pins.prepare(repo, server, skill)) == 0
    package = json.loads((repo / "MCPForUnity/package.json").read_bytes())
    assert package["mcpSkillRevision"] == skill
    assert server in package["mcpServerSource"]


def test_legacy_server_without_bundle_keeps_independent_skill_selection(history):
    # Given: independently reviewed server and skill commits preceding bundled skills.
    repo, server, skill, _ = history
    # When: the legacy server is selected with the newer skill revision.
    pins.apply(pins.prepare(repo, server, skill))
    # Then: that previously supported pair remains valid and idempotent.
    assert pins.apply(pins.prepare(repo, server, skill)) == 0
    package = json.loads((repo / "MCPForUnity/package.json").read_bytes())
    assert package["mcpSkillRevision"] == skill
    assert server in package["mcpServerSource"]


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
