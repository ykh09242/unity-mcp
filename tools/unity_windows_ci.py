"""Pinned Windows Unity installation and private serial activation for hosted CI."""

import argparse
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from player_e2e_artifacts import read_json, write_json
from player_e2e_process import Invocation, execute

MANIFEST = Path(__file__).with_name("unity-player-e2e.json")


def provision(root: Path) -> None:
    """Download only the pinned official installer, enforcing its exact byte/hash bounds."""
    manifest = read_json(MANIFEST)
    root.mkdir(parents=True, exist_ok=False)
    if " " in str(root.resolve()):
        raise ValueError("Silent Unity installation requires a path without spaces")
    installer = root / "UnitySetup.exe"
    expected_size = manifest["installer_size_bytes"]
    checksum = hashlib.md5(usedforsecurity=False)
    size = 0
    deadline = time.monotonic() + 1800
    with urllib.request.urlopen(manifest["installer_url"], timeout=60) as response:
        with installer.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                if time.monotonic() >= deadline:
                    raise ValueError("Unity installer download exceeded its deadline")
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("Unity installer exceeds official release size")
                checksum.update(chunk)
                stream.write(chunk)
    expected_digest = base64.b64decode(manifest["installer_integrity"].removeprefix("md5-"))
    if size != expected_size or checksum.digest() != expected_digest:
        raise ValueError("Unity installer differs from official release integrity")
    # Integrity is supplied by Unity's official release API; Windows signature adds publisher proof.
    installer_literal = str(installer).replace("'", "''")
    script = f"$s=Get-AuthenticodeSignature -LiteralPath '{installer_literal}'; " + (
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Unity Technologies') { exit 1 }"
    )
    signature = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        timeout=60,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if signature.returncode:
        raise ValueError("Unity installer does not have a valid Unity publisher signature")
    result = execute(
        Invocation(
            [str(installer), "/S", "/D=" + str(root / "installed")],
            root / "install-evidence",
            timeout=1800,
        )
    )
    if result["actual_exit_code"] or result["forced_termination"]:
        raise ValueError("Unity silent installation failed")
    if not (root / "installed" / manifest["mono_module"]).is_dir():
        raise ValueError("Pinned Unity installation lacks Windows Mono build support")
    write_json(
        root / "provision.json",
        {
            "unity_version": manifest["unity_version"],
            "installer_url": manifest["installer_url"],
            "installer_size_bytes": size,
            "installer_integrity": manifest["installer_integrity"],
            "authenticode_verified": True,
            "windows_mono_verified": True,
        },
    )


def license_command(editor: Path, action: str) -> None:
    """Credentials exist only in this activation child; discard its private output."""
    flags = {
        "activate": [
            "-serial",
            os.environ.get("UNITY_SERIAL", ""),
            "-username",
            os.environ.get("UNITY_EMAIL", ""),
            "-password",
            os.environ.get("UNITY_PASSWORD", ""),
        ],
        "return": ["-returnlicense"],
    }
    if action == "activate" and any(
        not os.environ.get(key) for key in ("UNITY_SERIAL", "UNITY_EMAIL", "UNITY_PASSWORD")
    ):
        raise ValueError("Windows serial activation prerequisites are missing")
    command = [str(editor), "-batchmode", "-nographics", "-quit", "-logFile", "-"] + flags[action]
    try:
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Private Unity license operation exceeded its deadline") from exc
    if result.returncode:
        raise ValueError("Private Unity license operation failed; credentials/output are withheld")


def main() -> int:
    """Separate provision, activation and return steps keep credentials out of build execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("provision", "activate", "return"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "provision":
            provision(args.path)
        else:
            license_command(args.path, args.action)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
