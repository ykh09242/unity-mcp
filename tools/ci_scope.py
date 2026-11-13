"""Select a conservative fork-beta validation profile and verify its final gate."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode


TOOLS_ONLY = frozenset(
    {
        "tools/ci_test_performance.py",
        "tools/tests/test_ci_test_performance.py",
        "tools/check_workflows.py",
        "tools/tests/test_check_workflows.py",
        "tools/tests/test_fork_workflows.py",
        "tools/tests/test_python_tests_workflow.py",
        "tools/tests/test_workflow_permissions.py",
    }
)

# Keep in sync with release-qualification.yml's beta push paths. The tests
# compare these prefixes and files to the actual workflow filter.
QUALIFICATION_PREFIXES = (
    "MCPForUnity/",
    "Server/",
    "TestProjects/UnityMCPTests/",
    "CustomTools/",
    "tools/",
    ".github/workflows/",
    ".github/actions/",
)
QUALIFICATION_FILES = frozenset({"manifest.json", "release-source-inputs.json"})


def qualification_selected(changes: str) -> bool:
    """Delegate only when the complete, bounded diff selects qualification."""
    parts = changes.split("\0")
    if not changes or parts[-1] != "" or len(parts) % 2 != 1:
        return False
    records = list(zip(parts[:-1:2], parts[1:-1:2]))
    if len(records) > 300 or len({path for _, path in records}) != len(records):
        return False
    if not all(status in {"A", "M", "D", "T"} and path for status, path in records):
        return False
    return any(
        path in QUALIFICATION_FILES or path.startswith(QUALIFICATION_PREFIXES)
        for _, path in records
    )


def classify(changes: str) -> str:
    """Only known, modified files can omit product verification."""
    parts = changes.split("\0")
    if not changes or parts[-1] != "" or len(parts) % 2 != 1:
        return "full"
    records = list(zip(parts[:-1:2], parts[1:-1:2]))
    if not records or len({path for _, path in records}) != len(records):
        return "full"
    return (
        "tools-only"
        if all(status == "M" and path in TOOLS_ONLY for status, path in records)
        else "full"
    )


def validated_base(repository: str, sha: str) -> bool:
    """A tooling push must not inherit failed or cancelled product validation."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return False
    query = urlencode(
        {"branch": "beta", "event": "push", "head_sha": sha, "status": "success", "per_page": 5}
    )
    endpoint = f"repos/{repository}/actions/workflows/fork-beta-tools.yml/runs?{query}"
    result = subprocess.run(
        ["gh", "api", "--method", "GET", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return any(
        run["head_sha"] == sha
        and run["head_branch"] == "beta"
        and run["event"] == "push"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        for run in json.loads(result.stdout)["workflow_runs"]
    )


def select_scope(
    event: Path | None, repository: Path, github_repository: str = ""
) -> tuple[str, str, bool]:
    if event is None:
        return "full", "No push event available", False
    try:
        payload = json.loads(event.read_text(encoding="utf-8"))
        before, after = payload["before"], payload["after"]
        if any(
            not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha) or sha == "0" * 40
            for sha in (before, after)
        ):
            return "full", "Missing or invalid push range", False

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args],
                cwd=repository,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=True,
                timeout=30,
            ).stdout

        if git("rev-parse", "HEAD").strip() != after:
            return "full", "Checkout does not match the push", False
        git("merge-base", "--is-ancestor", before, after)
        changes = git("diff", "--name-status", "-z", "--no-renames", before, after, "--")
        profile = classify(changes)
        qualification = qualification_selected(changes)
        if profile == "tools-only" and not validated_base(github_repository, before):
            return "full", "Previous commit has no verified successful fork-beta run", qualification
        return (
            profile,
            "Only allowlisted tooling changed"
            if profile == "tools-only"
            else "Full validation required by changed paths",
            qualification,
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return "full", "Push range could not be verified", False


def select(event: Path | None, repository: Path, github_repository: str = "") -> tuple[str, str]:
    profile, reason, _ = select_scope(event, repository, github_repository)
    return profile, reason


def gate(profile: str, classification: str, python: str, unity: str, codex: str) -> bool:
    return (
        classification == "success"
        and python == "success"
        and (
            (profile == "full" and unity == "success" and codex == "success")
            or (profile == "tools-only" and unity == "skipped" and codex == "skipped")
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("select")
    event = os.environ.get("GITHUB_EVENT_PATH")
    selection.add_argument("--event", type=Path, default=Path(event) if event else None)
    selection.add_argument("--repository", type=Path, default=Path.cwd())
    check = commands.add_parser("check")
    for name in ("profile", "classification", "python", "unity", "codex"):
        check.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        passed = gate(args.profile, args.classification, args.python, args.unity, args.codex)
        print(
            "Selected validation passed"
            if passed
            else "Selected validation failed or was unexpectedly skipped"
        )
        return 0 if passed else 1
    profile, reason, qualification = select_scope(
        args.event, args.repository, os.environ.get("GITHUB_REPOSITORY", "")
    )
    print(f"Validation profile: {profile}. {reason}.")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"profile={profile}\n")
            stream.write(f"qualification_selected={str(qualification).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
