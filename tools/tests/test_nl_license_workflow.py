"""Execute manual-suite license staging with synthetic payloads, never activation."""

import base64
import os
from pathlib import Path
import shutil
import subprocess
from typing import Final

import pytest
import yaml


WORKFLOW: Final = Path(__file__).resolve().parents[2] / ".github/workflows/claude-nl-suite.yml"


def stage_license(tmp_path: Path, value: str) -> subprocess.CompletedProcess[str]:
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    script = next(step["run"] for step in config["jobs"]["nl-suite"]["steps"] if step.get("id") == "ulf")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if name in os.environ}
    environment.update(
        RUNNER_TEMP=(tmp_path / "runner temp").as_posix(),
        GITHUB_OUTPUT=(tmp_path / "outputs").as_posix(),
        UNITY_LICENSE=value,
    )
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture-payload" not in result.stdout + result.stderr
    return result


@pytest.mark.parametrize("tag", ["Entitlement", "entitlement", "ENTITLEMENT"])
@pytest.mark.parametrize("encoded", [False, True])
def test_entitlement_input_is_rehomed(tmp_path: Path, tag: str, encoded: bool) -> None:
    payload = f"<License><{tag}>fixture-payload</{tag}></License>"
    value = base64.b64encode(payload.encode()).decode() if encoded else payload
    result = stage_license(tmp_path, value)
    root = tmp_path / "runner temp"
    destination = root / "unity-config/Unity/licenses/UnityEntitlementLicense.xml"
    assert destination.is_file(), result.stdout
    assert destination.read_text(encoding="utf-8") == payload
    assert not (root / "unity-license-ulf/Unity_lic.ulf").exists()
    assert not (root / "unity-local/Unity/Unity_lic.ulf").exists()
    assert (tmp_path / "outputs").read_text(encoding="utf-8") == "ok=false\n"


@pytest.mark.parametrize("encoded", [False, True])
def test_signed_ulf_keeps_precedence_over_entitlement_marker(tmp_path: Path, encoded: bool) -> None:
    payload = "<License><Signature>fixture-payload</Signature><Entitlement/></License>"
    value = base64.b64encode(payload.encode()).decode() if encoded else payload
    stage_license(tmp_path, value)
    root = tmp_path / "runner temp"
    for path in ("unity-license-ulf/Unity_lic.ulf", "unity-local/Unity/Unity_lic.ulf"):
        assert (root / path).read_text(encoding="utf-8") == payload
    assert not (root / "unity-config/Unity/licenses/UnityEntitlementLicense.xml").exists()
    assert (tmp_path / "outputs").read_text(encoding="utf-8") == "ok=true\n"


@pytest.mark.parametrize("encoded", [False, True])
def test_unknown_input_is_not_marked_as_a_license(tmp_path: Path, encoded: bool) -> None:
    payload = "<Unknown>fixture-payload</Unknown>"
    value = base64.b64encode(payload.encode()).decode() if encoded else payload
    stage_license(tmp_path, value)
    root = tmp_path / "runner temp"
    assert (root / "unity-license-ulf/Unity_lic.ulf").read_text(encoding="utf-8") == payload
    assert not (root / "unity-local/Unity/Unity_lic.ulf").exists()
    assert not (root / "unity-config/Unity/licenses/UnityEntitlementLicense.xml").exists()
    assert (tmp_path / "outputs").read_text(encoding="utf-8") == "ok=false\n"
