"""Run the publisher with a local Docker recorder; never build or publish an image."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[2]


def run_publisher(
    tmp_path: Path, working_directory: str, image_environment: bool, version: str
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    # Given: a repository path with spaces and a fake Docker that records actual argv/cwd.
    repo = tmp_path / "repository with spaces"
    for directory in ("tools", "Server", "MCPForUnity", "test-bin"):
        (repo / directory).mkdir(parents=True)
    script = repo / "tools/docker_publish.sh"
    script.write_bytes((ROOT / "tools/docker_publish.sh").read_bytes())
    (repo / "Server/Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    recorder = repo / "test-bin/docker"
    recorder.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\0" "$PWD" "$@" > "$DOCKER_TEST_RECORD"\n',
        encoding="utf-8",
        newline="\n",
    )
    recorder.chmod(0o755)
    elsewhere = tmp_path / "alternate working directory"
    elsewhere.mkdir()
    record = tmp_path / "docker-arguments"
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    arguments = [] if image_environment else ["fixture/image"]
    env = {
        key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if key in os.environ
    }
    env.update(IMAGE="fixture/image", DOCKER_TEST_RECORD=str(record), DOCKER_TEST_VERSION=version)

    # When: the real production publisher is called from tools/ or another working directory.
    result = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1/test-bin:$PATH"; shift; source "$@" "$DOCKER_TEST_VERSION"',
            "fixture",
            str(repo),
            str(script),
            *arguments,
        ],
        cwd=repo / "tools" if working_directory == "tools" else elsewhere,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    return result, record, repo, elsewhere


@pytest.mark.parametrize("working_directory", ["tools", "elsewhere"])
@pytest.mark.parametrize("image_environment", [False, True])
@pytest.mark.parametrize(
    ("version", "tags"),
    [
        ("v1.2.3", ["v1.2.3", "v1.2", "v1"]),
        ("0.0.0", ["v0.0.0", "v0.0", "v0"]),
        ("1.2.3-beta.1", ["v1.2.3-beta.1"]),
        ("v1.2.3-0", ["v1.2.3-0"]),
        ("1.2.3-alpha01", ["v1.2.3-alpha01"]),
        ("1.2.3-" + "a" * 121, ["v1.2.3-" + "a" * 121]),
    ],
)
def test_publisher_uses_repository_context_from_any_directory(
    tmp_path: Path, working_directory: str, image_environment: bool, version: str, tags: list[str]
) -> None:
    result, record, repo, elsewhere = run_publisher(
        tmp_path, working_directory, image_environment, version
    )
    # Then: Docker receives absolute paths matching the Dockerfile's root-relative COPY inputs.
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = record.read_bytes().decode().rstrip("\0").split("\0")
    argv = recorded[1:]
    assert argv[:2] == ["buildx", "build"]
    expected_root = repo.as_posix()
    caller = (repo / "tools" if working_directory == "tools" else elsewhere).as_posix()
    if os.name == "nt":
        expected_root = f"/{expected_root[0].lower()}{expected_root[2:]}"
        caller = f"/{caller[0].lower()}{caller[2:]}"
    assert argv[-1] == expected_root
    assert argv[argv.index("-f") + 1] == expected_root + "/Server/Dockerfile"
    assert recorded[0] == caller
    assert [argv[index + 1] for index, arg in enumerate(argv) if arg == "-t"] == [
        f"fixture/image:{tag}" for tag in tags
    ]
    assert "--push" in argv


@pytest.mark.parametrize(
    "version",
    [
        "",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1..3",
        "1.2.3-",
        "1.2.3-beta..1",
        "1.2.3-.beta",
        "1.2.3-beta.",
        "1.2.3-01",
        "1.2.3-beta.01",
        "1.2.3+build",
        "1.2.3-beta_1",
        "1.2.3-beta/1",
        "1.2.3-\u03b1",
        "1.2.3\n",
        "vv1.2.3",
        "1.2.3-" + "a" * 122,
    ],
)
def test_invalid_version_never_calls_docker(tmp_path: Path, version: str) -> None:
    result, record, _, _ = run_publisher(tmp_path, "tools", False, version)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "version" in result.stderr.lower()
    assert not record.exists()
