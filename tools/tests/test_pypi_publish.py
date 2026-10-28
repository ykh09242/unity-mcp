"""Offline artifact selection for the manual Python publisher."""

import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[1] / "python_distribution_files.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("python_distribution_files", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_project(tmp_path, *, name="ykh09242-unity-mcp-server", version="2.1.8"):
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    return project, dist


def test_selects_current_distribution_and_exact_version(tmp_path):
    project, dist = make_project(tmp_path)
    expected = {
        "ykh09242_unity_mcp_server-2.1.8-py3-none-any.whl",
        "ykh09242_unity_mcp_server-2.1.8-1-py3-none-any.whl",
        "ykh09242_unity_mcp_server-2.1.8.tar.gz",
    }
    excluded = {
        "ykh09242_unity_mcp_server-2.1.80-py3-none-any.whl",
        "ykh09242_unity_mcp_server-2.1.8rc1.tar.gz",
        "ykh09242_unity_mcp_server-2.1.8.post1.tar.gz",
        "ykh09242_unity_mcp_server-2.1.8+local.tar.gz",
        "mcpforunityserver-2.1.8-py3-none-any.whl",
        "ykh09242_unity_mcp_server-2.1.8.txt",
        "ykh09242_unity_mcp_server-2.1.8-invalid.whl",
        "invalid.tar.gz",
    }
    for filename in expected | excluded:
        (dist / filename).write_bytes(b"synthetic artifact")
    (dist / "ykh09242_unity_mcp_server-2.1.8-2-py3-none-any.whl").mkdir()
    selected = load_helper().distribution_files(project, dist)
    assert [path.name for path in selected] == sorted(expected)


def test_normalizes_project_name_and_version(tmp_path):
    project, dist = make_project(tmp_path, name="Example.Project_Name", version="1.0-rc1")
    filename = "example_project_name-1.0rc1-py3-none-any.whl"
    (dist / filename).write_bytes(b"synthetic artifact")
    assert load_helper().distribution_files(project, dist) == [dist / filename]


def test_cli_accepts_quoted_paths_and_prints_only_filenames(tmp_path):
    directory = tmp_path / "publisher's project"
    directory.mkdir()
    project, dist = make_project(directory)
    filename = "ykh09242_unity_mcp_server-2.1.8.tar.gz"
    (dist / filename).write_bytes(b"synthetic artifact")
    result = subprocess.run(
        [sys.executable, str(HELPER), str(project), str(dist)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [filename]


def test_cli_fails_when_no_current_artifacts_exist(tmp_path):
    project, dist = make_project(tmp_path)
    (dist / "ykh09242_unity_mcp_server-2.1.80.tar.gz").write_bytes(b"other version")
    result = subprocess.run(
        [sys.executable, str(HELPER), str(project), str(dist)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "No distribution files match" in result.stderr


@pytest.mark.parametrize("current_artifacts", [True, False])
def test_publisher_passes_only_selected_artifacts_to_upload(tmp_path, current_artifacts):
    bash = shutil.which("bash")
    if bash is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        if candidate.is_file():
            bash = str(candidate)
    if bash is None:
        pytest.skip("Bash is required for the offline publisher fixture")

    repo = tmp_path / "publisher's project"
    tools = repo / "tools"
    server = repo / "Server"
    tools.mkdir(parents=True)
    server.mkdir()
    make_project(server)
    script = tools / "pypi_publish.sh"
    shutil.copyfile(HELPER.with_name("pypi_publish.sh"), script)
    shutil.copyfile(HELPER, tools / HELPER.name)
    uploads = tmp_path / "upload-arguments.txt"
    wheel = "ykh09242_unity_mcp_server-2.1.8-py3-none-any.whl"
    sdist = "ykh09242_unity_mcp_server-2.1.8.tar.gz"
    outputs = ["ykh09242_unity_mcp_server-2.1.80.tar.gz", "unrelated-2.1.8.tar.gz"]
    if current_artifacts:
        outputs.extend([wheel, sdist])
    fixture = tmp_path / "offline-publisher.sh"
    fixture.write_text(
        "uv() {\n"
        + "\n".join(f"  printf 'fixture' > {shlex.quote('dist/' + name)}" for name in outputs)
        + "\n}\n"
        + "python3() {\n"
        + '  if [[ "${1:-}" == "-m" && "${2:-}" == "twine" ]]; then\n'
        + '    if [[ "${3:-}" == "upload" ]]; then\n'
        + f"      printf '%s\\n' \"$@\" > {shlex.quote(uploads.as_posix())}\n"
        + "    fi\n    return 0\n  fi\n"
        + f'  {shlex.quote(Path(sys.executable).as_posix())} "$@"\n'
        + "}\n",
        encoding="utf-8",
        newline="\n",
    )
    env = {
        key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if key in os.environ
    }
    env.update(BASH_ENV=fixture.as_posix(), TWINE_USERNAME="fixture", TWINE_PASSWORD="fixture")
    result = subprocess.run(
        [bash, script.as_posix(), "--test"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if current_artifacts:
        assert result.returncode == 0, result.stderr
        arguments = uploads.read_text(encoding="utf-8").splitlines()
        assert arguments[:5] == ["-m", "twine", "upload", "--repository", "testpypi"]
        assert {Path(argument).name for argument in arguments[5:]} == {wheel, sdist}
    else:
        assert result.returncode == 2
        assert "No distribution files match" in result.stderr
        assert not uploads.exists()
