"""Every CLI reference source or checked example must select the drift workflow."""

import fnmatch
from pathlib import Path

import pytest
import yaml

from tools.cli_reference import EXAMPLE_PAGES


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_cli_inputs_select_canonical_reference_and_example_check(event):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs-generate.yml").read_text())
    # YAML 1.1 readers recognize the GitHub Actions 'on' key as True.
    paths = workflow[True][event]["paths"]
    inputs = [
        "Server/src/cli/main.py",
        "Server/src/cli/commands/future_command.py",
        "Server/src/cli/utils/future_helper.py",
        "Server/src/models/play_scenario_limits.py",
        "Server/src/models/doctor.py",
        "Server/src/models/future_metadata.py",
        "Server/src/core/telemetry.py",
        "Server/pyproject.toml",
        "Server/uv.lock",
        "tools/cli_reference.py",
        "tools/cli_example_validation.py",
        "tools/generate_docs_reference.py",
        "tools/generate_tool_capabilities.py",
        "Server/src/services/registry/capability_catalog.py",
        *EXAMPLE_PAGES,
    ]
    assert all(any(fnmatch.fnmatchcase(path, pattern) for pattern in paths) for path in inputs)
    checks = "\n".join(step.get("run", "") for step in workflow["jobs"]["check"]["steps"])
    assert "generate_docs_reference.py --check" in checks
