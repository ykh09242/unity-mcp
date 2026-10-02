"""Per-launch credentials shared with native clients through a local file."""

import os
import secrets
import tempfile
from contextlib import contextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import Final, Iterator

LOCAL_AUTH_HEADER: Final = "X-Unity-MCP-Token"
LOCAL_AUTH_TOKEN_ENV: Final = "UNITY_MCP_LOCAL_AUTH_TOKEN"
LOCAL_AUTH_FILE_ENV: Final = "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE"


def local_auth_token_path(port: int) -> Path:
    """Resolve the shared file without exposing the token through HTTP or logs."""
    override = os.environ.get(LOCAL_AUTH_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".unity-mcp" / "auth" / f"token-{port}"


@contextmanager
def local_auth_token(port: int) -> Iterator[str]:
    """Publish a new 256-bit token atomically, and remove it on clean shutdown."""
    path = local_auth_token_path(port)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd, temporary_path = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    try:
        # mkstemp creates mode 0600 on POSIX. Windows inherits the user's ACL.
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        os.replace(temporary_path, path)
        try:
            yield token
        finally:
            # A newer launch may have replaced this file. Never remove its token.
            if path.exists() and path.read_text(encoding="utf-8").strip() == token:
                path.unlink()
    finally:
        Path(temporary_path).unlink(missing_ok=True)


def read_local_auth_token(host: str, port: int) -> str | None:
    """Read client credentials; never auto-send a loopback token to another host."""
    explicit_token = os.environ.get(LOCAL_AUTH_TOKEN_ENV)
    if explicit_token:
        return explicit_token.strip()
    normalized_host = host.strip("[]").lower()
    is_loopback = normalized_host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ip_address(normalized_host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback and not os.environ.get(LOCAL_AUTH_FILE_ENV):
        return None
    try:
        return local_auth_token_path(port).read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
