"""Archive assurance runs public behavior with installed imports and no Editor."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
from importlib.util import module_from_spec, spec_from_file_location

import pytest
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tools/check_server_archive.py"


def archive_checker():
    spec = spec_from_file_location("archive_checker", PROBE)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mismatch", ["pin", "url", "subdirectory", "editable", "vcs"])
@pytest.mark.parametrize("archive_info", [{}, {"hashes": {}}, {"hashes": {"sha256": "b" * 64}}])
def test_selected_archive_requires_matching_installed_origin(mismatch, archive_info):
    module = archive_checker()
    source = f"https://github.com/ykh09242/unity-mcp/archive/{'a' * 40}.zip#subdirectory=Server"
    direct = {
        "url": source.split("#")[0],
        "subdirectory": "Server",
        "archive_info": archive_info,
    }
    assert module.verify_archive_origin(direct, source, "a" * 40) == direct
    match mismatch:
        case "pin":
            direct["url"] = direct["url"].replace("a" * 40, "c" * 40)
        case "url":
            direct["url"] = direct["url"].replace("github.com", "example.invalid")
        case "subdirectory":
            direct["subdirectory"] = "Other"
        case "editable":
            direct["dir_info"] = {"editable": True}
        case "vcs":
            direct["vcs_info"] = {"commit_id": "a" * 40}
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, source, "a" * 40)


@pytest.mark.parametrize("proof", ["metadata", "installation-url-fragment"])
def test_local_unpublished_archive_is_bound_to_file_bytes(tmp_path, proof):
    module = archive_checker()
    archive = tmp_path / "unpublished candidate.zip"
    archive.write_bytes(b"owned synthetic candidate ZIP bytes")
    import hashlib

    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    direct = (
        {"url": archive.as_uri(), "archive_info": {"hashes": {"sha256": checksum}}}
        if proof == "metadata"
        else {"url": archive.as_uri() + "#sha256=" + checksum, "archive_info": {}}
    )
    # A local file is sufficient; no remote existence lookup is needed for this commit label.
    assert module.verify_archive_origin(direct, archive, "a" * 40) == direct
    archive.write_bytes(b"different archive")
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, archive, "a" * 40)


@pytest.mark.parametrize(
    "fragment",
    [
        "",
        "sha256=bad",
        "sha256=" + "0" * 64,
        "sha256=" + "c" * 64,
        "sha512=" + "c" * 128,
        "sha256={digest}&sha256={digest}",
        "sha256={digest}&sha256=" + "c" * 64,
        "sha256={digest}&subdirectory=Server",
        "sha256={digest}&other=1",
        "sha256={digest}&sha512=" + "c" * 128,
    ],
)
def test_local_archive_requires_one_valid_matching_installation_digest(tmp_path, fragment):
    import hashlib

    module = archive_checker()
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"owned candidate")
    fragment = fragment.format(digest=hashlib.sha256(archive.read_bytes()).hexdigest())
    direct = {"url": archive.as_uri() + ("#" + fragment if fragment else ""), "archive_info": {}}
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, archive, "a" * 40)


@pytest.mark.parametrize(
    "archive_info",
    [
        None,
        {"hashes": None},
        {"hashes": []},
        {"hashes": {"sha256": None}},
        {"hashes": {"sha256": "bad"}},
        {"hashes": {"sha256": "0" * 64}},
        {"hash": None},
        {"hash": "bad"},
        {"hash": "sha256=bad"},
        {"hashes": {"sha512": "bad"}},
        {"hashes": {"unknown": "b" * 64}},
        {"hash": "sha256=" + "b" * 64, "hashes": {"sha256": "c" * 64}},
    ],
)
def test_optional_remote_digest_does_not_accept_malformed_provided_metadata(archive_info):
    module = archive_checker()
    source = f"https://github.com/ykh09242/unity-mcp/archive/{'a' * 40}.zip#subdirectory=Server"
    direct = {"url": source.split("#")[0], "subdirectory": "Server", "archive_info": archive_info}
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, source, "a" * 40)


def test_local_fragment_and_metadata_digest_must_agree(tmp_path):
    import hashlib

    module = archive_checker()
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"owned candidate")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    direct = {
        "url": archive.as_uri() + "#sha256=" + checksum,
        "archive_info": {"hashes": {"sha256": "b" * 64}},
    }
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, archive, "a" * 40)


def test_archive_origin_accepts_consistent_deprecated_hash_alongside_sha256():
    module = archive_checker()
    source = f"https://github.com/ykh09242/unity-mcp/archive/{'a' * 40}.zip#subdirectory=Server"
    direct = {
        "url": source.split("#")[0],
        "subdirectory": "Server",
        "archive_info": {
            "hash": "sha512=" + "c" * 128,
            "hashes": {"sha256": "b" * 64, "sha512": "c" * 128},
        },
    }
    assert module.verify_archive_origin(direct, source, "a" * 40) == direct
    direct["archive_info"]["hash"] = "sha512=" + "d" * 128
    with pytest.raises(RuntimeError, match="provenance"):
        module.verify_archive_origin(direct, source, "a" * 40)


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
            "--archive-source",
            f"https://github.com/ykh09242/unity-mcp/archive/{'a' * 40}.zip#subdirectory=Server",
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
    assert "Get-FileHash -LiteralPath $archive -Algorithm SHA256" in install["run"]
    assert "'#sha256=' + $archiveDigest" in install["run"]
    assert install["run"].index("Get-FileHash") < install["run"].index("uv pip install")
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


def test_candidate_resolution_is_complete_before_probes_and_retained_on_failure():
    config = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = config["jobs"]["installed_archive"]
    install = next(
        step
        for step in job["steps"]
        if step.get("name") == "Install candidate archive without runtime lock"
    )
    program = install["run"]
    assert program.index("uv pip install") < program.index("uv pip freeze")
    assert program.index("uv pip freeze") < program.index('"ARCHIVE_PYTHON=$python"')
    assert "candidate_sha = $revision" in program
    assert "archive_sha256 = $archiveDigest" in program
    assert 'uv --version > "$evidence/toolchain.txt"' in program
    assert "print(sys.version); print(sys.executable); print(platform.platform())" in program
    assert 'uv pip freeze --no-config --python $python > "$evidence/resolved.txt"' in program
    behavior = next(
        step
        for step in job["steps"]
        if step.get("name") == "Exercise installed MCP, HTTP and CLI behavior"
    )
    assert "> .tmp/candidate-archive/behavior.json" in behavior["run"]
    assert "> .tmp/candidate-archive/registration.jsonl" in behavior["run"]
    upload = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == ".tmp/candidate-archive/"
    rows = [(os_name, "3.11") for os_name in job["strategy"]["matrix"]["os"]]
    rows.append(("ubuntu-26.04", "3.15"))
    names = {
        upload["with"]["name"]
        .replace("${{ matrix.os }}", os_name)
        .replace("${{ matrix.python-version }}", version)
        for os_name, version in rows
    }
    assert len(names) == len(rows)


@pytest.mark.parametrize("failed_probe", ["behavior", "registration"])
def test_candidate_failed_probe_retains_stderr_and_preserves_json_and_resolution(
    tmp_path, failed_probe
):
    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("The cross-platform workflow's PowerShell interpreter is unavailable")
    config = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = config["jobs"]["installed_archive"]
    behavior = next(
        step
        for step in job["steps"]
        if step.get("name") == "Exercise installed MCP, HTTP and CLI behavior"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, script in (
        ("behavior", "check_server_archive.py"),
        ("registration", "check_server_startup.py"),
    ):
        (tools / script).write_text(
            "import json, sys\n"
            f"print(json.dumps({{'probe': '{name}'}}))\n"
            f"sys.stderr.write('inert {name} diagnostic\\n')\n"
            f"sys.exit({7 if name == failed_probe else 0})\n",
            encoding="utf-8",
        )
    evidence = tmp_path / ".tmp/candidate-archive"
    evidence.mkdir(parents=True)
    (evidence / "resolved.txt").write_text("first==1\nsecond==2\n", encoding="utf-8")
    script = tmp_path / "probe.ps1"
    script.write_text(behavior["run"], encoding="utf-8")
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_WORKSPACE": str(tmp_path),
            "ARCHIVE_PYTHON": sys.executable,
            "ARCHIVE_CANDIDATE_SHA": "a" * 40,
            "ARCHIVE_FILE": str(tmp_path / "inert.zip"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert (evidence / f"{failed_probe}.stderr.log").read_text(encoding="utf-8").strip() == (
        f"inert {failed_probe} diagnostic"
    )
    assert json.loads((evidence / "behavior.json").read_text(encoding="utf-8")) == {
        "probe": "behavior"
    }
    if failed_probe == "registration":
        assert json.loads((evidence / "registration.jsonl").read_text(encoding="utf-8")) == {
            "probe": "registration"
        }
    assert (evidence / "resolved.txt").read_text(encoding="utf-8") == "first==1\nsecond==2\n"
    upload = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == evidence.relative_to(tmp_path).as_posix() + "/"
