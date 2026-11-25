"""Authored tool examples follow the advertised MCP inputs and survive generation."""

import asyncio
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator, validate

from tools.tests.test_skill_documentation import distributed_tool_examples

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import generate_docs_reference as docs  # noqa: E402
from public_contracts import capture_public_contracts  # noqa: E402

EDITING_PAGES = (
    ("core", "manage_scene_edits", 4),
    ("prefabs", "prefab_create", 1),
    ("prefabs", "prefab_contents", 2),
    ("legacy", "manage_scene", 7),
)
WORKFLOW_PAGES = (
    ("testing", "manage_verification", ("prepare", "run", "status", "report")),
    ("testing", "manage_play_scenario", ("save", "run", "status")),
)
ARGUMENT_BLOCKS = re.compile(r'^```json title="MCP arguments"\n(.*?)^```', re.M | re.S)


def _arguments(path: Path) -> list[dict]:
    return [
        json.loads(block) for block in ARGUMENT_BLOCKS.findall(docs._read_existing_examples(path))
    ]


@pytest.fixture(scope="module")
def public_contracts():
    return capture_public_contracts()


def test_distributed_skill_calls_validate_on_every_protocol(public_contracts):
    for path, example in distributed_tool_examples():
        name, params = example["tool"], example["params"]
        for mode, catalog in public_contracts["protocols"].items():
            schema = catalog["tools"][name]["inputSchema"]
            error = next(Draft202012Validator(schema).iter_errors(params), None)
            assert error is None, (path, name, mode, error.message)
            assert params.keys() <= schema["properties"].keys(), (path, name, mode)


@pytest.mark.parametrize("group,name,count", EDITING_PAGES)
def test_editing_examples_validate_against_observed_sdk_schema(
    public_contracts, group, name, count
):
    path = docs.TOOLS_OUT / group / f"{name}.md"
    examples = docs._read_existing_examples(path)
    assert "../../../guides/scene-and-prefab-edits.md" in examples
    assert (path.parent / "../../../guides/scene-and-prefab-edits.md").resolve().is_file()
    blocks = re.findall(r"^```json\n(.*?)^```", examples, re.MULTILINE | re.DOTALL)
    assert len(blocks) == count
    for protocol in public_contracts["protocols"].values():
        schema = protocol["tools"][name]["inputSchema"]
        for block in blocks:
            validate(instance=json.loads(block), schema=schema)


def test_examples_survive_regeneration(tmp_path):
    tools_root = tmp_path / "tools"
    docs.generate(tools_root, tmp_path / "resources", examples_source=docs.TOOLS_OUT)
    paths = {docs.TOOLS_OUT / group / f"{name}.md" for group, name, _ in EDITING_PAGES}
    paths.update(path for path in docs.TOOLS_OUT.glob("*/*.md") if _arguments(path))
    for path in sorted(paths):
        relative = path.relative_to(docs.TOOLS_OUT)
        original = docs._read_existing_examples(path)
        generated = docs._read_existing_examples(tools_root / relative)
        assert "No examples yet" not in original
        assert generated == original, relative


def test_marked_arguments_validate_against_observed_sdk_schemas(public_contracts):
    for path in sorted(docs.TOOLS_OUT.glob("*/*.md")):
        for arguments in _arguments(path):
            for protocol in public_contracts["protocols"].values():
                schema = protocol["tools"][path.stem]["inputSchema"]
                assert arguments.keys() <= schema["properties"].keys(), path
                validate(instance=arguments, schema=schema)


@pytest.mark.parametrize("group,name,actions", WORKFLOW_PAGES)
def test_workflow_examples_use_closed_action_inputs(group, name, actions):
    docs._ensure_server_on_path()
    from models.play_scenarios import PlayScenarioCommand
    from models.verification import parse_verification_options

    arguments = _arguments(docs.TOOLS_OUT / group / f"{name}.md")
    assert tuple(step["action"] for step in arguments) == actions
    for step in arguments:
        if name == "manage_verification":
            parse_verification_options(step["action"], step.get("options"))
        else:
            PlayScenarioCommand.model_validate(step)


def test_verification_example_follows_returned_handles_to_settled_report(monkeypatch):
    docs._ensure_server_on_path()
    from core.config import config
    from services.verification import service as verification
    from services.verification.runner import VerificationRunner

    prepare, run, status, report = _arguments(docs.TOOLS_OUT / "testing/manage_verification.md")
    (script,) = prepare["options"]["paths"]
    fixture_root = docs.REPO_ROOT / "TestProjects/VerificationProjects/Harbor"
    assert (fixture_root / script).is_file()
    target = SimpleNamespace(
        scope=SimpleNamespace(key=("user", "project"), generation="session"),
        resume_generation="editor-process",
        is_current=lambda: True,
        authorize_public=None,
        public_available=None,
    )
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(verification, "capture_target", AsyncMock(return_value=target))
    monkeypatch.setattr(
        verification, "get_unity_instance_from_context", AsyncMock(return_value="Harbor@project")
    )
    monkeypatch.setattr(
        verification,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"byte_hash": "missing", "document": {"schema_version": 1, "profiles": {}}},
            }
        ),
    )

    async def exercise() -> None:
        release = asyncio.Event()

        async def finish(runner: VerificationRunner) -> dict:
            await release.wait()
            runner.report["state"] = "passed"
            return runner.report

        monkeypatch.setattr(VerificationRunner, "run", finish)
        subject = verification.VerificationService()
        ctx = SimpleNamespace(fastmcp=SimpleNamespace())
        prepared = await subject.execute(ctx, **prepare)
        assert prepared["success"] and prepared["data"]["ready"]
        run["options"]["preparation_id"] = prepared["data"]["preparation_id"]
        started = await subject.execute(ctx, **run)
        assert started["success"]
        run_id = started["data"]["run_id"]
        status["options"]["run_id"] = report["options"]["run_id"] = run_id
        try:
            observed = await subject.execute(ctx, **status)
            assert observed["data"]["state"] == "running"
        finally:
            release.set()
            await asyncio.gather(*subject.tasks.values())
        result = await subject.execute(ctx, **report)
        assert result["success"]
        assert result["data"]["run_id"] == run_id
        assert result["data"]["state"] == "passed"
        assert result["data"]["native_settled"] is True
        assert result["data"]["required_permissions"] == ["edit", "read", "run"]

    asyncio.run(exercise())


def test_play_scenario_example_reuses_job_id_and_matches_native_fixture(monkeypatch):
    docs._ensure_server_on_path()
    from services.operation_permissions import required_permissions
    import services.tools.manage_play_scenario as scenarios

    save, run, status = _arguments(docs.TOOLS_OUT / "testing/manage_play_scenario.md")
    fixture = docs.REPO_ROOT / (
        "TestProjects/VerificationProjects/Harbor/ProjectSettings/MCPForUnity/"
        "PlayScenarios/berth-number.json"
    )
    saved = json.loads(fixture.read_text(encoding="utf-8"))
    assert save["scenario"]["steps"] == saved["steps"]
    assert run["name"] == save["scenario"]["name"]
    assert status["job_id"] == run["job_id"]
    for step, permission in zip((save, run, status), ("edit", "run", "read"), strict=True):
        assert required_permissions("manage_play_scenario", step) == {permission}

    # Replay the example with a report shape shared by the native and Python contract suites.
    fixture = docs.REPO_ROOT / (
        "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/PlayScenarios/"
        "Fixtures/shared-report-contract.json"
    )
    recorded = json.loads(fixture.read_text(encoding="utf-8"))["native_cases"]
    terminal = next(
        case["report"] for case in recorded if case["name"] == "native_success_equal_boundary"
    )
    terminal["job_id"] = run["job_id"]
    running = {"job_id": terminal["job_id"], "status": "running"}
    outputs = [
        {"success": True, "data": {"scenario": save["scenario"]}},
        {"success": True, "data": running},
        {"success": True, "data": terminal},
    ]
    monkeypatch.setattr(
        scenarios, "get_unity_instance_from_context", AsyncMock(return_value="Harbor@project")
    )
    monkeypatch.setattr(scenarios, "send_with_unity_instance", AsyncMock(side_effect=outputs))

    async def exercise() -> None:
        ctx = SimpleNamespace()
        assert (await scenarios.manage_play_scenario(ctx, **save))["success"]
        started = await scenarios.manage_play_scenario(ctx, **run)
        assert started["data"]["status"] == "running"
        status["job_id"] = started["data"]["job_id"]
        result = await scenarios.manage_play_scenario(ctx, **status)
        assert result["data"]["job_id"] == status["job_id"]
        assert result["data"]["status"] == "succeeded"
        assert result["data"]["runner_resources_released"] is True
        assert result["data"].get("report_error") is None
        assert {"steps", "logs"} <= result["data"].keys()

    asyncio.run(exercise())
