"""Run the repository's Python correctness checks with the locked dev environment.

From the repository root:
    uv run --directory Server --locked --extra dev python ../tools/lint_python.py
"""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "Server"


def main() -> int:
    """Keep the two unrelated tests packages in separate Pylint processes."""
    status = 0
    groups = (
        (SERVER, ("src", "tests", "__init__.py")),
        (ROOT, ("tools", "mcp_source.py", ".github/scripts")),
    )
    for directory, targets in groups:
        print(f"Pylint ({directory.name}): {', '.join(targets)}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pylint",
                f"--rcfile={SERVER / 'pyproject.toml'}",
                f"--source-roots={directory},{SERVER / 'src'}",
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
        )
        if result.returncode != 0:
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
