"""Versioned typed UI state and privacy, through real saved-artifact consumers."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_ui import capture_ui_snapshot as capture_command
from cli.utils.play_scenario_ui import compare_ui_snapshots, load_ui_snapshot
from models import ui_snapshots as contracts
from services.tools.capture_ui_snapshot import capture_ui_snapshot
from .test_play_scenario_ui import snapshot, save

SECRET = "UI04_PRIVATE_SENTINEL_7a29"


def snapshot_v2():
    value = snapshot()
    value["schema_version"] = 2
    value["evidence"].update(
        control_state_method="typed_allowlist_v1", input_privacy="recognized_input_ownership_v1"
    )
    for node in value["nodes"]:
        node["state"] = {}
    return value


@pytest.mark.parametrize(
    "state",
    [
        {"toggle": {"value": True}},
        {
            "slider": {
                "kind": "float",
                "value": 0.25,
                "low": 0.0,
                "high": 1.0,
                "whole_numbers": False,
            }
        },
        {"slider": {"kind": "integer", "value": 3, "low": 0, "high": 10}},
        {"selection": {"kind": "index", "index": -1}},
        {"selection": {"kind": "indices", "selection_type": "multiple", "indices": [1, 3]}},
        {"input": {"redacted": True, "read_only": False, "password": True}},
        {"scroll": {"kind": "normalized", "position": [0.0, 0.5]}},
    ],
)
def test_v2_state_is_strict_and_parseable(state):
    value = snapshot_v2()
    value["nodes"][0]["state"] = state
    parsed = contracts.validate_ui_snapshot(value, require_v2=True)
    assert parsed.schema_version == 2
    assert parsed.nodes[0].state.model_dump(exclude_none=True, mode="json") == state


@pytest.mark.parametrize(
    "state",
    [
        {"toggle": {"value": 1}},
        {
            "slider": {
                "kind": "float",
                "value": True,
                "low": 0.0,
                "high": 1.0,
                "whole_numbers": False,
            }
        },
        {
            "slider": {
                "kind": "float",
                "value": float("nan"),
                "low": 0.0,
                "high": 1.0,
                "whole_numbers": False,
            }
        },
        {"slider": {"kind": "integer", "value": 1.5, "low": 0, "high": 10}},
        {"selection": {"kind": "indices", "selection_type": "multiple", "indices": [1, 1]}},
        {"selection": {"kind": "indices", "selection_type": "multiple", "indices": [3, 1]}},
        {"selection": {"kind": "indices", "selection_type": "multiple", "indices": [-1]}},
        {
            "selection": {
                "kind": "indices",
                "selection_type": "multiple",
                "indices": list(range(65)),
            }
        },
        {"input": {"redacted": False, "read_only": False, "password": False}},
        {"input": {"redacted": 1, "read_only": False, "password": False}},
        {"input": {"redacted": True, "read_only": False, "password": False, "value": SECRET}},
        {"unknown": {"value": SECRET}},
    ],
)
def test_invalid_state_refuses_without_echoing_payload(state):
    value = snapshot_v2()
    value["nodes"][0]["state"] = state
    with pytest.raises(ValueError) as error:
        contracts.validate_ui_snapshot(value)
    assert SECRET not in str(error.value)
    assert len(str(error.value)) < 300


def test_input_state_requires_null_text_without_echoing_secret():
    value = snapshot_v2()
    value["nodes"][0].update(
        text=SECRET, state={"input": {"redacted": True, "read_only": False, "password": True}}
    )
    with pytest.raises(ValueError) as error:
        contracts.validate_ui_snapshot(value)
    assert SECRET not in str(error.value)


def test_versions_preserve_legacy_without_silent_state_upgrade(tmp_path):
    old = save(tmp_path / "old.json", snapshot())
    assert load_ui_snapshot(old).schema_version == 1
    assert compare_ui_snapshots(old, old)["status"] == "same"
    new = save(tmp_path / "new.json", snapshot_v2())
    with pytest.raises(ValueError, match="schema_version"):
        compare_ui_snapshots(old, new)
    for version in (True, 3, "2"):
        value = snapshot_v2()
        value["schema_version"] = version
        with pytest.raises(ValueError):
            contracts.validate_ui_snapshot(value)
    legacy = snapshot()
    legacy["nodes"][0]["state"] = {}
    with pytest.raises(ValueError):
        contracts.validate_ui_snapshot(legacy)
    missing = snapshot_v2()
    missing["nodes"][0].pop("state")
    with pytest.raises(ValueError):
        contracts.validate_ui_snapshot(missing)


def test_state_only_real_comparator_ignores_geometry_tolerance_for_values(tmp_path):
    before = snapshot_v2()
    after = deepcopy(before)
    before["nodes"][0]["state"] = {"toggle": {"value": False}}
    after["nodes"][0]["state"] = {"toggle": {"value": True}}
    left, right = save(tmp_path / "a.json", before), save(tmp_path / "b.json", after)
    assert compare_ui_snapshots(left, right)["changed"] == [
        {"id": before["target"], "fields": ["state"]}
    ]
    assert compare_ui_snapshots(left, left)["status"] == "same"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native", [snapshot(), {"complete": True}, {**snapshot_v2(), "unexpected": SECRET}]
)
async def test_live_boundary_rejects_legacy_or_invalid_before_publishing(monkeypatch, native):
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.preflight", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.send_with_unity_instance",
        AsyncMock(return_value={"success": True, "data": native}),
    )
    result = await capture_ui_snapshot(SimpleNamespace(), "ugui", "Canvas", 200)
    assert result["success"] is False
    assert SECRET not in json.dumps(result)


def test_cli_rejects_v1_live_without_new_file_or_secret_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    native = snapshot()
    native["nodes"][0]["text"] = SECRET
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.run_command", lambda *_: {"success": True, "data": native}
    )
    output = tmp_path / "rejected.json"
    result = CliRunner().invoke(
        capture_command, ["--framework", "ugui", "--target", "Canvas", "--output", str(output)]
    )
    assert result.exit_code != 0
    assert not output.exists()
    assert SECRET not in result.output


@pytest.mark.asyncio
async def test_native_failure_never_echoes_untrusted_getter_error(monkeypatch):
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.preflight", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.send_with_unity_instance",
        AsyncMock(return_value={"success": False, "error": SECRET}),
    )
    result = await capture_ui_snapshot(SimpleNamespace(), "ugui", "Canvas", 200)
    assert result["success"] is False
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("mixed,expected,code", [(False, "failed", 1), (True, "unavailable", 2)])
def test_v2_saved_gate_keeps_state_change_or_version_refusal(
    tmp_path, monkeypatch, mixed, expected, code
):
    from cli.utils import play_scenario_regression as regression
    from .test_play_scenario_regression_gates import _ordinary_player

    before, after = snapshot_v2(), snapshot_v2()
    before["nodes"][0]["state"] = {"toggle": {"value": False}}
    after["nodes"][0]["state"] = {"toggle": {"value": True}}
    if mixed:
        before = snapshot()
    save(tmp_path / "before.json", before)
    save(tmp_path / "after.json", after)
    profile = tmp_path / "profile.json"
    save(
        profile,
        {
            "schema_version": 1,
            "name": "UI04 state gate",
            "execution": {"build_directory": "build"},
            "analyze": False,
            "ui_checks": [{"baseline": "before.json", "candidate": "after.json"}],
        },
    )
    monkeypatch.setattr(regression, "run_player", _ordinary_player)
    result = regression.run_regression(profile, tmp_path / "results")
    gates = {gate.name: gate for gate in result.gates}
    assert result.execution_exit_code == 0 and gates["execution"].status == "passed"
    assert result.exit_code == code and gates["saved-ui-0"].status == expected
    if not mixed:
        assert gates["saved-ui-0"].detail["changed"][0]["fields"] == ["state"]


def compare_native_exports(directory: Path):
    """Explicit QA replay entry point; ordinary CI does not pretend native outputs exist."""
    pairs = list(directory.glob("ui04-*-before.json"))
    assert len(pairs) >= 3, "Require actual uGUI and Toolkit exported state-only pairs"
    for before in pairs:
        after = before.with_name(before.name.replace("-before.json", "-after.json"))
        result = compare_ui_snapshots(before, after)
        if "-redacted-" in before.name:
            assert result["status"] == "same" and not result["changed"]
        else:
            assert result["status"] == "changed"
            assert result["changed"] and all(
                item["fields"] == ["state"] for item in result["changed"]
            )
        assert compare_ui_snapshots(before, before)["status"] == "same"
    return len(pairs)


SDK_PROGRAM = textwrap.dedent(r"""
    import asyncio
    import copy
    import importlib
    import json
    import os
    from fastmcp import Client, FastMCP
    from mcp.types import CallToolResult
    from core.logging_decorator import log_execution
    from core.telemetry_decorator import telemetry_tool
    from services.registry import get_registered_tools
    from models.response_limits import response_size, bounded_json_text, MAX_RESPONSE_BYTES, MAX_RESPONSE_DEPTH, MAX_RESPONSE_NODES

    source = json.load(open(os.environ["UI04_SNAPSHOT_JSON"], encoding="utf-8-sig"))
    native = {"success": True, "data": source}
    module = importlib.import_module("services.tools.capture_ui_snapshot")
    sent = []
    async def ready(*args, **kwargs): return None
    async def send(*args, **kwargs):
        sent.append(args)
        return copy.deepcopy(native)
    module.preflight = ready
    module.get_unity_instance_from_context = ready
    module.send_with_unity_instance = send
    server = FastMCP("ui04-state-privacy")
    metadata = next(item for item in get_registered_tools() if item["name"] == "capture_ui_snapshot")
    server.tool(name="capture_ui_snapshot", description=metadata["description"], **metadata["kwargs"])(telemetry_tool("capture_ui_snapshot")(
        log_execution("capture_ui_snapshot", "Tool")(module.capture_ui_snapshot)))

    async def main():
        global native
        for protocol in ("2026-07-28", "legacy"):
            async with Client(server, mode=protocol) as client:
                tool = next(t for t in await client.list_tools() if t.name == "capture_ui_snapshot")
                assert "schema version 2" in tool.description
                native = {"success": True, "data": source}
                result = await client.call_tool("capture_ui_snapshot", {"framework": source["framework"], "target": "123"})
                assert result.structured_content == native
                wire = CallToolResult(content=result.content, structuredContent=result.structured_content,
                    isError=result.is_error, _meta=result.meta).model_dump(mode="json", exclude_none=True)
                text = json.dumps(wire, ensure_ascii=False)
                assert response_size(wire) is not None
                assert bounded_json_text(text, max_bytes=MAX_RESPONSE_BYTES, max_depth=MAX_RESPONSE_DEPTH, max_nodes=MAX_RESPONSE_NODES) is not None
                print("UI04_SDK_WIRE", protocol, len(text.encode("utf-8")))
                for invalid in (True, 0, 1001):
                    before = len(sent)
                    try: await client.call_tool("capture_ui_snapshot", {"framework": source["framework"], "target": "123", "max_nodes": invalid})
                    except Exception: pass
                    else: raise AssertionError("invalid budget accepted")
                    assert len(sent) == before
                for legacy in (True, False):
                    value = copy.deepcopy(source)
                    if legacy: value["schema_version"] = 1
                    else:
                        value["nodes"][0]["state"] = {"input": {"redacted": True, "read_only": False, "password": True}}
                        value["nodes"][0]["text"] = "UI04_PRIVATE_SENTINEL_7a29"
                    native = {"success": True, "data": value}
                    failure = await client.call_tool("capture_ui_snapshot", {"framework": source["framework"], "target": "123"})
                    assert failure.structured_content["success"] is False
                    assert "UI04_PRIVATE_SENTINEL_7a29" not in str(failure)
        print("UI04 SDK contracts passed")
    asyncio.run(main())
""")


def replay_snapshot_sdk(snapshot_path: Path, temporary: Path):
    """Same actual-SDK path for synthetic tests and explicit native QA artifact replay."""
    temporary.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-B", "-c", SDK_PROGRAM],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "UI04_SNAPSHOT_JSON": str(snapshot_path),
            "APPDATA": str(temporary),
            "XDG_DATA_HOME": str(temporary),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI04 SDK contracts passed" in result.stdout
    return [line for line in result.stdout.splitlines() if line.startswith("UI04_SDK_WIRE")]


def test_actual_sdk_v2_publication_and_privacy_refusals(tmp_path):
    source = save(tmp_path / "synthetic-v2.json", snapshot_v2())
    assert len(replay_snapshot_sdk(source, tmp_path / "sdk")) == 2
