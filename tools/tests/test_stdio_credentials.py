"""Actual production libc writer -> Python reader; synthetic private paths only."""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tools/tests/fixtures/stdio_credentials"
PRODUCTION = (
    ROOT / "MCPForUnity/Editor/Security/SecureKeyStore/ISecureKeyStore.cs",
    ROOT / "MCPForUnity/Editor/Security/SecureKeyStore/SecureKeyStoreConstants.cs",
    ROOT / "MCPForUnity/Editor/Security/SecureKeyStore/WindowsCredentialKeyStore.cs",
    ROOT / "MCPForUnity/Editor/Services/Transport/Transports/StdioLaunchCredential.cs",
)
READER = ROOT / "Server/src/transport/legacy/stdio_credentials.py"


def compile_writer(work: Path) -> tuple[str, Path, dict]:
    """Use only installed SDK/reference packs; no restore or network access."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Installed dotnet SDK unavailable")
    env = dict(
        os.environ,
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
        DOTNET_CLI_HOME=str(work / "dotnet-home"),
    )
    inventory = subprocess.run(
        [dotnet, "--list-sdks"], capture_output=True, text=True, check=True, env=env, timeout=30
    )
    choices = []
    for line in inventory.stdout.splitlines():
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \[(.+)\]", line)
        if match and int(match[1]) >= 8:
            choices.append((tuple(map(int, match.group(1, 2, 3))), Path(match[4])))
    if not choices:
        pytest.skip("Installed .NET 8+ SDK unavailable")
    version, sdk_parent = max(choices)
    major = version[0]
    sdk = sdk_parent / ".".join(map(str, version))
    packs = sdk_parent.parent / "packs/Microsoft.NETCore.App.Ref"
    installed = sorted(
        (path for path in packs.glob(f"{major}.*") if path.is_dir()),
        key=lambda path: tuple(map(int, path.name.split("."))),
    )
    if not installed:
        pytest.skip("Installed matching .NET reference pack unavailable")
    references = sorted((installed[-1] / "ref" / f"net{major}.0").glob("*.dll"))
    if not references:
        pytest.skip("Installed matching .NET reference assemblies unavailable")
    assembly = work / "PosixCredentialRoundTrip.dll"
    platform_define = "UNITY_EDITOR_OSX" if sys.platform == "darwin" else "UNITY_EDITOR_LINUX"
    sources = (*PRODUCTION, FIXTURE / "PosixCredentialRoundTripHarness.cs")
    response = work / "compile.rsp"
    response.write_text(
        "\n".join(
            [
                "/nologo",
                "/nostdlib+",
                "/target:exe",
                "/langversion:latest",
                f"/define:{platform_define}",
                f'/out:"{assembly}"',
                *(f'/reference:"{path}"' for path in references),
                *(f'"{path}"' for path in sources),
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [dotnet, str(sdk / "Roslyn/bincore/csc.dll"), "/noconfig", f"@{response}"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assembly.with_suffix(".runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": f"net{major}.0",
                    "framework": {"name": "Microsoft.NETCore.App", "version": f"{major}.0.0"},
                }
            }
        ),
        encoding="utf-8",
    )
    return dotnet, assembly, env


@pytest.fixture(scope="module")
def writer(tmp_path_factory):
    if os.name != "posix":
        pytest.skip("Actual libc integration requires POSIX")
    work = tmp_path_factory.mktemp("owned-stdio-compile")
    tracked = (
        *PRODUCTION,
        READER,
        FIXTURE / "PosixCredentialRoundTripHarness.cs",
        FIXTURE / "check_owned_posix_credential.py",
    )
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
    yield compile_writer(work)
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}


def start_writer(writer, home: Path, generation: str, token: str):
    dotnet, assembly, env = writer
    process = subprocess.Popen(
        [dotnet, str(assembly), str(home)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process.stdin.write(generation + "\n" + token + "\n")
    process.stdin.flush()
    return process


def finish_writer(process):
    try:
        if process.poll() is None:
            process.stdin.write("dispose\n")
            process.stdin.flush()
        output, error = process.communicate(timeout=15)
        return output.strip(), error, process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)


def ready_line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=15), "Owned writer did not report readiness within deadline"
        return process.stdout.readline().strip()


def test_actual_posix_writer_reader_private_modes_and_exact_cleanup(writer, tmp_path):
    home = tmp_path / "owned-home"
    home.mkdir(mode=0o700)
    generation, token = secrets.token_hex(16), secrets.token_hex(32)
    directory = home / ".unity-mcp/stdio-auth" / generation
    retained = directory.parent / secrets.token_hex(16)
    retained.mkdir(parents=True, mode=0o700)
    process = start_writer(writer, home, generation, token)
    try:
        assert ready_line(process) == "READY"
        reader = subprocess.run(
            [sys.executable, "-B", str(FIXTURE / "check_owned_posix_credential.py"), str(READER)],
            input=generation + "\n" + token + "\n",
            env=dict(os.environ, HOME=str(home)),
            capture_output=True,
            text=True,
            timeout=15,
        )
        token = None
        assert reader.returncode == 0, (
            "Owned production reader failed (credential content suppressed)"
        )
        result = json.loads(reader.stdout)
        assert result == {
            "matched": True,
            "private_modes": True,
            "owners": True,
            "malformed_rejected": True,
            "malformed_count": 8,
            "unsafe_file_mode_rejected": True,
        }
    finally:
        token = None
        output, error, code = finish_writer(process)
        assert code == 0 and output == "DISPOSED" and not error
        assert not directory.exists(), "Production Dispose did not remove the exact owned launch"
    assert (home / ".unity-mcp/stdio-auth").is_dir()
    assert retained.is_dir(), "Production cleanup removed another owned launch directory"


@pytest.mark.parametrize("ancestor", [".unity-mcp", "stdio-auth"])
def test_actual_posix_writer_refuses_ancestor_symlink(writer, tmp_path, ancestor):
    home = tmp_path / "owned-home"
    home.mkdir(mode=0o700)
    destination = tmp_path / "owned-link-destination"
    destination.mkdir(mode=0o700)
    if ancestor == "stdio-auth":
        (home / ".unity-mcp").mkdir(mode=0o700)
        link = home / ".unity-mcp/stdio-auth"
    else:
        link = home / ".unity-mcp"
    link.symlink_to(destination, target_is_directory=True)
    generation, token = secrets.token_hex(16), secrets.token_hex(32)
    process = start_writer(writer, home, generation, token)
    token = None
    try:
        output, error = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)
    assert process.returncode == 2 and output.strip() == "REJECTED" and not error
    assert list(destination.iterdir()) == [], (
        "Rejected writer changed the owned symlink destination"
    )
    assert link.is_symlink()


@pytest.mark.parametrize(
    "generation",
    ["", "a" * 31, "a" * 33, "A" * 32, "G" * 32, "../" + "a" * 32, "MCPForUnity.Stdio:" + "a" * 32],
)
def test_actual_posix_writer_rejects_malformed_generation_before_path_creation(
    writer, tmp_path, generation
):
    home = tmp_path / "owned-home"
    home.mkdir(mode=0o700)
    process = start_writer(writer, home, generation, secrets.token_hex(32))
    try:
        output, error = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)
    assert process.returncode == 3 and output.strip() == "INVALID" and not error
    assert list(home.iterdir()) == []
