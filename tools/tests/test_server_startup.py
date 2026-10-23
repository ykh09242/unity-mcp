"""The installed-server startup probe must follow the default focused catalog."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("aliased_temp", [False, True])
def test_installed_server_startup_probe_accepts_focused_defaults(tmp_path, aliased_temp):
    temporary = tmp_path
    if aliased_temp:
        target = tmp_path / "private-temp"
        target.mkdir(mode=0o700)
        temporary = tmp_path / "temp-alias"
        try:
            temporary.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name == "nt":
                pytest.skip("Creating a Windows directory symlink requires developer mode")
            raise
    probe = Path(__file__).resolve().parents[1] / "check_server_startup.py"
    result = subprocess.run(
        [sys.executable, "-I", str(probe)],
        cwd=tmp_path,
        env={**os.environ, "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads(result.stdout.splitlines()[-1])
    assert all(inventory[kind] > 0 for kind in ("tools", "resources", "templates"))
    assert inventory["runtime"]["sdk"]["supported"] is True
    features = inventory["runtime"]["features"]
    assert all(
        features[name]["state"] == "active"
        for name in ("execution_receipts", "wait_tasks", "skills")
    )
    assert features["skills"]["installed"] is True
    assert features["scene_load_inputs"]["state"] in {"disabled", "unavailable"}
    assert features["scene_load_inputs"]["installed"] is False
