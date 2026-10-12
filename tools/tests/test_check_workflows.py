"""Verify that workflow tooling rejects untrusted downloads before execution."""

import hashlib
import io
from pathlib import Path
import tarfile
import zipfile

import pytest
import yaml

from tools import check_workflows as checker


def test_workflow_lint_runs_independently_with_the_local_pinned_command():
    config = yaml.safe_load(
        (checker.ROOT / ".github/workflows/workflow-lint.yml").read_text(encoding="utf-8")
    )
    job = config["jobs"]["actionlint"]
    assert "needs" not in job
    assert "continue-on-error" not in job
    assert job["steps"][-1]["run"] == "python3 tools/check_workflows.py --download"


def test_uncached_tool_requires_explicit_download(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "urlopen", lambda *a, **kw: pytest.fail("Unexpected network"))
    with pytest.raises(ValueError, match="--download"):
        checker.verified_archive(tmp_path, "linux_amd64", "tool.tar.gz", False)


def test_corrupt_cached_tool_is_never_used_or_silently_replaced(tmp_path, monkeypatch):
    path = tmp_path / "tool.zip"
    path.write_bytes(b"corrupt")
    monkeypatch.setattr(checker, "urlopen", lambda *a, **kw: pytest.fail("Unexpected network"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        checker.verified_archive(tmp_path, "windows_amd64", path.name, True)
    assert path.read_bytes() == b"corrupt"


def test_download_is_verified_and_reused_offline(tmp_path, monkeypatch):
    content = b"verified archive fixture"
    monkeypatch.setitem(checker.CHECKSUMS, "fixture", hashlib.sha256(content).hexdigest())
    requests = []

    def download(url, *, timeout):
        requests.append((url, timeout))
        return io.BytesIO(content)

    monkeypatch.setattr(checker, "urlopen", download)
    assert checker.verified_archive(tmp_path, "fixture", "tool.zip", True) == content
    assert checker.verified_archive(tmp_path, "fixture", "tool.zip", False) == content
    assert len(requests) == 1
    assert requests[0][0].startswith("https://github.com/rhysd/actionlint/releases/download/")


@pytest.mark.parametrize("windows", [True, False])
def test_only_the_named_executable_is_read_from_archive(tmp_path: Path, windows: bool):
    content = io.BytesIO()
    if windows:
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("actionlint.exe", b"binary")
            archive.writestr("../escape", b"unrelated")
    else:
        with tarfile.open(fileobj=content, mode="w:gz") as archive:
            for name, payload in (("actionlint", b"binary"), ("../escape", b"unrelated")):
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    assert checker.executable_bytes(content.getvalue(), windows) == b"binary"
    assert list(tmp_path.iterdir()) == []
