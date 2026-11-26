"""Capture or verify only the Windows Unity MCP tool enablement booleans."""

import argparse
import ctypes
import hashlib
import json
import re
from ctypes import wintypes
from pathlib import Path


def snapshot():
    try:
        import winreg
    except ImportError as error:
        raise OSError(
            "Capturing Unity Editor tool preferences requires the Windows registry"
        ) from error

    values = {}
    physical = {}
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, r"Software\Unity Technologies\Unity Editor 5.x"
    ) as key:
        # Enumerate only names. Read data only after matching the tool-enable prefix.
        index = 0
        while True:
            size = wintypes.DWORD(16384)
            name = ctypes.create_unicode_buffer(size.value)
            status = ctypes.windll.advapi32.RegEnumValueW(
                wintypes.HKEY(int(key)), index, name, ctypes.byref(size), None, None, None, None
            )
            if status == 259:
                break
            if status:
                raise OSError(status)
            index += 1
            if not name.value.startswith("MCPForUnity.ToolEnabled."):
                continue
            match = re.fullmatch(r"(MCPForUnity\.ToolEnabled\..+)_h[0-9]+", name.value)
            value, kind = winreg.QueryValueEx(key, name.value)
            if not match or kind != winreg.REG_DWORD or value not in (0, 1) or match[1] in values:
                raise ValueError("Unexpected native tool preference encoding")
            values[match[1]] = bool(value)
            physical[name.value] = value
    return {
        "values": values,
        "sha256": hashlib.sha256(
            json.dumps(physical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    current = snapshot()
    if args.action == "capture":
        with args.path.open("x", encoding="utf-8") as stream:
            json.dump(current, stream, indent=2, sort_keys=True)
    elif json.loads(args.path.read_bytes()) != current:
        raise SystemExit("Shared native tool preferences changed")
    print(f"{len(current['values'])} tool preferences, sha256={current['sha256']}")


if __name__ == "__main__":
    main()
