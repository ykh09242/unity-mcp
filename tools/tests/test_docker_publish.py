"""Run the publisher with a local Docker recorder; never build or publish an image."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("working_directory", ["tools", "elsewhere"])
@pytest.mark.parametrize("image_environment", [False, True])
def test_publisher_uses_repository_context_from_any_directory(
    tmp_path: Path, working_directory: str, image_environment: bool
) -> None:
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
    arguments = ["v1.2.3"] if image_environment else ["fixture/image", "1.2.3"]

    # When: the real production publisher is called from tools/ or another working directory.
    result = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1/test-bin:$PATH"; shift; exec bash "$@"',
            "fixture",
            str(repo),
            str(script),
            *arguments,
        ],
        cwd=repo / "tools" if working_directory == "tools" else elsewhere,
        env={**os.environ, "IMAGE": "fixture/image", "DOCKER_TEST_RECORD": str(record)},
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
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
        "fixture/image:v1.2.3",
        "fixture/image:v1.2",
        "fixture/image:v1",
    ]
    assert "--push" in argv
