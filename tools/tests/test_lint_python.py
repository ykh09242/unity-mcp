"""Exercise the real lint command and its failure status on isolated source trees."""

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def lint_project(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    sources = {
        "Server/__init__.py": "",
        "Server/src/__init__.py": "",
        "Server/src/server_module.py": "VALUE = 3\n",
        "Server/tests/__init__.py": "",
        "Server/tests/fixture.py": "SERVER_VALUE = 1\n",
        "Server/tests/test_imports.py": (
            "from tests.fixture import SERVER_VALUE\n"
            "from server_module import VALUE\nassert (SERVER_VALUE, VALUE) == (1, 3)\n"
        ),
        "tools/helper.py": "VALUE = 4\n",
        "tools/tests/__init__.py": "",
        "tools/tests/fixture.py": "TOOL_VALUE = 2\n",
        "tools/tests/test_imports.py": (
            "from tools.tests.fixture import TOOL_VALUE\n"
            "from helper import VALUE\nassert (TOOL_VALUE, VALUE) == (2, 4)\n"
        ),
        "mcp_source.py": "",
        ".github/scripts/check.py": "",
    }
    for relative, source in sources.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for relative in ("tools/lint_python.py", "Server/pyproject.toml"):
        (project / relative).write_bytes((ROOT / relative).read_bytes())
    return project


def run_lint(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(project / "tools" / "lint_python.py")],
        cwd=project.parent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_lint_accepts_independent_tests_packages_from_another_directory(lint_project):
    # Given: server and tooling tests import distinct fixtures with the same filename.
    # When: the real command runs outside the repository working directory.
    result = run_lint(lint_project)
    # Then: both package roots resolve without import errors or disabled checks.
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "Server/src/broken.py",
        "Server/tests/test_broken.py",
        "tools/broken.py",
        "tools/tests/test_broken.py",
        ".github/scripts/broken.py",
        "mcp_source.py",
    ],
)
def test_lint_fails_on_undefined_names_in_each_source_scope(lint_project, relative):
    # Given: a new file containing a real error in one of the covered source trees.
    (lint_project / relative).write_text("RESULT = missing_name\n", encoding="utf-8")
    # When: the same entry point used by CI checks the tree.
    result = run_lint(lint_project)
    # Then: either subprocess's diagnostics make the overall check fail.
    assert result.returncode != 0
    assert "undefined-variable" in result.stdout


def test_lint_fails_on_selected_correctness_warnings(lint_project):
    # Given: valid Python with a shared mutable argument default.
    (lint_project / "Server/src/shared.py").write_text(
        "def append(value, items=[]):\n    items.append(value)\n    return items\n",
        encoding="utf-8",
    )
    # When: the configured correctness checks run.
    result = run_lint(lint_project)
    # Then: warnings selected by policy block CI as well as errors.
    assert result.returncode != 0
    assert "dangerous-default-value" in result.stdout
