"""Optional backend provisioning refuses unverified bytes and unrelated Editor versions."""

import base64
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unity_windows_ci as windows
from player_e2e_artifacts import write_json


def test_signature_child_rebuilds_native_module_path_without_changing_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("PSModulePath", "fixture-pwsh-modules")
    monkeypatch.setenv("MCP_SIGNATURE_ROUTING_FIXTURE", "preserved")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(windows.subprocess, "run", run)
    windows.verify_installer_signature(tmp_path / "module.exe")
    command, options = calls[0]
    assert command[0] == "powershell"
    assert "Get-AuthenticodeSignature" in command[-1]
    assert "'Valid'" in command[-1] and "Unity Technologies" in command[-1]
    assert not any(key.casefold() == "psmodulepath" for key in options["env"])
    assert options["env"]["MCP_SIGNATURE_ROUTING_FIXTURE"] == "preserved"
    assert os.environ["PSModulePath"] == "fixture-pwsh-modules"


def test_signature_validation_failure_still_refuses_installer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        windows.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1),
    )
    with pytest.raises(ValueError, match="valid Unity publisher signature"):
        windows.verify_installer_signature(tmp_path / "module.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows Authenticode integration")
def test_real_cached_unity_signature_loads_security_module_under_inherited_pwsh_path():
    fixture = os.environ.get("UNITY_MCP_SIGNATURE_FIXTURE")
    if not fixture or not shutil.which("powershell"):
        pytest.skip("An explicit cached Unity installer and Windows PowerShell are required")
    path = Path(fixture).resolve(strict=True)
    # Real module invocation against existing bytes: no download or installer execution.
    windows.verify_installer_signature(path)


def test_installer_integrity_failure_cannot_reach_signature_or_execution(tmp_path, monkeypatch):
    payload = b"modified-installer"
    monkeypatch.setattr(
        windows.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload)
    )
    calls = []
    monkeypatch.setattr(
        windows.subprocess, "run", lambda *_args, **_kwargs: calls.append("signature")
    )
    expected = (
        "md5-"
        + base64.b64encode(
            hashlib.md5(b"original-installer", usedforsecurity=False).digest()
        ).decode()
    )
    with pytest.raises(ValueError, match="integrity"):
        windows.verified_installer(
            tmp_path / "module.exe", "https://download.unity3d.com/fixture", len(payload), expected
        )
    assert not calls


def test_installer_overrun_is_bounded_before_signature_or_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(
        windows.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"too-large")
    )
    with pytest.raises(ValueError, match="exceeds official release size"):
        windows.verified_installer(
            tmp_path / "module.exe", "https://download.unity3d.com/fixture", 1, "md5-unused"
        )


def test_optional_module_refuses_other_editor_without_downloading(tmp_path, monkeypatch):
    write_json(
        tmp_path / "provision.json", {"unity_version": "2022.3.69f1", "authenticode_verified": True}
    )
    calls = []
    monkeypatch.setattr(windows, "verified_installer", lambda *_args: calls.append("download"))
    with pytest.raises(ValueError, match="matching owned Editor"):
        windows.provision_il2cpp(tmp_path)
    assert not calls


def test_unknown_backend_is_refused_before_creating_install_root(tmp_path):
    root = tmp_path / "editor"
    with pytest.raises(ValueError, match="backend"):
        windows.provision(root, "unknown")
    assert not root.exists()
