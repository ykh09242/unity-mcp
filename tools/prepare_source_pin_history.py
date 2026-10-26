"""Fetch a missing validated immutable server pin before the offline drift check."""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Final

from check_release_evidence import BASE, EvidenceError, digest, git, require
from check_source_pin_drift import resolve_commit, source_pin


FETCH_TIMEOUT: Final = 60
FORK_GIT_URL: Final = BASE + ".git"


@dataclass(frozen=True, slots=True)
class PreparedHistory:
    checked_commit: str
    server_commit: str
    fetched: bool


def fetch_pin(repo: Path, pin: str) -> None:
    """Fetch only the selected object from the fixed public fork without updating refs."""
    require(digest(pin, 40), "Expected a full immutable source pin")
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "--no-optional-locks",
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "http.extraHeader=",
            "-C",
            str(repo),
            "fetch",
            "--depth=1",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            "--no-auto-maintenance",
            FORK_GIT_URL,
            pin,
        ],
        capture_output=True,
        check=False,
        timeout=FETCH_TIMEOUT,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
        },
    )
    require(result.returncode == 0, "Pinned history fetch failed")


def prepare(repo: Path, revision: str = "HEAD") -> PreparedHistory:
    """Validate committed metadata and retrieve only a missing exact source commit."""
    checked = resolve_commit(repo, revision)
    pin = source_pin(repo, checked)
    try:
        object_type = git(repo, "cat-file", "-t", pin)
    except EvidenceError:
        fetch_pin(repo, pin)
        fetched = True
    else:
        require(object_type.strip() == b"commit", "Pinned object is not a commit")
        fetched = False
    require(resolve_commit(repo, pin) == pin, "Pinned object is not a commit")
    return PreparedHistory(checked, pin, fetched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.repo, args.revision)
    except (
        EvidenceError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.TimeoutExpired,
    ):
        # Never disclose rejected metadata, Git stderr, credentials or local paths.
        print(json.dumps({"state": "unavailable", "reason": "invalid_or_unavailable_pin_history"}))
        return 2
    print(json.dumps({"state": "available", **asdict(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
