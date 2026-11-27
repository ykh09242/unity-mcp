"""Compile the production heartbeat publisher and check its native filesystem contract."""

import hashlib
import ctypes
import os
import platform
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent
SOURCE = (
    FIXTURE.parents[3]
    / "MCPForUnity/Editor/Services/Transport/Transports/HeartbeatFilePublication.cs"
)
FIRST = '{"seq":1,"message":"Editor ready"}'
SECOND = '{"seq":2,"message":"Ready ✓"}'
RECOVERED = '{"seq":3,"message":"Recovered"}'


def check(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"PASS {label}: {actual!r}", flush=True)


def compile_publisher(executable):
    sdk = subprocess.check_output(["dotnet", "--list-sdks"], text=True).splitlines()[-1]
    version, directory = sdk.rstrip("]").split(" [")
    compiler = Path(directory) / version / "Roslyn/bincore/csc.dll"
    if sys.platform == "win32":
        api = (
            Path(os.environ["ProgramFiles(x86)"])
            / "Reference Assemblies/Microsoft/Framework/.NETFramework/v4.8"
        )
        define = "UNITY_EDITOR_WIN"
    else:
        lib = subprocess.check_output(
            ["pkg-config", "--variable=libdir", "mono-2"], text=True
        ).strip()
        api = Path(lib) / "mono/4.7.2-api"
        define = {"darwin": "UNITY_EDITOR_OSX", "linux": "UNITY_EDITOR_LINUX"}[sys.platform]
    subprocess.run(
        [
            "dotnet",
            compiler,
            "-nologo",
            "-nostdlib+",
            "-target:exe",
            f"-define:{define}",
            f"-out:{executable}",
            *[f"-r:{api / assembly}.dll" for assembly in ("mscorlib", "System", "System.Core")],
            SOURCE,
            FIXTURE / "Publisher.cs",
        ],
        check=True,
    )
    print(
        f"{define} Roslyn {version}, source SHA-256 {hashlib.sha256(SOURCE.read_bytes()).hexdigest()}"
    )


def publish(executable, path, payload, expected=0):
    result = subprocess.run(["mono", executable, path, payload], timeout=20, umask=0o027)
    check(f"{path.name} publication exit", result.returncode, expected)


def run_windows_fixture(executable, path, expected):
    # Children inherit this process's error mode. Restore it as soon as the fixture exits.
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetErrorMode.argtypes = []
    kernel.GetErrorMode.restype = ctypes.c_uint
    kernel.SetErrorMode.argtypes = [ctypes.c_uint]
    kernel.SetErrorMode.restype = ctypes.c_uint
    prior = kernel.GetErrorMode()
    kernel.SetErrorMode(prior | 0x0001 | 0x0002)  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
    try:
        result = subprocess.run(
            [executable, path, FIRST, SECOND, RECOVERED],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stderr=subprocess.PIPE,
            timeout=20,
        )
    finally:
        kernel.SetErrorMode(prior)
    sys.stderr.buffer.write(result.stderr)
    sys.stderr.buffer.flush()
    check("Windows fixture exit", result.returncode, expected)
    if expected:
        check("managed failure diagnostic", b"System.IO." in result.stderr, True)


def acl(path):
    if sys.platform == "linux":
        return subprocess.check_output(["getfacl", "-cpn", path], text=True).strip()
    lines = subprocess.check_output(["ls", "-le", path], text=True).splitlines()
    return "\n".join(lines[1:])  # The first line includes the size, timestamp and name.


def set_acl(path, *, inherit=False):
    if sys.platform == "linux":
        entry = ("d:" if inherit else "") + "u:nobody:r--"
        subprocess.run(["setfacl", "-m", entry, path], check=True)
    else:
        entry = "everyone allow readattr" + (",file_inherit" if inherit else "")
        subprocess.run(["chmod", "+a", entry, path], check=True)


def metadata(path):
    info = path.stat()
    return (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, acl(path))


def test_publication(executable, directory):
    path = directory / "heartbeat.json"
    publish(executable, path, FIRST)
    check("initial umask 0027", stat.S_IMODE(path.stat().st_mode), 0o640)
    check("initial complete UTF-8", path.read_bytes(), FIRST.encode())
    initial_acl = acl(path)
    set_acl(path)
    access = metadata(path)
    check("fixture installed an explicit ACL", access[3] != initial_acl, True)
    with path.open("rb") as old:
        publish(executable, path, SECOND)
        check("reader opened before replacement", old.read(), FIRST.encode())
        check("new file identity", os.fstat(old.fileno()).st_ino != path.stat().st_ino, True)
    check("replacement complete UTF-8", path.read_bytes(), SECOND.encode())
    check("replacement mode, uid, gid and ACL", metadata(path), access)

    path.chmod(0o400)
    before = path.stat()
    readonly = metadata(path)
    publish(executable, path, RECOVERED, expected=13)
    check("read-only inode identity", path.stat().st_ino, before.st_ino)
    check("read-only content", path.read_bytes(), SECOND.encode())
    check("read-only mode, uid, gid and ACL", metadata(path), readonly)
    check("no uncommitted stage", sorted(p.name for p in directory.iterdir()), [path.name])
    path.chmod(access[0])
    publish(executable, path, RECOVERED)
    check("recovery content", path.read_bytes(), RECOVERED.encode())
    check("recovery mode, uid, gid and ACL", metadata(path), access)
    check("no leftover stage", sorted(p.name for p in directory.iterdir()), [path.name])


def test_inherited_acl(executable, directory):
    set_acl(directory, inherit=True)
    # Ask the kernel how an ordinary 0666 create inherits this directory's ACL.
    reference = directory / "reference"
    previous = os.umask(0o027)
    try:
        reference.touch(mode=0o666)
    finally:
        os.umask(previous)
    expected = metadata(reference)
    entry = "user:" if sys.platform == "linux" else "everyone"
    check(
        "reference inherited named ACL",
        any(entry in line and "::" not in line for line in expected[3].splitlines()),
        True,
    )
    path = directory / "heartbeat.json"
    publish(executable, path, FIRST)
    check("first publication inherited mode, uid, gid and ACL", metadata(path), expected)
    publish(executable, path, SECOND)
    check("replacement inherited mode, uid, gid and ACL", metadata(path), expected)
    check("replacement under inherited ACL", path.read_bytes(), SECOND.encode())
    check(
        "inherited directory contents",
        sorted(p.name for p in directory.iterdir()),
        [path.name, reference.name],
    )


def main():
    if sys.platform not in ("darwin", "linux", "win32"):
        raise SystemExit("Run on native Windows, macOS or Linux.")
    print(f"{platform.platform()}, architecture {platform.machine()}", flush=True)
    if sys.platform != "win32":
        if subprocess.check_output(["id", "-u"], text=True).strip() == "0":
            raise SystemExit("Run as an unprivileged user to verify write refusal.")
        subprocess.run(["mono", "--version"], check=True)
    with tempfile.TemporaryDirectory(prefix="heartbeat-publication-") as temporary:
        root = Path(temporary)
        executable = root / "Publisher.exe"
        compile_publisher(executable)
        ordinary = root / "ordinary"
        ordinary.mkdir()
        neighbor = root / "neighbor"
        neighbor.write_bytes(b"unchanged")
        expected = ["Publisher.exe", "neighbor", "ordinary"]
        if sys.platform == "win32":
            path = ordinary / "heartbeat.json"
            run_windows_fixture(executable, neighbor / "invalid.json", expected=1)
            run_windows_fixture(executable, path, expected=0)
            check("recovery complete UTF-8", path.read_bytes(), RECOVERED.encode())
            check("no leftover stage or backup", list(ordinary.iterdir()), [path])
        else:
            test_publication(executable, ordinary)
            inherited = root / "inherited"
            inherited.mkdir()
            expected.append(inherited.name)
            test_inherited_acl(executable, inherited)
        check("neighbor content", neighbor.read_bytes(), b"unchanged")
        check(
            "scratch contents",
            sorted(p.name for p in root.iterdir()),
            sorted(expected),
        )


if __name__ == "__main__":
    main()
