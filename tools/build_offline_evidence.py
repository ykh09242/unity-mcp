"""Build a Click-only wheel from the same command and reader sources as unity-mcp."""

import argparse
import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

_SERVER = Path(__file__).resolve().parents[1] / "Server"
_PACKAGE = "unity_mcp_evidence"


def _metadata() -> str:
    server = tomllib.loads((_SERVER / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((_SERVER / "uv.lock").read_text(encoding="utf-8"))
    (click,) = (package["version"] for package in lock["package"] if package["name"] == "click")
    project = server["project"]
    backend = server["build-system"]
    return f"""
[project]
name = "ykh09242-unity-mcp-evidence"
version = {json.dumps(project["version"])}
description = "Offline Unity MCP verification evidence reader"
requires-python = {json.dumps(project["requires-python"])}
license = {json.dumps(project["license"])}
license-files = ["LICENSE"]
dependencies = ["click=={click}"]

[project.scripts]
unity-mcp-evidence = "{_PACKAGE}.commands:offline_main"

[build-system]
requires = {json.dumps(backend["requires"])}
build-backend = {json.dumps(backend["build-backend"])}

[tool.setuptools.packages.find]
where = ["src"]
"""


def build_wheel(output: Path) -> None:
    output = output.resolve()
    with TemporaryDirectory(prefix="unity-mcp-offline-evidence-") as name:
        project = Path(name)
        shutil.copytree(
            _SERVER / "src" / _PACKAGE,
            project / "src" / _PACKAGE,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (project / "pyproject.toml").write_text(_metadata(), encoding="utf-8")
        shutil.copyfile(_SERVER / "LICENSE", project / "LICENSE")
        subprocess.run(
            ["uv", "build", "--wheel", "--no-create-gitignore", "--out-dir", str(output)],
            cwd=project,
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Wheel destination directory.")
    build_wheel(parser.parse_args().output)


if __name__ == "__main__":
    main()
