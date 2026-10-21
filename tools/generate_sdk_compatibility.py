"""Generate the reviewed adapter dependency set from the committed lock.

Run after an explicit SDK upgrade and protocol review. Default/check mode never writes.
"""

import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "Server/src/core/sdk_compatibility.json"
PACKAGES = ("fastmcp", "mcp", "mcp-types")


def render(root: Path) -> str:
    lock = tomllib.loads((root / "Server/uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "Server/pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    packages = {}
    for name in PACKAGES:
        versions = [p["version"] for p in lock["package"] if p["name"] == name]
        if len(versions) != 1 or not re.fullmatch(r"[0-9][0-9A-Za-z+_.-]{0,79}", versions[0]):
            raise ValueError(f"Expected one locked SDK version for {name}")
        expected = versions[0]
        if f"{name}=={expected}" not in dependencies:
            raise ValueError(
                f"SDK exact dependency must match the reviewed lock: {name}=={expected}"
            )
        packages[name] = expected
    return json.dumps({"schema_version": 1, "packages": packages}, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write", action="store_true", help="Regenerate after reviewing the locked SDK set"
    )
    mode.add_argument("--check", action="store_true", help="Verify without writing (default)")
    args = parser.parse_args()
    try:
        expected = render(ROOT)
        if args.write:
            TARGET.write_text(expected, encoding="utf-8", newline="\n")
        elif not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != expected:
            print("SDK compatibility manifest differs; review the locked upgrade, then regenerate.")
            return 1
    except (ValueError, OSError) as error:
        print(str(error))
        return 1
    print("Reviewed SDK dependencies and compatibility manifest agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
