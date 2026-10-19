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


def verified_installer(installer: Path, url: str, expected_size: int, integrity: str) -> int:
    """Require exact official release bytes and a valid Unity publisher signature before execution."""
    checksum = hashlib.md5(usedforsecurity=False)
    size = 0
    deadline = time.monotonic() + 1800
    with urllib.request.urlopen(url, timeout=60) as response:
        with installer.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                if time.monotonic() >= deadline:
                    raise ValueError("Unity installer download exceeded its deadline")
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("Unity installer exceeds official release size")
                checksum.update(chunk)
                stream.write(chunk)
    expected_digest = base64.b64decode(integrity.removeprefix("md5-"))
    if size != expected_size or checksum.digest() != expected_digest:
        raise ValueError("Unity installer differs from official release integrity")
    verify_installer_signature(installer)
    return size


def verify_installer_signature(installer: Path) -> None:
    """Load Windows PowerShell's own security module, without inheriting a pwsh module override."""
    # Integrity is supplied by Unity's official release API; Windows signature adds publisher proof.
    installer_literal = str(installer).replace("'", "''")
    script = (
        f"$ErrorActionPreference='Stop'; $s=Get-AuthenticodeSignature -LiteralPath '{installer_literal}'; "
        + (
            "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Unity Technologies') { exit 1 }"
        )
    )
    signature = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        timeout=60,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Windows PowerShell reconstructs its native module paths when this override is absent.
        # Keep every other variable and the parent process environment unchanged.
        env={key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
    )
    if signature.returncode:
        raise ValueError("Unity installer does not have a valid Unity publisher signature")


def provision(root: Path, backend: str = "mono") -> None:
    """Install the pinned Editor; the optional IL2CPP module requires explicit selection."""
    if backend not in ("mono", "il2cpp"):
        raise ValueError("Windows backend must be mono or il2cpp")
    manifest = read_json(MANIFEST)
    root.mkdir(parents=True, exist_ok=False)
    if " " in str(root.resolve()):
        raise ValueError("Silent Unity installation requires a path without spaces")
    installer = root / "UnitySetup.exe"
    size = verified_installer(
        installer,
        manifest["installer_url"],
        manifest["installer_size_bytes"],
        manifest["installer_integrity"],
    )
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
    if backend == "il2cpp":
        provision_il2cpp(root)


def provision_il2cpp(root: Path) -> None:
    """Add only the pinned module to the owned pinned CI Editor installation."""
    module = read_json(Path(__file__).with_name("unity-windows-il2cpp.json"))
    editor = read_json(root / "provision.json")
    if editor.get("unity_version") != module["unity_version"] or not editor.get(
        "authenticode_verified"
    ):
        raise ValueError("IL2CPP module requires the verified matching owned Editor installation")
    installer = root / "UnityWindowsIL2CPP.exe"
    size = verified_installer(
        installer, module["url"], module["download_size_bytes"], module["integrity"]
    )
    result = execute(
        Invocation(
            [str(installer), "/S", "/D=" + str(root / "installed")],
            root / "module-install-evidence",
            timeout=900,
        )
    )
    if (
        result["actual_exit_code"]
        or result["forced_termination"]
        or not (root / "installed" / module["module_directory"]).is_dir()
    ):
        raise ValueError("Pinned Windows IL2CPP module installation did not complete")
    write_json(
        root / "module-provision.json",
        {
            "unity_version": module["unity_version"],
            "module_id": module["module_id"],
            "url": module["url"],
            "download_size_bytes": size,
            "integrity": module["integrity"],
            "authenticode_verified": True,
            "windows_il2cpp_verified": True,
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
    parser.add_argument("--backend", choices=("mono", "il2cpp"), default="mono")
    args = parser.parse_args()
    try:
        if args.action == "provision":
            provision(args.path, args.backend)
        else:
            license_command(args.path, args.action)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
