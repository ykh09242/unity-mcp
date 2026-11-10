"""Prepare the pinned NUnit reference for license-free production-code harnesses."""

import argparse
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile


NUNIT_URL = "https://api.nuget.org/v3-flatcontainer/nunit/3.14.0/nunit.3.14.0.nupkg"
# Verified against the official NuGet archive, 2026-11-10.
NUNIT_SHA256 = "0ae3ffab91e8bcf59f016dc2b72fd0c518bb5698f2909dd30cbab94c4348e8a6"
NUNIT_MEMBER = "lib/netstandard2.0/nunit.framework.dll"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024


def publish_reference(payload: bytes, destination: Path) -> None:
    """Verify the whole archive before writing only the selected assembly."""
    if len(payload) > MAX_ARCHIVE_BYTES or hashlib.sha256(payload).hexdigest() != NUNIT_SHA256:
        raise ValueError("NUnit archive differs from its reviewed size/digest")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assembly = archive.read(NUNIT_MEMBER)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace a local Unity reference supplied by a different preparation.
    with destination.open("xb") as stream:
        stream.write(assembly)


def main() -> int:
    """Download the reviewed framework into an explicitly owned output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with urllib.request.urlopen(NUNIT_URL, timeout=60) as response:
        payload = response.read(MAX_ARCHIVE_BYTES + 1)
    publish_reference(payload, args.output)
    print("Prepared verified NUnit 3.14.0 managed test reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
