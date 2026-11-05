"""Build a local candidate and exercise its installed CLI and socket-free registration."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
INPUTS = frozenset(
    {
        ".dockerignore",
        "Server/Dockerfile",
        "Server/pyproject.toml",
        "Server/uv.lock",
        "Server/README.md",
        "Server/LICENSE",
        "MCPForUnity/package.json",
        "docker-compose.yml",
        "docker-compose.remote.yml",
        "tools/check_container.py",
        "tools/check_server_startup.py",
        "tools/tests/test_container_validation.py",
        ".github/workflows/python-tests.yml",
        ".github/workflows/fork-beta-tools.yml",
    }
)


def relevant(path: str) -> bool:
    return path in INPUTS or path.startswith("Server/src/")


def selected(event: Path, root: Path = ROOT) -> bool:
    """Use the complete event range; missing/ambiguous evidence requires a build."""
    try:
        payload = json.loads(event.read_text(encoding="utf-8"))
        if "pull_request" in payload:
            base = payload["pull_request"]["base"]["sha"]
        else:
            base = payload["before"]
        if not isinstance(base, str) or not re.fullmatch(r"[a-f0-9]{40}", base) or base == "0" * 40:
            return True
        result = subprocess.run(
            ["git", "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return any(relevant(path) for path in result.stdout.decode("utf-8").split("\0") if path)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return True


def probe(root: Path, image: str, arguments: list[str], *, registration: bool = False):
    name = f"unity-mcp-smoke-{uuid.uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--env",
        "UV_OFFLINE=true",
        "--env",
        "UV_NO_SYNC=true",
        "--env",
        "DISABLE_TELEMETRY=true",
        "--env",
        "UNITY_MCP_TELEMETRY_ENABLED=false",
    ]
    if registration:
        command += [
            "--mount",
            f"type=bind,source={root / 'tools/check_server_startup.py'},target=/tmp/check_server_startup.py,readonly",
            "--entrypoint",
            "/app/Server/.venv/bin/python",
        ]
    command += [image, *arguments]
    try:
        return subprocess.run(command, check=True, timeout=60, capture_output=True, text=True)
    finally:
        # The exact unique name also covers a lost/timeout run reply. Never clean other containers.
        subprocess.run(
            ["docker", "rm", "--force", name], check=False, timeout=15, capture_output=True
        )


def negative_controls(root: Path, image: str):
    """Real CI controls: invalid COPY and image entrypoint must be rejected."""
    with tempfile.TemporaryDirectory(prefix="unity-mcp-invalid-copy-") as temporary:
        dockerfile = Path(temporary) / "Dockerfile"
        dockerfile.write_text(
            f"FROM {image}\nCOPY absent-input-{uuid.uuid4().hex} /invalid\n", encoding="utf-8"
        )
        result = subprocess.run(
            ["docker", "build", "--file", str(dockerfile), str(root)],
            check=False,
            timeout=60,
            capture_output=True,
        )
        if result.returncode == 0:
            raise RuntimeError("Invalid COPY unexpectedly built successfully")
    name = f"unity-mcp-invalid-entrypoint-{uuid.uuid4().hex}"
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--entrypoint",
                f"/absent-entrypoint-{uuid.uuid4().hex}",
                image,
            ],
            check=False,
            timeout=60,
            capture_output=True,
        )
        if result.returncode == 0:
            raise RuntimeError("Invalid entrypoint unexpectedly ran successfully")
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], check=False, timeout=15, capture_output=True
        )


def validate(root: Path = ROOT, *, controls: bool = False):
    image = f"unity-mcp-candidate:{uuid.uuid4().hex}"
    subprocess.run(
        ["docker", "build", "--tag", image, "--file", str(root / "Server/Dockerfile"), str(root)],
        check=True,
        timeout=600,
    )
    cli = probe(root, image, ["--help"])
    if "Unity MCP (ykh09242)" not in cli.stdout:
        raise RuntimeError("Candidate entrypoint did not render installed CLI help")
    print(cli.stdout, end="")
    registration = probe(root, image, ["-I", "/tmp/check_server_startup.py"], registration=True)
    print(registration.stdout, end="")
    if controls:
        negative_controls(root, image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope")
    scope.add_argument("--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    scope.add_argument("--output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    run = commands.add_parser("validate")
    run.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "scope":
        required = args.event is None or selected(args.event)
        line = f"required={str(required).lower()}\n"
        print(line, end="")
        if args.output:
            with args.output.open("a", encoding="utf-8") as stream:
                stream.write(line)
    else:
        validate(controls=args.negative_controls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
