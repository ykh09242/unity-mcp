"""Process-frozen, non-secret build identity for version diagnostics."""

from dataclasses import dataclass
from importlib import metadata
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from typing_extensions import TypedDict

from core.telemetry import MCP_VERSION, PACKAGE_NAME


class ServerBuildMetadata(TypedDict):
    version: str
    source_commit: str | None
    server_id: str


def commit_from_direct_url(document: str | None) -> str | None:
    """Extract only an immutable Git commit; never disclose a source URL or credentials."""
    if not document or len(document) > 65_536:
        return None
    try:
        data = json.loads(document)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    vcs = data.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git":
        commit = vcs.get("commit_id")
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", commit):
            return commit.lower()
    url = data.get("url")
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    match = re.fullmatch(
        r"/[^/]+/[^/]+/archive/([0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?)\.(?:zip|tar\.gz)",
        parsed.path,
    )
    return match.group(1).lower() if match else None


@dataclass(frozen=True, slots=True)
class ServerBuild:
    version: str
    source_commit: str | None
    server_id: str

    def command_metadata(self) -> ServerBuildMetadata:
        """Return a fresh envelope so one request cannot alter the frozen identity."""
        return {
            "version": self.version,
            "source_commit": self.source_commit,
            "server_id": self.server_id,
        }


def _capture_running_build() -> ServerBuild:
    """Read installed metadata once while loading the server, without Git or network calls."""
    try:
        direct_url = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except (metadata.PackageNotFoundError, OSError, UnicodeError):
        direct_url = None
    version = MCP_VERSION if re.fullmatch(r"[0-9A-Za-z+_.-]{1,80}", MCP_VERSION) else "unknown"
    return ServerBuild(version, commit_from_direct_url(direct_url), uuid4().hex)


# Installed files can change while an externally owned stdio process keeps running.
# Its diagnostics must continue to describe the process loaded before that update.
RUNNING_SERVER = _capture_running_build()
