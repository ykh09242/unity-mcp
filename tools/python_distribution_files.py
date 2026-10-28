"""Select built wheel/sdist files matching a project's declared distribution."""

import argparse
from pathlib import Path
import sys
import tomllib

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version


def distribution_files(project_file: Path, directory: Path) -> list[Path]:
    """Compare parsed names and versions, never a version-prefix glob."""
    with project_file.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    expected_name = canonicalize_name(project["name"])
    expected_version = Version(project["version"])
    selected = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        try:
            if path.name.endswith(".whl"):
                name, version, _, _ = parse_wheel_filename(path.name)
            elif path.name.endswith(".tar.gz"):
                name, version = parse_sdist_filename(path.name)
            else:
                continue
        except (InvalidSdistFilename, InvalidWheelFilename):
            continue
        if name == expected_name and version == expected_version:
            selected.append(path)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_file", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    files = distribution_files(args.project_file, args.directory)
    if not files:
        print(
            f"No distribution files match {args.project_file} in {args.directory}.", file=sys.stderr
        )
        return 2
    for path in files:
        print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
