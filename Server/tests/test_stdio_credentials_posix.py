"""Private owned files only; never read a real user's launch credentials."""
import os
from pathlib import Path

import pytest

from transport.legacy.stdio_credentials import read_stdio_token

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and no-follow filesystem contract")
GENERATION = "a" * 32
TOKEN = "synthetic-owned-token-" * 3


@pytest.fixture
def credential(tmp_path, monkeypatch):
    home = tmp_path / "owned-home"
    directory = home / ".unity-mcp" / "stdio-auth" / GENERATION
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(home, 0o700)
    os.chmod(directory, 0o700)
    token = directory / "token"
    token.write_text(TOKEN, encoding="utf-8")
    os.chmod(token, 0o600)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home, directory, token


def test_private_owned_credential_and_strict_generation(credential):
    assert read_stdio_token(GENERATION) == TOKEN
    for generation in ("../token", "A" * 32, "a" * 31, "a" * 33):
        assert read_stdio_token(generation) is None


@pytest.mark.parametrize("target", ["token", "generation", "ancestor"])
def test_permission_or_symlink_escape_fails_closed(credential, target):
    home, directory, token = credential
    path = {"token": token, "generation": directory, "ancestor": directory.parent}[target]
    os.chmod(path, 0o777)
    assert read_stdio_token(GENERATION) is None
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
    owned = home / ("parked-" + target)
    path.rename(owned)
    path.symlink_to(owned, target_is_directory=owned.is_dir())
    assert read_stdio_token(GENERATION) is None
