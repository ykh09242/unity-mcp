"""The packaged skill is derived from the existing verified pin, never a branch."""

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIRRORS = (".claude/skills/unity-mcp-skill", "unity-mcp-skill")


def generator(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "generate_skill_bundle", ROOT / "tools/generate_skill_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *arguments):
    return subprocess.run(
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
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


@pytest.fixture
def pinned_source(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    skill = (
        "---\nname: unity-mcp-orchestrator\ndescription: Fixture guidance\n"
        "metadata:\n  version: '1.10'\n  enabled: 'yes'\n"
        "custom:\n  flag: true\n  labels: [one, two]\n---\n\n"
        "[Reference](references/guide.md)\n"
    )
    for mirror in MIRRORS:
        folder = root / mirror
        (folder / "references").mkdir(parents=True)
        (folder / "SKILL.md").write_bytes(skill.encode())
        (folder / "references/guide.md").write_bytes("UTF-8 fixture: 한글 é\n".encode())
    git(root, "add", ".")
    git(root, "commit", "-m", "pinned fixture content")
    (root / "MCPForUnity").mkdir()
    (root / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": git(root, "rev-parse", "HEAD")}), encoding="utf-8"
    )
    return root


def test_verified_generation_preserves_exact_committed_bytes_and_full_frontmatter(
    pinned_source, monkeypatch
):
    # Given: a locally verified preceding content commit, without a provider/network lookup.
    module = generator(monkeypatch)
    before = (pinned_source / "MCPForUnity/package.json").read_bytes()
    # When: the pinned snapshot is rendered twice.
    encoded = module.build_bundle(pinned_source, verify_git=True)
    assert module.build_bundle(pinned_source, verify_git=True) == encoded
    record = json.loads(encoded)
    payload = record["payload"]
    # Then: provenance, all fields, and raw Unicode bytes survive without pin changes.
    assert payload["revision"] == json.loads(before)["mcpSkillRevision"]
    assert payload["frontmatter"]["metadata"] == {"version": "1.10", "enabled": "yes"}
    assert payload["frontmatter"]["custom"] == {"flag": True, "labels": ["one", "two"]}
    assert payload["repository"] == "https://github.com/ykh09242/unity-mcp"
    for entry in payload["files"]:
        raw = base64.b64decode(entry["data"], validate=True)
        assert raw == (pinned_source / MIRRORS[0] / entry["path"]).read_bytes()
        assert len(raw) == entry["size"]
        assert entry["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert (pinned_source / "MCPForUnity/package.json").read_bytes() == before


def test_working_copy_newlines_do_not_change_the_verified_bundle(pinned_source, monkeypatch):
    # Given: a verified bundle based on committed LF bytes.
    module = generator(monkeypatch)
    before = module.build_bundle(pinned_source, verify_git=True)
    for mirror in MIRRORS:
        for path in (pinned_source / mirror).rglob("*.md"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    # When: both the verified writer and ordinary artifact checker inspect CRLF checkouts.
    verified = module.build_bundle(pinned_source, verify_git=True)
    checked = module.build_bundle(pinned_source, verify_git=False)
    # Then: neither path publishes working-copy newline-dependent manifest bytes.
    assert before == verified == checked


@pytest.mark.parametrize("change", ["mirror", "pin", "extra", "frontmatter", "non_markdown"])
def test_generation_refuses_stale_or_unsupported_sources(pinned_source, monkeypatch, change):
    # Given: a mismatch against one of the fixed canonical source/pin contracts.
    if change == "mirror":
        (pinned_source / MIRRORS[1] / "references/guide.md").write_text("changed\n")
    elif change == "pin":
        (pinned_source / "MCPForUnity/package.json").write_text(
            json.dumps({"mcpSkillRevision": "a" * 40}), encoding="utf-8"
        )
    else:
        for mirror in MIRRORS:
            root = pinned_source / mirror
            if change == "extra":
                # Stage the case alias below: a Windows checkout cannot hold both spellings.
                pass
            elif change == "frontmatter":
                path = root / "SKILL.md"
                path.write_bytes(path.read_bytes().replace(b"name: unity", b"name: INVALID-unity"))
            else:
                (root / "unsupported.bin").write_bytes(b"binary\x00")
        git(pinned_source, "add", ".")
        if change == "extra":
            for mirror in MIRRORS:
                blob = git(pinned_source, "rev-parse", f"HEAD:{mirror}/references/guide.md")
                git(
                    pinned_source,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{mirror}/references/Guide.md",
                )
        git(pinned_source, "commit", "-m", "new fixture source")
        (pinned_source / "MCPForUnity/package.json").write_text(
            json.dumps({"mcpSkillRevision": git(pinned_source, "rev-parse", "HEAD")}),
            encoding="utf-8",
        )
    # When/Then: generation fails before emitting an artifact or rewriting the declared pin.
    before = (pinned_source / "MCPForUnity/package.json").read_bytes()
    with pytest.raises(ValueError):
        generator(monkeypatch).build_bundle(pinned_source, verify_git=True)
    assert (pinned_source / "MCPForUnity/package.json").read_bytes() == before


def test_repository_artifact_matches_both_canonical_mirrors_and_declared_pin(monkeypatch):
    # Given: the checked-in generated artifact and current canonical guidance.
    target = ROOT / "Server/src/services/skills_bundle.json"
    # When: check mode derives the expected artifact without requiring historical Git objects.
    expected = generator(monkeypatch).build_bundle(ROOT, verify_git=False)
    # Then: stale/missing package data fails the normal tools suite.
    assert target.read_bytes() == expected


def test_server_package_explicitly_includes_the_generated_skill_asset():
    # Given/When: read the actual Python build configuration.
    import tomllib

    metadata = tomllib.loads((ROOT / "Server/pyproject.toml").read_text(encoding="utf-8"))
    # Then: wheel/source builds do not depend on guidance folders outside Server.
    assert "skills_bundle.json" in metadata["tool"]["setuptools"]["package-data"]["services"]


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: unity-mcp-orchestrator\nname: unity-mcp-orchestrator\ndescription: duplicate",
        "name: unity-mcp-orchestrator\ndescription: date\ncreated: 2026-10-17",
        "name: unity-mcp-orchestrator\ndescription: cyclic\ncycle: &cycle [*cycle]",
        "name: unity-mcp-orchestrator\ndescription: finite\nextra: .inf",
    ],
)
def test_generator_refuses_lossy_or_ambiguous_frontmatter(pinned_source, monkeypatch, frontmatter):
    # Given: both mirrors agree but the YAML cannot be preserved as unambiguous JSON.
    module = generator(monkeypatch)
    for mirror in MIRRORS:
        (pinned_source / mirror / "SKILL.md").write_text(
            "---\n" + frontmatter + "\n---\n", encoding="utf-8"
        )
    # When/Then: even non-history check mode refuses instead of silently coercing fields.
    with pytest.raises(ValueError):
        module.build_bundle(pinned_source, verify_git=False)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "/absolute.md",
        "C:/drive.md",
        "references\\guide.md",
        "encoded%2e.md",
        "query.md?x=1",
        "nul\x00.md",
    ],
)
def test_generator_refuses_unsafe_committed_relative_names(pinned_source, monkeypatch, path):
    # Given: a raw committed snapshot boundary containing an unsafe Git-compatible name.
    module = generator(monkeypatch)
    revision, files = module.verified_skill_snapshot(pinned_source)
    files[path] = b"unsafe\n"
    monkeypatch.setattr(module, "verified_skill_snapshot", lambda _repo: (revision, files))
    # When/Then: no unsafe path can enter the generated immutable namespace.
    with pytest.raises(ValueError):
        module.build_bundle(pinned_source, verify_git=True)


def test_verified_generation_refuses_committed_symlink_entries(pinned_source, monkeypatch):
    # Given: a real Git symlink object, representable even without Windows link privileges.
    for mirror in MIRRORS:
        blob = git(pinned_source, "rev-parse", f"HEAD:{mirror}/references/guide.md")
        git(
            pinned_source,
            "update-index",
            "--add",
            "--cacheinfo",
            f"120000,{blob},{mirror}/references/link.md",
        )
    git(pinned_source, "commit", "-m", "linked source fixture")
    (pinned_source / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": git(pinned_source, "rev-parse", "HEAD")}), encoding="utf-8"
    )
    # When/Then: the verified snapshot rejects link objects before bundle publication.
    with pytest.raises(ValueError, match="regular committed file"):
        generator(monkeypatch).build_bundle(pinned_source, verify_git=True)


@pytest.mark.parametrize("verify_git", [False, True])
def test_real_linked_source_root_is_refused(pinned_source, monkeypatch, verify_git):
    # Given: a canonical source directory linked to otherwise identical content.
    root = pinned_source / MIRRORS[0]
    original = root.with_name("owned-linked-target")
    root.rename(original)
    if os.name == "nt":
        # A directory junction needs no symlink privilege. Both paths are owned
        # fixture paths; no existing filesystem target is deleted or modified.
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root), str(original)], capture_output=True, check=True
        )
    else:
        root.symlink_to(original, target_is_directory=True)
    # When/Then: neither check mode nor verified generation traverses linked roots.
    with pytest.raises(ValueError, match="linked|reparse"):
        generator(monkeypatch).build_bundle(pinned_source, verify_git=verify_git)


@pytest.mark.parametrize("kind", ["alias_nodes", "depth", "alias_bytes"])
def test_frontmatter_is_bounded_before_json_expansion(pinned_source, monkeypatch, kind):
    # Given: compact source graphs whose JSON expansion must never reach dumps.
    module = generator(monkeypatch)
    header = "name: unity-mcp-orchestrator\ndescription: Bounded fixture\n"
    if kind == "depth":
        yaml_text = header + "nested: " + "[" * 66 + "x" + "]" * 66 + "\n"
    else:
        levels = 25 if kind == "alias_nodes" else 7
        base = "x" if kind == "alias_nodes" else "x" * 128
        yaml_text = header + "a0: &a0 [" + base + ", " + base + "]\n"
        yaml_text += "".join(f"a{n}: &a{n} [*a{n - 1}, *a{n - 1}]\n" for n in range(1, levels))
        if kind == "alias_bytes":
            monkeypatch.setattr(module, "MAX_FRONTMATTER_BYTES", 2048, raising=False)
    for mirror in MIRRORS:
        (pinned_source / mirror / "SKILL.md").write_text(
            "---\n" + yaml_text + "---\n", encoding="utf-8"
        )

    def unbounded_encoder(*_args, **_kwargs):
        pytest.fail("Unadmitted metadata reached json.dumps")

    monkeypatch.setattr(module.json, "dumps", unbounded_encoder)
    # When/Then: refuse the compact graph before an unbounded expansion is attempted.
    with pytest.raises(ValueError):
        module.build_bundle(pinned_source, verify_git=False)


@pytest.mark.parametrize("verify_git", [False, True])
@pytest.mark.parametrize("ancestor", [".claude", ".claude/skills"])
def test_linked_source_ancestors_are_refused_before_reads(
    pinned_source, monkeypatch, verify_git, ancestor
):
    # Given: an actual identical-content link above the canonical leaf directory.
    root = pinned_source / ancestor
    original = root.with_name("owned-ancestor-target")
    root.rename(original)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root), str(original)], capture_output=True, check=True
        )
    else:
        root.symlink_to(original, target_is_directory=True)
    module = generator(monkeypatch)
    real_read_bytes = Path.read_bytes

    def read_bytes(path):
        assert not path.is_relative_to(pinned_source / MIRRORS[0]), "Linked mirror was read"
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    # When/Then: check mode and verified mode reject before reading linked content.
    with pytest.raises(ValueError, match="linked|reparse"):
        module.build_bundle(pinned_source, verify_git=verify_git)
