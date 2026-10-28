"""Container inputs stay bounded without inspecting a developer's private files."""

import json
from pathlib import Path
import re
import shlex
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]


def docker_subset_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    """Model only our literal/*/** rules with Docker's parent matching.

    This is a semantic fixture, not the native Moby matcher. Docker strips
    boundary slashes and a matching directory pattern applies to descendants.
    The last matching rule wins. Unsupported syntax fails rather than being
    interpreted using Git-ignore semantics. Primary implementation and cases:
    https://github.com/moby/patternmatcher/blob/main/patternmatcher.go
    https://github.com/moby/patternmatcher/blob/main/patternmatcher_test.go
    """
    parts = path.split("/")
    candidates = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    ignored = False
    for line in patterns:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        exception = line.startswith("!")
        pattern = line.removeprefix("!").strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_./*~-]+", pattern):
            raise ValueError(f"Unsupported Docker fixture syntax: {line}")
        segments = pattern.split("/")
        expression = ""
        for index, segment in enumerate(segments):
            if "**" in segment and segment != "**":
                raise ValueError(f"Unsupported Docker fixture syntax: {line}")
            if segment == "**":
                expression += ".*" if index == len(segments) - 1 else "(?:[^/]+/)*"
            else:
                expression += re.escape(segment).replace(r"\*", "[^/]*")
                if index < len(segments) - 1:
                    expression += "/"
        if any(re.fullmatch(expression, candidate) for candidate in candidates):
            ignored = not exception
    return ignored


@pytest.mark.parametrize(
    "patterns,path,ignored",
    [
        (("**", "!util/docker/web"), "util/docker/web/foo", False),
        (("**", "!util/docker/web", "util/docker/web/foo"), "util/docker/web/foo", True),
        (("**", "!Server/"), "Server/tests/private.txt", False),
        (("**", "!/Server/"), "Server/tests/private.txt", False),
        (("**", "!MCPForUnity/"), "MCPForUnity/Editor/private.txt", False),
        (("**", "!Server/src/**"), "Server/src/core/config.py", False),
        (("**", "!Server/src/**"), "Server/src", True),
        (("**", "!Server/src/", "**/.env*"), "Server/src/core/.env.fixture", True),
        (("a/*.txt",), "a/dir/file.txt", False),
        (("a/*.txt",), "a/file.txt", True),
        (("**/.foo",), ".foo", True),
        (("**/.foo",), "bar.foo", False),
        (("**/.foo",), "dir/.foo/private.txt", True),
    ],
)
def test_docker_subset_matches_upstream_parent_and_wildcard_examples(patterns, path, ignored):
    assert docker_subset_ignored(path, patterns) is ignored


def test_docker_subset_rejects_unmodeled_glob_syntax():
    with pytest.raises(ValueError, match="Unsupported"):
        docker_subset_ignored("fixture", ("[a-z]*",))


def test_container_context_admits_build_runtime_inputs_and_excludes_private_sentinels(tmp_path):
    rules = tuple((ROOT / ".dockerignore").read_text().splitlines())
    required = {
        "Server/pyproject.toml",
        "Server/uv.lock",
        "Server/README.md",
        "Server/LICENSE",
        "Server/src/main.py",
        "Server/src/core/sdk_compatibility.json",
        "Server/src/services/skills_bundle.json",
        "Server/src/services/test_results_view.html",
        "MCPForUnity/package.json",
    }
    private = {
        ".env",
        "Server/.env.local",
        "Server/src/core/.env.fixture",
        ".work/private.txt",
        ".codex/settings.json",
        ".git/config",
        ".tmp/log.txt",
        "Server/.venv/pyvenv.cfg",
        "Server/src/core/__pycache__/config.pyc",
        "Server/src/core/.work/private.json",
        "Server/src/core/key.pem",
        "TestProjects/fixture/Library/cache.bin",
        "website/node_modules/fixture.js",
        "unknown-future-directory/private.txt",
        "Server/tests/private.txt",
        "MCPForUnity/Editor/private.txt",
    }
    for relative in required | private:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("harmless fixture", encoding="utf-8")
    selected = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
        and not docker_subset_ignored(path.relative_to(tmp_path).as_posix(), rules)
    }
    assert selected == required


def test_precise_container_copy_preserves_install_metadata_and_package_source_location():
    lines = (ROOT / "Server/Dockerfile").read_text().splitlines()
    copies = [shlex.split(line)[1:] for line in lines if line.startswith("COPY ")]
    assert copies == [
        [
            "Server/pyproject.toml",
            "Server/uv.lock",
            "Server/README.md",
            "Server/LICENSE",
            "/app/Server/",
        ],
        ["Server/src/", "/app/Server/src/"],
        ["MCPForUnity/package.json", "/app/MCPForUnity/package.json"],
    ]
    metadata = tomllib.loads((ROOT / "Server/pyproject.toml").read_text())
    assert metadata["project"]["readme"] == "README.md"
    assert metadata["project"]["license-files"] == ["LICENSE"]
    assert "WORKDIR /app/Server" in lines
    assert "RUN uv sync --locked --no-dev" in lines
    assert json.loads(
        next(line.removeprefix("ENTRYPOINT ") for line in lines if line.startswith("ENTRYPOINT "))
    ) == ["uv", "run", "mcp-for-unity"]
