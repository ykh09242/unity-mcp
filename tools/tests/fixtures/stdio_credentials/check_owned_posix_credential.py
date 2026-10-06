"""Actual POSIX reader with a private child HOME and owned stdin expectations."""
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


def main() -> int:
    generation = sys.stdin.readline().rstrip("\r\n")
    expected = sys.stdin.readline().rstrip("\r\n")
    spec = importlib.util.spec_from_file_location("owned_posix_reader", sys.argv[1])
    production = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(production)
    home = Path.home()
    directory = home / ".unity-mcp" / "stdio-auth" / generation
    token = directory / "token"
    modes = (stat.S_IMODE(directory.stat().st_mode) == 0o700
             and stat.S_IMODE(token.stat().st_mode) == 0o600)
    owners = directory.stat().st_uid == os.getuid() == token.stat().st_uid
    actual = production.read_stdio_token(generation)
    matched = isinstance(actual, str) and hmac.compare_digest(actual, expected)
    actual = expected = None
    os.chmod(token, 0o604)
    try:
        unsafe_file_mode_rejected = production.read_stdio_token(generation) is None
    finally:
        os.chmod(token, 0o600)
    # If invalid IDs pass the production guard, fail before any filesystem access.
    native_open = production.os.open
    calls = 0

    def forbidden_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("Malformed generation reached filesystem")

    production.os.open = forbidden_open
    malformed = ("", generation[:-1], generation + "0", "A" * 32, "G" * 32,
                 "../" + generation, "MCPForUnity.Stdio:" + generation,
                 "MCPForUnity.AssetGen:" + generation)
    try:
        invalid_rejected = all(production.read_stdio_token(item) is None for item in malformed)
    finally:
        production.os.open = native_open
    result = {"matched": matched, "private_modes": modes, "owners": owners,
              "malformed_rejected": invalid_rejected and calls == 0, "malformed_count": len(malformed),
              "unsafe_file_mode_rejected": unsafe_file_mode_rejected}
    print(json.dumps(result))
    return 0 if all(result[key] for key in ("matched", "private_modes", "owners", "malformed_rejected", "unsafe_file_mode_rejected")) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("FAIL " + type(error).__name__)
        sys.exit(1)
