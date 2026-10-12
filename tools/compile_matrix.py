"""Emit every validated Unity compile target, starting newer Editor versions first."""

import argparse
import json
from pathlib import Path
import re
import sys

import unity_ci


def version_order(row: unity_ci.Version) -> tuple[int, ...]:
    # Manifest validation guarantees numeric components and a recognized release stage.
    return tuple(
        int(part) if part.isdigit() else "abfp".index(part)
        for part in re.findall(r"\d+|[abfp]", row.id)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("unity-versions.json")
    )
    args = parser.parse_args(argv)
    try:
        manifest = unity_ci.load_manifest(args.manifest)
        rows = sorted(manifest.versions, key=version_order, reverse=True)
        print(json.dumps([{"version": row.id, "channel": row.channel} for row in rows]))
    except (OSError, ValueError) as exc:
        print(f"Unity compile matrix failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
