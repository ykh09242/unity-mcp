"""Report conservative Unity cache retention candidates; apply only a fresh reviewed plan."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Final, Protocol, TypeAlias
from urllib.parse import urlencode, urlsplit

import unity_ci
import unity_ci_package_cache as optional_cache
import unity_ci_packages as packages
import unity_compile_cache as sdk_cache


ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA: Final = 1
SAFETY_WINDOW: Final = timedelta(hours=24)
PLAN_LIFETIME: Final = timedelta(hours=1)
PAGE_SIZE: Final = 100
MAX_PAGES: Final = 20
MAX_RESPONSE_BYTES: Final = 4 * 1024 * 1024
ACTIVE_STATUSES: Final = ("queued", "in_progress", "waiting", "pending", "requested")
IDENTITY_INPUTS: Final = tuple(
    sorted(
        {
            "tools/unity-versions.json",
            "tools/unity-ci-packages.json",
            "tools/unity_compile_cache.py",
            *optional_cache.LOGIC_INPUTS,
            *optional_cache.MANIFEST_INPUTS,
        }
    )
)
KEY_PATTERN: Final = re.compile(
    r"unity-ci-(sdk|optional)-v([1-9][0-9]*)-linux-amd64-"
    r"([0-9]{4}\.[0-9]+\.[0-9]+[abfp][0-9]+)-[a-f0-9]{64}\Z"
)
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonDocument: TypeAlias = dict[str, JsonValue]
CurrentKeys: TypeAlias = dict[tuple[str, str], str]


class RetentionError(ValueError):
    """A complete safe retention decision could not be established."""


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Validated public cache metadata, never cache contents."""

    id: int
    key: str
    ref: str
    version: str
    size_in_bytes: int
    created_at: datetime
    last_accessed_at: datetime

    def record(self) -> JsonDocument:
        """Return stable metadata for review and fresh apply comparisons."""
        return {
            "id": self.id,
            "key": self.key,
            "ref": self.ref,
            "version": self.version,
            "size_in_bytes": self.size_in_bytes,
            "created_at": self.created_at.isoformat(),
            "last_accessed_at": self.last_accessed_at.isoformat(),
        }


def timestamp(value: JsonValue) -> datetime:
    """Parse a timezone-bearing API or plan timestamp, rejecting absent/ambiguous values."""
    if not isinstance(value, str):
        raise RetentionError("Timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetentionError("Invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise RetentionError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_entry(value: JsonValue) -> CacheEntry:
    """Reject incomplete or malformed inventory rows before planning."""
    if not isinstance(value, dict):
        raise RetentionError("Cache entry must be an object")
    identifier, size = value.get("id"), value.get("size_in_bytes")
    if type(identifier) is not int or identifier <= 0 or type(size) is not int or size < 0:
        raise RetentionError("Invalid cache ID or size")
    for field in ("key", "ref", "version"):
        text = value.get(field)
        if not isinstance(text, str) or not text or any(ord(char) < 32 for char in text):
            raise RetentionError(f"Invalid cache {field}")
    created = timestamp(value.get("created_at"))
    accessed = timestamp(value.get("last_accessed_at"))
    if accessed < created:
        raise RetentionError("Cache last access predates creation")
    return CacheEntry(
        identifier, value["key"], value["ref"], value["version"], size, created, accessed
    )


def namespace(entry: CacheEntry) -> tuple[str, str] | None:
    """Recognize only supported Unity cache families/schemas; preserve future namespaces."""
    match = KEY_PATTERN.fullmatch(entry.key)
    if not match:
        return None
    family, schema, version = match.groups()
    supported = {"sdk": sdk_cache.SCHEMA, "optional": optional_cache.SCHEMA}
    if int(schema) > supported[family]:
        return None
    return family, version


def current_keys() -> CurrentKeys:
    """Derive current keys through the same validated helpers used by CI."""
    manifest_path = ROOT / "tools/unity-versions.json"
    manifest = unity_ci.load_manifest(manifest_path)
    config = packages.read_json(packages.PROFILES)
    result = {}
    for row in manifest.versions:
        result[("sdk", row.id)] = sdk_cache.identity(manifest, row.id)["cache_key"]
        if packages.optional_matches(config, row.id):
            request = optional_cache.CacheRequest(row.id, ROOT, manifest_path, packages.PROFILES)
            result[("optional", row.id)] = optional_cache.identity(request).cache_key
    return result


def build_plan(
    entries: tuple[CacheEntry, ...],
    keys: CurrentKeys,
    repository: str,
    ref: str,
    now: datetime,
    active_run_count: int,
) -> JsonDocument:
    """Protect current/previous generations and report only old, unused, scoped caches."""
    protected = {}
    for group, current in keys.items():
        members = [row for row in entries if row.ref == ref and namespace(row) == group]
        current_rows = [row for row in members if row.key == current]
        if not current_rows:
            protected.update({row.id: "current-key-not-published" for row in members})
            continue
        first_current = min(row.created_at for row in current_rows)
        older = [row for row in members if row.key != current and row.created_at < first_current]
        # A GitHub archive version can repeat a key. Generation age uses its first publication.
        generations = {
            row.key: min(item.created_at for item in older if item.key == row.key) for row in older
        }
        previous_time = max(generations.values(), default=None)
        previous_keys = {key for key, created in generations.items() if created == previous_time}
        for row in members:
            if row.key == current:
                protected[row.id] = "current-generation"
            elif row.key in previous_keys:
                protected[row.id] = "previous-generation"
            elif row.created_at >= first_current:
                protected[row.id] = "unconfirmed-newer-generation"
    candidates, preserved = [], []
    for row in sorted(entries, key=lambda item: (item.ref, item.key, item.id)):
        reason = protected.get(row.id)
        if row.ref != ref:
            reason = "other-ref"
        if namespace(row) is None:
            reason = "unknown-namespace"
        if now - row.created_at < SAFETY_WINDOW or now - row.last_accessed_at < SAFETY_WINDOW:
            reason = "recently-created-or-accessed"
        if active_run_count:
            reason = "active-workflow-runs"
        if reason:
            preserved.append({**row.record(), "reason": reason})
        else:
            candidates.append(row.record())
    return {
        "schema": SCHEMA,
        "repository": repository,
        "ref": ref,
        "generated_at": now.isoformat(),
        "minimum_age_hours": 24,
        "last_access_grace_hours": 24,
        "active_run_count": active_run_count,
        "current_keys": [
            {"family": family, "unity_version": version, "key": key}
            for (family, version), key in sorted(keys.items())
        ],
        "inventory_count": len(entries),
        "inventory_bytes": sum(row.size_in_bytes for row in entries),
        "candidate_count": len(candidates),
        "candidate_bytes": sum(row["size_in_bytes"] for row in candidates),
        "candidates": candidates,
        "preserved": preserved,
    }


class Api(Protocol):
    """Small gh transport boundary; tests use an in-memory provider."""

    def request(self, endpoint: str, method: str = "GET") -> JsonValue:
        """Return decoded response or raise; DELETE returns null on 204."""


def command(arguments: list[str]) -> str:
    """Bound subprocess execution and omit credential-bearing stderr from diagnostics."""
    try:
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RetentionError(f"{arguments[0]} command unavailable or timed out") from exc
    if result.returncode or len(result.stdout.encode()) > MAX_RESPONSE_BYTES:
        raise RetentionError(f"{arguments[0]} command failed or response exceeded size limit")
    return result.stdout


@dataclass(frozen=True, slots=True)
class GitHubApi:
    """GitHub REST transport using the existing gh login without inspecting its credentials."""

    def request(self, endpoint: str, method: str = "GET") -> JsonValue:
        output = command(
            [
                "gh",
                "api",
                "--method",
                method,
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
                endpoint,
            ]
        )
        if method == "DELETE" and not output.strip():
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RetentionError("GitHub returned invalid JSON") from exc


def origin_repository(value: str) -> str:
    """Parse only a public GitHub origin identity; never include raw URLs in diagnostics."""
    ssh = re.fullmatch(r"git@github\.com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", value)
    if ssh:
        return ssh.group(1)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise RetentionError("Origin must be a public HTTPS or SSH GitHub repository URL")
    repository = parsed.path.removeprefix("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RetentionError("Invalid origin repository identity")
    return repository


def checkout_binding(repository: str, ref: str) -> None:
    """Require clean identity inputs and a checkout at the actual origin branch tip."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RetentionError("Repository must be OWNER/REPO")
    origin = origin_repository(command(["git", "remote", "get-url", "origin"]).strip())
    if origin.lower() != repository.lower():
        raise RetentionError("Selected repository does not match origin")
    if (
        not ref.startswith("refs/heads/")
        or command(["git", "symbolic-ref", "--quiet", "HEAD"]).strip() != ref
    ):
        raise RetentionError("Selected ref must match the checked-out branch")
    command(["git", "ls-files", "--error-unmatch", "--", *IDENTITY_INPUTS])
    if command(["git", "diff", "--name-only", "HEAD", "--", *IDENTITY_INPUTS]).strip():
        raise RetentionError(
            "Cache identity inputs have local changes; use a clean published checkout"
        )
    local = command(["git", "rev-parse", "HEAD"]).strip()
    remote = command(["git", "ls-remote", "--exit-code", "origin", ref]).split()
    if not re.fullmatch(r"[a-f0-9]{40}", local) or remote != [local, ref]:
        raise RetentionError("Checkout HEAD does not match the live origin branch tip")


def inventory(
    api: Api, repository: str, resource: str, field: str, query: JsonDocument
) -> list[JsonValue]:
    """Read every bounded page; changing counts, duplicates and truncation fail closed."""
    records, identifiers = [], set()
    expected = None
    for page in range(1, MAX_PAGES + 1):
        parameters = {**query, "per_page": PAGE_SIZE, "page": page}
        data = api.request(f"repos/{repository}/actions/{resource}?{urlencode(parameters)}")
        if not isinstance(data, dict):
            raise RetentionError("Inventory page must be an object")
        count, rows = data.get("total_count"), data.get(field)
        limit = min(PAGE_SIZE * MAX_PAGES, 1000) if resource == "runs" else PAGE_SIZE * MAX_PAGES
        if type(count) is not int or not 0 <= count <= limit or not isinstance(rows, list):
            raise RetentionError("Invalid inventory count or rows")
        if expected is None:
            expected = count
        if count != expected or len(rows) > PAGE_SIZE:
            raise RetentionError("Inventory changed during pagination")
        for row in rows:
            if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
                raise RetentionError("Invalid inventory ID")
            if row["id"] in identifiers:
                raise RetentionError("Duplicate inventory ID")
            identifiers.add(row["id"])
            records.append(row)
        if len(records) > expected:
            raise RetentionError("Inventory exceeds reported total")
        if len(records) == expected:
            return records
        if len(rows) != PAGE_SIZE:
            raise RetentionError("Incomplete inventory page")
    raise RetentionError("Inventory pagination exceeded limit")


def active_runs(api: Api, repository: str) -> int:
    """Count all noncompleted run statuses, validating their complete paginated inventories."""
    identifiers = set()
    for status in ACTIVE_STATUSES:
        for row in inventory(api, repository, "runs", "workflow_runs", {"status": status}):
            if row.get("status") != status:
                raise RetentionError("Unexpected active workflow status")
            identifiers.add(row["id"])
    return len(identifiers)


def fresh_plan(api: Api, repository: str, ref: str, now: datetime) -> JsonDocument:
    """Capture validated source identities, complete metadata and workflow activity."""
    checkout_binding(repository, ref)
    rows = inventory(
        api, repository, "caches", "actions_caches", {"sort": "created_at", "direction": "asc"}
    )
    entries = tuple(parse_entry(row) for row in rows)
    return build_plan(entries, current_keys(), repository, ref, now, active_runs(api, repository))


def apply_plan(
    api: Api, reviewed: JsonValue, repository: str, ref: str, now: datetime
) -> list[int]:
    """Revalidate the whole reviewed set before mutation and each exact ID before deletion."""
    if (
        not isinstance(reviewed, dict)
        or type(reviewed.get("schema")) is not int
        or reviewed.get("schema") != SCHEMA
    ):
        raise RetentionError("Unsupported reviewed plan")
    age = now - timestamp(reviewed.get("generated_at"))
    if not timedelta(0) <= age <= PLAN_LIFETIME:
        raise RetentionError("Reviewed plan expired or is from the future; generate a fresh plan")
    fresh = fresh_plan(api, repository, ref, now)
    for field in (
        "repository",
        "ref",
        "minimum_age_hours",
        "last_access_grace_hours",
        "current_keys",
    ):
        if reviewed.get(field) != fresh[field]:
            raise RetentionError(f"Reviewed plan {field} changed")
    rows = reviewed.get("candidates")
    if not isinstance(rows, list):
        raise RetentionError("Reviewed candidates must be a list")
    selected = tuple(parse_entry(row) for row in rows)
    if len({row.id for row in selected}) != len(selected):
        raise RetentionError("Duplicate reviewed candidate IDs")
    if any(row.record() not in fresh["candidates"] for row in selected):
        raise RetentionError(
            "Reviewed candidate metadata or eligibility changed; generate a fresh plan"
        )
    deleted = []
    for row in selected:
        # Re-read before each mutation: another run/access/generation may have appeared.
        latest = fresh_plan(api, repository, ref, datetime.now(timezone.utc))
        if (
            row.record() not in latest["candidates"]
            or latest["current_keys"] != fresh["current_keys"]
        ):
            raise RetentionError("Candidate became protected; stop and generate a fresh plan")
        api.request(f"repos/{repository}/actions/caches/{row.id}", "DELETE")
        deleted.append(row.id)
        print(f"Deleted reviewed cache ID {row.id}", file=sys.stderr)
    return deleted


def main(argv: list[str] | None = None) -> int:
    """Default to a dry-run report; --apply-plan is the sole mutation entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="OWNER/REPO matching this checkout")
    parser.add_argument("--ref", default="refs/heads/beta", help="Exact checked-out branch ref")
    parser.add_argument("--output", type=Path, help="Write the dry-run JSON report here")
    parser.add_argument(
        "--apply-plan", type=Path, help="Apply only eligible IDs from this reviewed report"
    )
    args = parser.parse_args(argv)
    try:
        api, now = GitHubApi(), datetime.now(timezone.utc)
        if args.apply_plan:
            if args.output:
                raise RetentionError("--output cannot be combined with --apply-plan")
            reviewed = json.loads(args.apply_plan.read_text(encoding="utf-8"))
            result = {"deleted_ids": apply_plan(api, reviewed, args.repository, args.ref, now)}
        else:
            result = fresh_plan(api, args.repository, args.ref, now)
        content = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8", newline="\n")
        print(content, end="")
    except (
        RetentionError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        packages.PreparationError,
    ) as exc:
        print(f"Cache retention failed closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
