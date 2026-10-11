"""Freeze required native case sets and bounded execution/retention policies."""

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Final, Mapping, TypeAlias, TypedDict

from player_e2e_artifacts import digest, read_json

PROFILE_NAMES: Final = ("quick", "standard", "long")
MANIFEST: Final = Path(__file__).with_name("unity-player-e2e.json")
PROFILES: Final = Path(__file__).with_name("unity-player-e2e-profiles.json")
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ProfileError(ValueError):
    """Selected or persisted verification scope is not a maintained profile."""


class ProfileEvidence(TypedDict):
    schema_version: int
    name: str
    required_cases: list[str]
    max_execution_seconds: int
    artifact_retention_days: int
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    name: str
    cases: tuple[str, ...]
    max_execution_seconds: int
    artifact_retention_days: int
    manifest_sha256: str

    def evidence(self) -> ProfileEvidence:
        """Return a fresh immutable-scope description suitable for JSON persistence."""
        return {
            "schema_version": 1,
            "name": self.name,
            "required_cases": list(self.cases),
            "max_execution_seconds": self.max_execution_seconds,
            "artifact_retention_days": self.artifact_retention_days,
            "manifest_sha256": self.manifest_sha256,
        }


def get_profile(name: str) -> PlayerProfile:
    """Resolve a repository-maintained profile, rejecting incomplete configuration."""
    if name not in PROFILE_NAMES:
        raise ProfileError("Unknown Player verification profile")
    raw = read_json(PROFILES, 16384)
    if (
        type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
        or set(raw.get("profiles", {})) != set(PROFILE_NAMES)
    ):
        raise ProfileError("Unsupported Player profile configuration")
    catalog = read_json(MANIFEST, 16384)["cases"]
    all_cases: set[str] = set()
    parsed: dict[str, PlayerProfile] = {}
    manifest_hash = digest(MANIFEST)
    for profile_name in PROFILE_NAMES:
        value = raw["profiles"][profile_name]
        if set(value) != {"cases", "max_execution_seconds", "artifact_retention_days"}:
            raise ProfileError("Incomplete Player profile limits")
        cases = value["cases"]
        seconds = value["max_execution_seconds"]
        days = value["artifact_retention_days"]
        if (
            not isinstance(cases, list)
            or not 1 <= len(cases) <= 64
            or not all(isinstance(case, str) and case in catalog for case in cases)
            or len(set(cases)) != len(cases)
        ):
            raise ProfileError("Player profile has missing, duplicate or unknown cases")
        if type(seconds) is not int or not 60 <= seconds <= 7200:
            raise ProfileError("Player profile execution bound is invalid")
        if type(days) is not int or not 1 <= days <= 90:
            raise ProfileError("Player profile artifact retention is invalid")
        all_cases.update(cases)
        parsed[profile_name] = PlayerProfile(
            profile_name, tuple(cases), seconds, days, manifest_hash
        )
    if all_cases != set(catalog):
        raise ProfileError("Maintained cases are omitted from every Player profile")
    if not set(parsed["quick"].cases) < set(parsed["standard"].cases) < set(parsed["long"].cases):
        raise ProfileError("Player profiles must grow from quick to standard to long")
    return parsed[name]


def require_profile(record: Mapping[str, JsonValue], expected: PlayerProfile) -> None:
    """Compare canonical JSON too so Boolean/float substitutions cannot match integers."""
    if json.dumps(record.get("profile"), sort_keys=True) != json.dumps(
        expected.evidence(), sort_keys=True
    ):
        raise ProfileError("Recorded profile differs from the independently required profile")


@dataclass(frozen=True, slots=True)
class ExecutionWindow:
    started_unix_ms: int
    finished_unix_ms: int


def validate_execution_profile(
    root: Path, session: Mapping[str, JsonValue], profile: PlayerProfile
) -> ExecutionWindow:
    """Reject changed scope, incomplete outcomes or an exceeded profile deadline."""
    start = read_json(root / "session-start.json", 16384)
    results = read_json(root / "results.json", 65536)
    for record in (session, start, results):
        require_profile(record, profile)
        if record.get("session_id") != session.get("session_id"):
            raise ProfileError("Profile evidence session identity differs")
    if start.get("started_unix_ms") != session.get("started_unix_ms"):
        raise ProfileError("Profile initialization timestamp differs")
    begun, finished = (
        results.get("execution_started_unix_ms"),
        results.get("execution_finished_unix_ms"),
    )
    elapsed = results.get("execution_elapsed_seconds")
    if (
        type(start.get("started_unix_ms")) is not int
        or type(begun) is not int
        or type(finished) is not int
        or not 0 < start["started_unix_ms"] <= begun <= finished
        or type(elapsed) not in (int, float)
        or not math.isfinite(elapsed)
        or not 0 <= elapsed <= profile.max_execution_seconds
        or results.get("failures") != []
    ):
        raise ProfileError("Profile execution is incomplete, failed or outside its deadline")
    return ExecutionWindow(begun, finished)


def require_case_names(directory: Path, profile: PlayerProfile) -> None:
    """Require the exact planned bodies; a partial directory can never prove completion."""
    discovered: set[str] = set()
    for path in directory.iterdir():
        discovered.add(path.name)
        if len(discovered) > len(profile.cases):
            raise ProfileError("Player case count exceeds the required profile")
    if discovered != set(profile.cases):
        raise ProfileError("Missing or unknown required Player case")


def main() -> int:
    """Expose the same bounded policy to CI without launching Unity or a Player."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILE_NAMES)
    args = parser.parse_args()
    print(json.dumps(get_profile(args.profile).evidence()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
