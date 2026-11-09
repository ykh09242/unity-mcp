"""Archive assurance runs public behavior with installed imports and no Editor."""

import os
from pathlib import Path
import subprocess
import sys
import json
from importlib.util import module_from_spec, spec_from_file_location

import pytest
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tools/check_server_archive.py"


def load_mcp_probe():
    spec = spec_from_file_location("installed_mcp_probe", ROOT / "tools/installed_mcp_probe.py")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_installed_mcp_probe_rejects_checkout_origin():
    probe = load_mcp_probe()
    with pytest.raises(RuntimeError, match="Installed archive required"):
        probe.installed_main_origin(ROOT)


def test_installed_mcp_probe_rejects_source_even_with_archive_metadata(monkeypatch):
    probe = load_mcp_probe()
    origin = ROOT / "Server/src/main.py"
    metadata = SimpleNamespace(
        files=["main.py"],
        locate_file=lambda _: origin,
        read_text=lambda _: '{"archive_info":{"hashes":{"sha256":"fixture"}}}',
    )
    monkeypatch.setattr(probe, "distribution", lambda _: metadata)
    monkeypatch.setattr(probe, "find_spec", lambda _: SimpleNamespace(origin=str(origin)))
    monkeypatch.setattr(probe.sys, "prefix", str(ROOT))
    with pytest.raises(RuntimeError, match="Installed archive required"):
        probe.installed_main_origin(ROOT)


def test_installed_mcp_result_requires_real_success_and_installed_server_identity(tmp_path):
    from mcp.types import CallToolResult, TextContent

    probe = load_mcp_probe()
    origin = tmp_path / "site-packages/main.py"
    payload = {
        "success": True,
        "data": {
            "server": {"argv": [str(origin)], "cwd": str(tmp_path), "version": "2.3.4"},
            "session_state": {"plugin_hub_configured": True},
        },
    }

    def result():
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])

    probe.validate_tool_result(result(), origin, tmp_path)
    failed = result().model_copy(update={"is_error": True})
    with pytest.raises(RuntimeError, match="tool failed"):
        probe.validate_tool_result(failed, origin, tmp_path)
    payload["data"]["server"]["argv"] = [str(ROOT / "Server/src/main.py")]
    with pytest.raises(RuntimeError, match="origin"):
        probe.validate_tool_result(result(), origin, tmp_path)


@pytest.mark.asyncio
async def test_installed_http_probe_rejects_protocol_failure():
    import httpx2

    probe = load_mcp_probe()

    async def malformed(_request):
        return httpx2.Response(
            200, headers={"content-type": "application/json"}, json={"bad": True}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(malformed)) as client:
        with pytest.raises(ExceptionGroup):
            await probe.http_exchange("http://127.0.0.1/mcp", client, ROOT / "bad.py", ROOT)


def test_archive_probe_refuses_editable_worktree_imports(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROBE),
            "--forbid-source",
            str(ROOT),
            "--candidate-sha",
            "a" * 40,
        ],
        cwd=tmp_path,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "Installed archive required" in result.stderr


def test_archive_workflow_installs_the_checked_candidate_without_lock_or_source_path():
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = workflow["jobs"]["installed_archive"]
    assert job.get("continue-on-error", False) is False
    assert job["if"] == "inputs.validation_profile != 'tools-only'"
    steps = job["steps"]
    install = next(
        step
        for step in steps
        if step.get("name") == "Install candidate archive without runtime lock"
    )
    assert "git archive" in install["run"]
    assert '"${revision}:Server"' in install["run"]
    assert "$revision = git rev-parse HEAD" in install["run"]
    assert "ARCHIVE_CANDIDATE_SHA=$revision" in install["run"]
    assert "uv pip install" in install["run"]
    assert "ykh09242-unity-mcp-server @ " in install["run"]
    assert "uv sync" not in install["run"]
    behavior = next(
        step
        for step in steps
        if step.get("name") == "Exercise installed MCP, HTTP and CLI behavior"
    )
    assert " -I " in behavior["run"]
    assert "--forbid-source" in behavior["run"]
    assert "--candidate-sha $env:ARCHIVE_CANDIDATE_SHA" in behavior["run"]
    assert "check_server_startup.py" in behavior["run"]
    assert "PYTHONPATH" not in job.get("env", {})
    assert "PYTHONPATH" not in behavior.get("env", {})


def test_archive_workflow_adds_only_linux_315_and_retains_cross_os_311_behavior():
    # Given the unlocked installed-artifact job, separate from locked test suites.
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = workflow["jobs"]["installed_archive"]
    # When its matrix selects the interpreter for installation and behavior probes.
    matrix = job["strategy"]["matrix"]
    setup = next(step for step in job["steps"] if "setup-uv@" in step.get("uses", ""))
    install = next(
        step
        for step in job["steps"]
        if step.get("name") == "Install candidate archive without runtime lock"
    )
    # Then all original OS checks remain, with exactly one newest-minor addition.
    assert matrix["os"] == ["windows-latest", "ubuntu-26.04", "macos-26-intel"]
    assert matrix["python-version"] == ["3.11"]
    assert matrix["include"] == [{"os": "ubuntu-26.04", "python-version": "3.15"}]
    assert setup["with"]["python-version"] == "${{ matrix.python-version }}"
    assert "uv venv --no-config --python $env:ARCHIVE_PYTHON_VERSION" in install["run"]
    assert install["env"]["ARCHIVE_PYTHON_VERSION"] == "${{ matrix.python-version }}"
    assert job["env"]["PY_KEY_VALUE_DISABLE_BEARTYPE"] == (
        "${{ matrix.python-version == '3.15' && 'true' || 'false' }}"
    )
    behavior = next(
        step
        for step in job["steps"]
        if step.get("name") == "Exercise installed MCP, HTTP and CLI behavior"
    )
    assert behavior["run"].count(" -I -W error ") == 2
