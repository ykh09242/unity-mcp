"""Per-launch credentials shared with native clients through a local file."""

import errno
import os
import secrets
import socket
import tempfile
from contextlib import ExitStack, contextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import Final, Iterator

from core.file_lock import file_lock

LOCAL_AUTH_HEADER: Final = "X-Unity-MCP-Token"
LOCAL_AUTH_TOKEN_ENV: Final = "UNITY_MCP_LOCAL_AUTH_TOKEN"
LOCAL_AUTH_FILE_ENV: Final = "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE"


@contextmanager
def local_http_sockets(host: str, port: int) -> Iterator[list[socket.socket]]:
    """Own every resolved listener before making the launch credential discoverable."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    seen = set()
    with ExitStack() as stack:
        sockets = []
        assigned_port = port
        for family, kind, protocol, _, address in addresses:
            if port == 0 and assigned_port:
                address = (address[0], assigned_port, *address[2:])
            key = (family, kind, protocol, address)
            if key in seen:
                continue
            seen.add(key)
            try:
                listener = stack.enter_context(socket.socket(family, kind, protocol))
                if os.name == "nt":
                    # The Windows socket option is absent from POSIX Python builds.
                    # pylint: disable-next=no-member
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                listener.bind(address)
                listener.listen(socket.SOMAXCONN)
            except OSError as exc:
                if exc.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL):
                    continue
                raise
            assigned_port = listener.getsockname()[1]
            sockets.append(listener)
        if not sockets:
            raise OSError(f"No addresses available for {host}:{port}")
        yield sockets


def local_auth_token_path(port: int) -> Path:
    """Resolve the shared file without exposing the token through HTTP or logs."""
    override = os.environ.get(LOCAL_AUTH_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".unity-mcp" / "auth" / f"token-{port}"


@contextmanager
def local_auth_token(port: int) -> Iterator[str]:
    """Own the token file for the launch lifetime before atomically publishing it."""
    path = local_auth_token_path(port)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Normalize directory aliases without following an existing token-file symlink:
    # publication replaces that directory entry, matching the discovery contract.
    path = path.parent.resolve() / path.name
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(path.with_name(path.name + ".lock"), wait=False))
        except BlockingIOError as error:
            raise BlockingIOError(
                "Local authentication token file is already owned by another server"
            ) from error
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
                # Preserve a file changed externally while this launch was running.
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
