"""Vendor blobs must survive fresh Windows worktrees without phantom edits."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
VENDOR = "TestProjects/AssetStoreUploads/Packages/com.unity.asset-store-tools"
LONG_SOURCE = (
    f"{VENDOR}/Editor/Previews/Scripts/Generators/Custom/TypeGenerators/"
    "TypePreviewGeneratorFromSceneSettings.cs"
)


def git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    """Use per-command long-path support, without changing anyone's Git config."""
    return subprocess.run(
        [
            "git",
            "-c",
            "core.longpaths=true",
            "-c",
            "core.safecrlf=false",
            "-c",
            f"safe.directory={root.as_posix()}",
            "-C",
            str(root),
            *arguments,
        ],
        input=data,
        capture_output=True,
        check=True,
    ).stdout


def make_seed(tmp_path: Path, autocrlf: str, attributes: bytes) -> tuple[Path, dict[str, bytes]]:
    """Create disposable Git history with the same mixed vendor blob endings."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init")
    git(seed, "config", "user.name", "Worktree Regression")
    git(seed, "config", "user.email", "worktree-test@example.invalid")
    git(seed, "config", "core.autocrlf", autocrlf)
    (seed / ".gitattributes").write_bytes(attributes)
    (seed / "Authored.cs").write_bytes(b"// Authored LF source\n")
    git(seed, "add", ".gitattributes", "Authored.cs")

    # These upstream files entered history as CRLF blobs before the authored LF rules.
    # Bypass clean filters to reproduce that existing history, not normalized new files.
    originals = {
        LONG_SOURCE: b"// Original vendor source\r\nclass Vendor {}\r\n",
        f"{VENDOR}/package.json": b'{"name":"vendor"}\r\n',
        f"{VENDOR}/Editor/Example.cs.meta": b"fileFormatVersion: 2\n",
    }
    for relative, content in originals.items():
        blob = git(seed, "hash-object", "-w", "--stdin", data=content).decode().strip()
        git(seed, "update-index", "--add", "--cacheinfo", f"100644,{blob},{relative}")
    git(seed, "commit", "-m", "Seed historical vendor bytes")
    return seed, originals


@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_fresh_deep_worktree_preserves_vendor_bytes_and_removes_cleanly(tmp_path, autocrlf):
    seed, originals = make_seed(tmp_path, autocrlf, (ROOT / ".gitattributes").read_bytes())

    checkout = tmp_path / ("deep-worktree-" + "x" * 60) / "checkout with spaces"
    assert len(str(checkout / LONG_SOURCE)) > 260
    assert checkout.resolve().is_relative_to(tmp_path.resolve())
    try:
        git(seed, "worktree", "add", "--detach", str(checkout), "HEAD")
        assert git(checkout, "status", "--porcelain") == b"", (
            "A fresh checkout must not report vendor CRLF normalization as user edits"
        )
        for relative, original in originals.items():
            actual = (checkout / relative).read_bytes()
            if relative.endswith(".meta") and autocrlf == "true":
                assert actual == original.replace(b"\n", b"\r\n")
            else:
                assert actual == original
        assert (checkout / "Authored.cs").read_bytes() == b"// Authored LF source\n"

        # Real changes must still block ordinary removal and remain available for preservation.
        source = checkout / LONG_SOURCE
        source.write_bytes(originals[LONG_SOURCE] + b"// User edit\r\n")
        with pytest.raises(subprocess.CalledProcessError):
            git(seed, "worktree", "remove", str(checkout))
        assert source.read_bytes().endswith(b"// User edit\r\n")
        source.write_bytes(originals[LONG_SOURCE])
        git(seed, "worktree", "remove", str(checkout))
        assert not checkout.exists()
        assert checkout.as_posix().encode() not in git(seed, "worktree", "list", "--porcelain")
    finally:
        # Only this test's disposable fixture can be removed if an assertion fails.
        if checkout.exists():
            assert checkout.resolve().is_relative_to(tmp_path.resolve())
            git(seed, "worktree", "remove", "--force", str(checkout))


def test_old_branch_scoped_attribute_override_preserves_vendor_bytes(tmp_path):
    attributes = (ROOT / ".gitattributes").read_bytes().splitlines(keepends=True)
    overrides = [line for line in attributes if line.startswith(f"/{VENDOR}/".encode())]
    assert len(overrides) == 2
    historical = b"".join(line for line in attributes if line not in overrides)
    seed, originals = make_seed(tmp_path, "true", historical)
    checkout = tmp_path / "old checkout"
    info_attributes = seed / ".git" / "info" / "attributes"
    previous = b"# Existing local attributes must survive cleanup\n"
    info_attributes.write_bytes(previous)
    assert checkout.resolve().is_relative_to(tmp_path.resolve())
    try:
        git(seed, "worktree", "add", "--detach", str(checkout), "HEAD")
        assert git(checkout, "status", "--porcelain")
        before = {relative: (checkout / relative).read_bytes() for relative in originals}
        # A temporary high-precedence override works even on branches predating the fix.
        info_attributes.write_bytes(previous + b"".join(overrides))
        assert git(checkout, "status", "--porcelain") == b""
        assert {relative: (checkout / relative).read_bytes() for relative in originals} == before
        git(seed, "worktree", "remove", str(checkout))
        assert not checkout.exists()
    finally:
        info_attributes.write_bytes(previous)
        if checkout.exists():
            assert checkout.resolve().is_relative_to(tmp_path.resolve())
            git(seed, "worktree", "remove", "--force", str(checkout))
    assert info_attributes.read_bytes() == previous
