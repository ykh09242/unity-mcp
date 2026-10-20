"""The installed-server startup probe must follow the default focused catalog."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_installed_server_startup_probe_accepts_focused_defaults(tmp_path):
    probe = Path(__file__).resolve().parents[1] / "check_server_startup.py"
    result = subprocess.run(
        [sys.executable, "-I", str(probe)],
        cwd=tmp_path,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path), "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads(result.stdout.splitlines()[-1])
    assert all(inventory[kind] > 0 for kind in ("tools", "resources", "templates"))
