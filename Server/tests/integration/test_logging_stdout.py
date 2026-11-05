import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


# locate server src dynamically to avoid hardcoded layout assumptions
ROOT = Path(__file__).resolve().parents[2]  # tests/integration -> tests -> Server
candidates = [
    ROOT / "src",
]
SRC = next((p for p in candidates if p.exists()), None)
if SRC is None:
    searched = "\n".join(str(p) for p in candidates)
    pytest.skip(
        "MCP for Unity server source not found. Tried:\n" + searched,
        allow_module_level=True,
    )


MAINTENANCE_CLI = Path("services") / "reference_graph" / "maintenance.py"


def _stdout_writes(tree: ast.Module, *, cli_main: bool = False) -> bool:
    allowed = set()
    if cli_main:
        for definition in tree.body:
            if isinstance(definition, ast.FunctionDef) and definition.name == "main":
                # Defaults/decorators execute on import and remain guarded. Only
                # the explicit offline CLI entry point's body may emit output.
                for statement in definition.body:
                    allowed.update(ast.walk(statement))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node in allowed:
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "write":
            value = node.func.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "stdout"
                and isinstance(value.value, ast.Name)
                and value.value.id == "sys"
            ):
                return True
    return False


def test_no_print_statements_in_codebase():
    """Ensure no stray print/sys.stdout writes remain in server source."""
    # CLI tools that intentionally print to stdout
    ALLOWED_PRINT_FILES = {
        Path("scene_generator") / "test_pipeline.py",
    }
    offenders = []
    syntax_errors = []
    for py_file in SRC.rglob("*.py"):
        # Skip virtual envs and third-party packages if they exist under SRC
        parts = set(py_file.parts)
        if ".venv" in parts or "site-packages" in parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            # Be tolerant of encoding edge cases in source tree without silently dropping bytes
            text = py_file.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(py_file))
        except SyntaxError:
            syntax_errors.append(py_file.relative_to(SRC))
            continue

        rel_path = py_file.relative_to(SRC)
        if rel_path not in ALLOWED_PRINT_FILES and _stdout_writes(
            tree, cli_main=rel_path == MAINTENANCE_CLI
        ):
            offenders.append(rel_path)
    assert not syntax_errors, "syntax errors in: " + ", ".join(str(e) for e in syntax_errors)
    assert not offenders, "stdout writes found in: " + ", ".join(str(o) for o in offenders)


@pytest.mark.parametrize(
    "source",
    [
        "print('import output')",
        "sys.stdout.write('import output')",
        "def archive_scope():\n    print('library output')",
        "def main(argv=print('default output')):\n    pass",
        "@print('decorator output')\ndef main():\n    pass",
    ],
)
def test_offline_cli_exception_still_rejects_non_cli_stdout(source):
    assert _stdout_writes(ast.parse(source), cli_main=True)


def test_main_output_is_only_allowed_for_the_named_offline_cli():
    tree = ast.parse("def main():\n    print('explicit CLI output')")
    assert _stdout_writes(tree)
    assert not _stdout_writes(tree, cli_main=True)


def test_maintenance_import_and_programmatic_inspection_are_silent(tmp_path):
    script = """
import gc
from pathlib import Path
import sys
from services.reference_graph import maintenance
from services.reference_graph.store import GraphScope, GraphStore
root = Path(sys.argv[1]) / 'state'
scope = GraphScope('synthetic-owner', 'editor', 'project')
store = GraphStore.open(root, scope)
del store
gc.collect()
report = maintenance.archive_scope(root, scope.key, Path(sys.argv[1]) / 'archive',
                                   all_servers_stopped=True)
assert report['outcome'] == 'dry_run'
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_explicit_maintenance_module_invocation_emits_one_json_document(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "services.reference_graph.maintenance",
            "list",
            "--directory",
            str(tmp_path / "missing-state"),
            "--all-servers-stopped",
        ],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["success"] is False
    assert result.stderr == ""
