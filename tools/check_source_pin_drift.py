"""Report immutable launcher drift using committed local objects only."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tomllib

from check_release_evidence import BASE, EvidenceError, committed, digest, git, load_json, require


IGNORED_SERVER_FILES = frozenset(
    {"Server/README.md", "Server/DOCKER_OVERVIEW.md", "Server/pyrightconfig.json"}
)
DISPLAY_PATHS = 100


def resolve_commit(repo: Path, revision: str) -> str:
    value = git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    result = value.decode("ascii").strip()
    require(digest(result, 40), "Expected a full local commit")
    return result


def source_pin(repo: Path, revision: str) -> str:
    package = load_json(committed(repo, revision, "MCPForUnity/package.json"))
    require(package.get("name") == "com.ykh09242.unity-mcp", "Wrong Unity package identity")
    source = package.get("mcpServerSource")
    require(isinstance(source, str), "Missing source archive")
    match = re.fullmatch(
        re.escape(BASE) + r"/archive/([0-9a-f]{40})\.zip#subdirectory=Server", source
    )
    require(match is not None and digest(match[1], 40), "Expected an immutable fork archive")
    pin = match[1]
    manifest = load_json(committed(repo, revision, "manifest.json"))
    args = manifest.get("server", {}).get("mcp_config", {}).get("args", [])
    require(isinstance(args, list) and args.count("--from") == 1, "Expected one manifest source")
    position = args.index("--from") + 1
    require(position < len(args) and args[position] == source, "Manifest source differs")
    require(manifest.get("version") == package.get("version"), "Package versions differ")
    inputs = load_json(committed(repo, revision, "release-source-inputs.json"))
    require(
        inputs
        == {
            "schema_version": 1,
            "repository": BASE,
            "server_commit": pin,
            "skill_commit": package.get("mcpSkillRevision"),
            "server_source": source,
        },
        "Generated source inputs differ",
    )
    return pin


def inspect(
    repo: Path, revision: str = "HEAD", baseline: str | None = None, *, strict: bool = False
) -> dict:
    report = {
        "schema_version": 1,
        "state": "unavailable",
        "checked_commit": None,
        "server_commit": None,
        "baseline_commit": None,
        "pin_changed": False,
        "requires_current": strict,
        "changed_path_count": 0,
        "changed_paths": [],
        "paths_truncated": False,
        "reason": "invalid_or_unavailable_inputs",
        "next_action": "provide_full_local_history_and_consistent_immutable_source_metadata",
    }
    try:
        checked = resolve_commit(repo, revision)
        report["checked_commit"] = checked
        pin = source_pin(repo, checked)
        report["server_commit"] = pin
        require(resolve_commit(repo, pin) == pin, "Pinned object is not a commit")
        project = tomllib.loads(committed(repo, pin, "Server/pyproject.toml").decode("utf-8"))
        require(
            project.get("project", {}).get("name") == "ykh09242-unity-mcp-server",
            "Wrong server identity",
        )
        if baseline is not None:
            base = resolve_commit(repo, baseline)
            report["baseline_commit"] = base
            report["pin_changed"] = source_pin(repo, base) != pin
            report["requires_current"] = strict or report["pin_changed"]
        raw = git(
            repo,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            pin,
            checked,
            "--",
            "Server",
        )
        paths = [
            path.decode("utf-8", errors="replace")
            for path in raw.split(b"\0")
            if path
            and not path.startswith(b"Server/tests/")
            and path.decode("utf-8", errors="replace") not in IGNORED_SERVER_FILES
        ]
        report.update(
            state="stale" if paths else "current",
            changed_path_count=len(paths),
            changed_paths=paths[:DISPLAY_PATHS],
            paths_truncated=len(paths) > DISPLAY_PATHS,
            reason="server_inputs_differ" if paths else "same_server_inputs",
            next_action="review_and_refresh_to_a_validated_merged_server_commit"
            if paths
            else "none",
        )
    except (
        EvidenceError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.TimeoutExpired,
    ):
        # Never include Git stderr, a filesystem location or rejected metadata in diagnostics.
        pass
    return report


def exit_status(report: dict) -> int:
    if report["state"] == "unavailable":
        return 2
    return int(report["state"] == "stale" and report["requires_current"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument(
        "--baseline", help="A changed source pin must match the checked server inputs"
    )
    parser.add_argument("--require-current", action="store_true")
    args = parser.parse_args(argv)
    report = inspect(args.repo, args.revision, args.baseline, strict=args.require_current)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return exit_status(report)


if __name__ == "__main__":
    raise SystemExit(main())
