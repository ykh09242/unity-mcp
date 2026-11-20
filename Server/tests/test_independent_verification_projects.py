"""Validate each independent project's saved scenario against the distributed schema."""

import json
from pathlib import Path

import pytest

from models.play_scenarios import PlayScenario

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "TestProjects/VerificationProjects"


@pytest.mark.parametrize(
    ("project", "scenario", "state", "expected"),
    [
        ("Harbor", "berth-number", "harbor.berth", 3),
        ("Observatory", "instrument-name", "observatory.instrument", "Zenith"),
    ],
)
def test_independent_project_contract(project, scenario, state, expected):
    root = EXAMPLES / project
    definition = PlayScenario.model_validate_json(
        (root / f"ProjectSettings/MCPForUnity/PlayScenarios/{scenario}.json").read_bytes()
    )
    assert definition.name == scenario
    assert definition.steps[-1].state_id == state
    assert definition.steps[-1].state_equals == expected
    assert (root / definition.steps[0].scene).is_file()
    manifest = json.loads((root / "Packages/manifest.json").read_bytes())
    pin = manifest["dependencies"]["com.ykh09242.unity-mcp"]
    assert (root / "Packages" / pin.removeprefix("file:")).resolve() == ROOT / "MCPForUnity"
    (other,) = [
        path
        for path in EXAMPLES.iterdir()
        if path.is_dir() and path.name in {"Harbor", "Observatory"} - {project}
    ]
    assert not (other / f"ProjectSettings/MCPForUnity/PlayScenarios/{scenario}.json").exists()
