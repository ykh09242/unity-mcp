"""Download official profile artifacts and write a review proposal, never update accepted pins."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

if __package__:
    from tools import unity_ci_packages as packages
else:
    import unity_ci_packages as packages


def propose(profiles: Path, output: Path) -> None:
    """Verify publisher SHA1 and hash actual tarball bytes for every registry identity."""
    if output.resolve() == packages.REGISTRY_PINS.resolve():
        raise packages.PreparationError("Write a proposal elsewhere, then review the diff")
    if output.exists():
        raise FileExistsError(f"Proposal already exists: {output}")
    config = packages.read_json(profiles)
    identities = sorted(
        {
            (name, spec["version"])
            for section in ("profiles", "optionalProfiles")
            for profile in config.get(section, {}).values()
            for name, spec in profile["packages"].items()
            if spec["source"] == "registry"
        }
    )
    if not identities:
        raise packages.PreparationError("No registry packages selected for review")

    def download(identity: tuple[str, str]) -> tuple[str, str]:
        name, version = identity
        content = packages.download_registry_archive(name, version)
        digest = hashlib.sha256(content).hexdigest()
        print(f"{name}@{version} {len(content)} bytes sha256:{digest}", file=sys.stderr)
        return f"{name}@{version}", digest

    with ThreadPoolExecutor(max_workers=3) as workers:
        artifacts = dict(workers.map(download, identities))
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"schemaVersion": 1, "artifacts": artifacts}, stream, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=packages.PROFILES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        propose(args.profiles, args.output)
    except (OSError, ValueError, KeyError, packages.PreparationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
