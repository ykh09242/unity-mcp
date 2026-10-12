"""Run the checksum-pinned actionlint used by local and CI workflow validation."""

import argparse
import hashlib
import io
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.7.12"
# Official actionlint_1.7.12_checksums.txt, committed with the version pin.
CHECKSUMS = {
    "darwin_amd64": "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644",
    "darwin_arm64": "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f",
    "linux_amd64": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    "linux_arm64": "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6",
    "windows_amd64": "6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9",
    "windows_arm64": "cadcf7ea4efe3a68728893813643cebe1185e5b1d4be5b96245f65c9a4d5ea41",
}
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024


def distribution() -> tuple[str, str]:
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    key = f"{platform.system().lower()}_{arch.get(machine, machine)}"
    if key not in CHECKSUMS:
        raise ValueError(f"No pinned actionlint distribution for {key}")
    extension = "zip" if key.startswith("windows_") else "tar.gz"
    return key, f"actionlint_{VERSION}_{key}.{extension}"


def verified_archive(cache: Path, key: str, filename: str, download: bool) -> bytes:
    path = cache / filename
    if path.is_file():
        content = path.read_bytes()
    elif download:
        url = f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/{filename}"
        with urlopen(url, timeout=60) as response:
            content = response.read(MAX_ARCHIVE_BYTES + 1)
    else:
        raise ValueError("Pinned actionlint is not cached; run with --download once")
    if len(content) > MAX_ARCHIVE_BYTES or hashlib.sha256(content).hexdigest() != CHECKSUMS[key]:
        raise ValueError(f"Actionlint archive checksum mismatch: {path}")
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return content


def executable_bytes(content: bytes, windows: bool) -> bytes:
    name = "actionlint.exe" if windows else "actionlint"
    if windows:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return archive.read(name)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        member = archive.getmember(name)
        if not member.isfile():
            raise ValueError("Actionlint executable must be a regular archive member")
        with archive.extractfile(member) as stream:
            return stream.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="Download the pinned archive if absent"
    )
    parser.add_argument(
        "files", nargs="*", help="Workflow paths; defaults to every repository workflow"
    )
    args = parser.parse_args(argv)
    cache = ROOT / ".tmp" / "actionlint" / VERSION
    try:
        key, filename = distribution()
        content = verified_archive(cache, key, filename, args.download)
        windows = key.startswith("windows_")
        # Recreate the executable from the verified archive, never trust a cached binary.
        with tempfile.TemporaryDirectory(prefix="run-", dir=cache) as temporary:
            binary = Path(temporary) / ("actionlint.exe" if windows else "actionlint")
            binary.write_bytes(executable_bytes(content, windows))
            binary.chmod(0o755)
            paths = args.files or sorted(
                str(path)
                for suffix in ("*.yml", "*.yaml")
                for path in (ROOT / ".github/workflows").glob(suffix)
            )
            if not paths:
                raise ValueError("No workflow files selected")
            # Keep the workflow gate deterministic on hosts without optional script linters.
            return subprocess.run(
                [str(binary), "-shellcheck=", "-pyflakes=", *paths], cwd=ROOT, check=False
            ).returncode
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Workflow validation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
