"""Run the repository's Python correctness checks with the locked dev environment.

From the repository root:
    uv run --directory Server --locked --extra dev python ../tools/lint_python.py
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "Server"


def lint_group(directory: Path, targets: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Keep one source group's import roots isolated in its own Pylint process."""
    # dill 0.4.1 still serializes co_lnotab, removed in Python 3.15.
    # Each process stays serial on 3.15 without patching the serializer.
    parallel_options = ["--jobs=1"] if sys.version_info >= (3, 15) else []
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pylint",
            f"--rcfile={SERVER / 'pyproject.toml'}",
            f"--source-roots={directory},{SERVER / 'src'}",
            *parallel_options,
            *targets,
        ],
        cwd=directory,
        # Match the installed src layout and standalone tools' import paths.
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                str(path) for path in (directory, SERVER / "src", ROOT / "tools")
            ),
        },
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    """Run both independent source groups concurrently and retain every diagnostic."""
    status = 0
    groups = (
        (SERVER, ("src", "tests", "__init__.py")),
        (ROOT, ("tools", "mcp_source.py", ".github/scripts")),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        for directory, targets in groups:
            print(f"Pylint ({directory.name}): {', '.join(targets)}", flush=True)
            futures.append(executor.submit(lint_group, directory, targets))
        for (directory, _), future in zip(groups, futures):
            try:
                result = future.result()
            except OSError as error:
                print(f"Pylint ({directory.name}) failed to start: {error}", file=sys.stderr)
                status = 1
                continue
            print(result.stdout, end="", flush=True)
            print(result.stderr, end="", file=sys.stderr, flush=True)
            if result.returncode != 0:
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
