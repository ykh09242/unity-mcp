"""Explicit per-capture identities preserve strict evidence and actual publication."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_ui import capture_ui_snapshot as capture_command
from cli.utils.play_scenario_ui import compare_ui_snapshots
from models import ui_snapshots as contracts
from services.tools.capture_ui_snapshot import UiSnapshotRequest, capture_ui_snapshot
from .test_play_scenario_ui import save
from .test_ui_snapshot_control_state import snapshot_v2

SECRET = "UI05_PRIVATE_SENTINEL_512f"


def snapshot_v3():
    value = snapshot_v2()
    value["schema_version"] = 3
    value["evidence"]["identity_method"] = "explicit_path_map_v1"
    value["nodes"][1]["id"] = value["target"] + "/@id:item-A"
    return value


def mapping():
    return [{"element_path": "/0", "id": "item-A"}]


def character_map(characters):
    entries = [
        {"element_path": "/999" * 126 + "/" + str(i), "id": f"i{i:03d}_" + "x" * 123}
        for i in range(104)
    ]
    excess = sum(len(e["element_path"]) + len(e["id"]) for e in entries) - characters
    assert excess >= 0
    for entry in entries:
        remove = min(excess, len(entry["id"]) - 5)
        entry["id"] = entry["id"][: len(entry["id"]) - remove]
        excess -= remove
    assert excess == 0
    return entries


def test_exact_versions_and_input_privacy_remain_strict():
    value = snapshot_v3()
    assert contracts.validate_ui_snapshot(value, expected_version=3).schema_version == 3
    assert contracts.validate_ui_snapshot(snapshot_v2(), expected_version=2).schema_version == 2
    for change in ("missing_method", "wrong_method", "input_text", "unknown", "boolean_version"):
        invalid = deepcopy(value)
        if change == "missing_method":
            invalid["evidence"].pop("identity_method")
        elif change == "wrong_method":
            invalid["evidence"]["identity_method"] = "guessed"
        elif change == "input_text":
            invalid["nodes"][0].update(
                text=SECRET,
                state={"input": {"redacted": True, "read_only": False, "password": True}},
            )
        elif change == "unknown":
            invalid["evidence"]["arbitrary"] = SECRET
        else:
            invalid["schema_version"] = True
        with pytest.raises(ValueError) as error:
            contracts.validate_ui_snapshot(invalid)
        assert SECRET not in str(error.value)
    with pytest.raises(ValueError):
        contracts.validate_ui_snapshot(value, expected_version=2)
    with pytest.raises(ValueError):
        contracts.validate_ui_snapshot(snapshot_v2(), expected_version=3)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        [None],
        [{"element_path": "", "id": "A"}],
        [{"element_path": "/00", "id": "A"}],
        [{"element_path": "/0/", "id": "A"}],
        [{"element_path": "/1000", "id": "A"}],
        [{"element_path": "/0" * 128, "id": "A"}],
        [{"element_path": "/0", "id": ""}],
        [{"element_path": "/0", "id": "A B"}],
        [{"element_path": "/0", "id": "A/B"}],
        [{"element_path": "/0", "id": "한"}],
        [{"element_path": "/0", "id": "x" * 129}],
        [{"element_path": "/0", "id": True}],
        [{"element_path": "/0", "id": "A", "extra": 1}],
        [{"element_path": "/0", "id": "A"}, {"element_path": "/0", "id": "B"}],
        [{"element_path": "/0", "id": "A"}, {"element_path": "/1", "id": "A"}],
    ],
)
def test_invalid_identity_maps_reject_before_dispatch(value):
    with pytest.raises(ValueError):
        UiSnapshotRequest(framework="ugui", target="Canvas", identity_map=value)


@pytest.mark.parametrize("count,success", [(999, True), (1000, False)])
def test_assignment_cap_is_exact(count, success):
    values = [{"element_path": f"/{i}", "id": f"item{i}"} for i in range(count)]
    if success:
        assert (
            len(
                UiSnapshotRequest(
                    framework="ugui", target="Canvas", max_nodes=1000, identity_map=values
                ).identity_map
            )
            == count
        )
    else:
        with pytest.raises(ValueError):
            UiSnapshotRequest(
                framework="ugui", target="Canvas", max_nodes=1000, identity_map=values
            )


@pytest.mark.parametrize("characters,success", [(65536, True), (65537, False)])
def test_shared_character_budget_is_exact(characters, success):
    values = character_map(characters)
    if success:
        UiSnapshotRequest(framework="ugui", target="Canvas", max_nodes=1000, identity_map=values)
    else:
        with pytest.raises(ValueError):
            UiSnapshotRequest(
                framework="ugui", target="Canvas", max_nodes=1000, identity_map=values
            )


def test_map_cannot_exceed_non_root_node_budget():
    with pytest.raises(ValueError):
        UiSnapshotRequest(framework="ugui", target="Canvas", max_nodes=1, identity_map=mapping())


@pytest.mark.asyncio
async def test_mapped_dispatch_requires_v3_and_default_omits_map(monkeypatch):
    transport = AsyncMock(return_value={"success": True, "data": snapshot_v3()})
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.preflight", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", transport)
    result = await capture_ui_snapshot(SimpleNamespace(), "ugui", "Canvas", identity_map=mapping())
    assert result["success"] is True
    assert transport.await_args.args[3]["identity_map"] == mapping()
    transport.return_value = {"success": True, "data": snapshot_v2()}
    rejected = await capture_ui_snapshot(
        SimpleNamespace(), "ugui", "Canvas", identity_map=mapping()
    )
    assert rejected["success"] is False
    default = await capture_ui_snapshot(SimpleNamespace(), "ugui", "Canvas")
    assert default["success"] is True and "identity_map" not in transport.await_args.args[3]


def test_cli_map_file_never_saves_ignored_v2_or_overwrites_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    calls = []

    def send(*args):
        calls.append(args)
        return {"success": True, "data": snapshot_v2()}

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", send)
    source = save(tmp_path / "map.json", {"identity_map": mapping()})
    output = tmp_path / "snapshot.json"
    args = [
        "--framework",
        "ugui",
        "--target",
        "Canvas",
        "--identity-map-file",
        str(source),
        "--output",
        str(output),
    ]
    rejected = CliRunner().invoke(capture_command, args)
    assert rejected.exit_code != 0 and not output.exists()
    assert len(calls) == 1 and calls[0][1]["identity_map"] == mapping()
    output.write_text("keep")
    calls.clear()
    refused = CliRunner().invoke(capture_command, args)
    assert refused.exit_code != 0 and not calls and output.read_text() == "keep"


@pytest.mark.parametrize(
    "text",
    [
        '{"identity_map":null}',
        '{"identity_map":[],"extra":1}',
        '{"identity_map":[],"identity_map":[]}',
        " " * (256 * 1024 + 1),
        "[" * 2000 + "]" * 2000,
    ],
    ids=["null", "extra", "duplicate", "oversized", "deep"],
)
def test_cli_invalid_map_file_stays_local(tmp_path, monkeypatch, text):
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )

    def forbidden(*args):
        pytest.fail("Invalid map must not dispatch")

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", forbidden)
    source = tmp_path / "map.json"
    source.write_text(text)
    output = tmp_path / "snapshot.json"
    result = CliRunner().invoke(
        capture_command,
        [
            "--framework",
            "ugui",
            "--target",
            "Canvas",
            "--identity-map-file",
            str(source),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0 and not output.exists()


def test_real_comparator_matches_ids_and_preserves_order_state_and_rebind(tmp_path):
    before = snapshot_v3()
    other = deepcopy(before["nodes"][1])
    other["id"] = before["target"] + "/@id:item-B"
    other["sibling_index"] = 1
    before["nodes"].append(other)
    after = deepcopy(before)
    after["nodes"][1]["sibling_index"] = 1
    after["nodes"][2]["sibling_index"] = 0
    left = save(tmp_path / "before.json", before)
    right = save(tmp_path / "after.json", after)
    result = compare_ui_snapshots(left, right)
    assert not result["added"] and not result["removed"]
    assert all(c["fields"] == ["sibling_index"] for c in result["changed"])
    assert compare_ui_snapshots(left, left)["status"] == "same"
    rebound = deepcopy(before)
    rebound["nodes"][1]["id"] = before["target"] + "/@id:item-C"
    result = compare_ui_snapshots(left, save(tmp_path / "rebound.json", rebound))
    assert result["added"] == [before["target"] + "/@id:item-C"]
    assert result["removed"] == [before["target"] + "/@id:item-A"]
    with pytest.raises(ValueError):
        compare_ui_snapshots(left, save(tmp_path / "v2.json", snapshot_v2()))


SDK_PROGRAM = r"""
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

source = json.load(open(os.environ["UI05_SNAPSHOT_JSON"], encoding="utf-8-sig"))
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
server = FastMCP("ui05-explicit-identity")
metadata = next(item for item in get_registered_tools() if item["name"] == "capture_ui_snapshot")
server.tool(name="capture_ui_snapshot", description=metadata["description"], **metadata["kwargs"])(telemetry_tool("capture_ui_snapshot")(
    log_execution("capture_ui_snapshot", "Tool")(module.capture_ui_snapshot)))

async def main():
    global native
    request = {"framework": source["framework"], "target": "123", "identity_map": [{"element_path": "/0", "id": "item-A"}]}
    leaked = []
    for protocol in ("2026-07-28", "legacy"):
        async with Client(server, mode=protocol) as client:
            tool = next(t for t in await client.list_tools() if t.name == "capture_ui_snapshot")
            assert "schema version 3" in tool.description
            schema = tool.inputSchema
            field = schema["properties"]["identity_map"]
            assert "identity_map" not in schema.get("required", [])
            assert field["type"] == "array" and field["minItems"] == 1 and field["maxItems"] == 999
            assert "nonsecret" in field["description"]
            item = field["items"]
            if "$ref" in item: item = schema["$defs"][item["$ref"].rsplit("/", 1)[-1]]
            assert "Current" in item["properties"]["element_path"]["description"]
            assert "nonsecret" in item["properties"]["id"]["description"]
            native = {"success": True, "data": source}
            result = await client.call_tool("capture_ui_snapshot", request)
            assert result.structured_content == native
            assert sent[-1][3]["identity_map"] == request["identity_map"]
            wire = CallToolResult(content=result.content, structuredContent=result.structured_content,
                isError=result.is_error, _meta=result.meta).model_dump(mode="json", exclude_none=True)
            text = json.dumps(wire, ensure_ascii=False)
            assert response_size(wire) is not None
            assert bounded_json_text(text, max_bytes=MAX_RESPONSE_BYTES, max_depth=MAX_RESPONSE_DEPTH, max_nodes=MAX_RESPONSE_NODES) is not None
            print("UI05_SDK_WIRE", protocol, len(text.encode("utf-8")))
            for invalid in (None, [], [{"element_path": "/00", "id": "A"}], [{"element_path": "/0", "id": True}], [{"element_path": "/0", "id": "A"}, {"element_path": "/1", "id": "A"}]):
                before = len(sent)
                try:
                    failure = await client.call_tool("capture_ui_snapshot", {**request, "identity_map": invalid})
                except Exception: pass
                else: assert failure.structured_content["success"] is False
                assert len(sent) == before
            for label, invalid in (("invalid-id", [{"element_path": "/0", "id": "UI05_PRIVATE_SENTINEL_512f/"}]), ("unknown-key", [{"element_path": "/0", "id": "A", "UI05_PRIVATE_SENTINEL_512f": 1}])):
                before = len(sent)
                try:
                    failure = await client.call_tool("capture_ui_snapshot", {**request, "identity_map": invalid})
                except Exception as exc: message = str(exc)
                else:
                    assert failure.structured_content["success"] is False
                    message = str(failure)
                assert len(sent) == before
                exposed = "UI05_PRIVATE_SENTINEL_512f" in message
                print("UI05_MAP_REJECTION", protocol, label, "echoed" if exposed else "constant")
                if exposed: leaked.append(protocol + ":" + label)
            old = copy.deepcopy(source)
            old["schema_version"] = 2
            old["evidence"].pop("identity_method")
            native = {"success": True, "data": old}
            failure = await client.call_tool("capture_ui_snapshot", request)
            assert failure.structured_content["success"] is False
            default = await client.call_tool("capture_ui_snapshot", {"framework": source["framework"], "target": "123"})
            assert default.structured_content["success"] is True
            assert "identity_map" not in sent[-1][3]
            private = copy.deepcopy(source)
            private["nodes"][0].update(text="UI05_PRIVATE_SENTINEL_512f", state={"input": {"redacted": True, "read_only": False, "password": True}})
            native = {"success": True, "data": private}
            failure = await client.call_tool("capture_ui_snapshot", request)
            assert failure.structured_content["success"] is False
            assert "UI05_PRIVATE_SENTINEL_512f" not in str(failure)
    assert not leaked, "SDK map validation echoed caller data: " + str(leaked)
    print("UI05 SDK contracts passed")
asyncio.run(main())
"""


def replay_snapshot_sdk(snapshot_path: Path, temporary: Path):
    """Actual installed SDK over controlled transport, also used for exact native exports."""
    import os
    import subprocess
    import sys

    temporary.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-B", "-c", SDK_PROGRAM],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "UI05_SNAPSHOT_JSON": str(snapshot_path),
            "APPDATA": str(temporary),
            "XDG_DATA_HOME": str(temporary),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI05 SDK contracts passed" in result.stdout
    lines = [line for line in result.stdout.splitlines() if line.startswith("UI05_SDK_WIRE")]
    assert len(lines) == 2
    return lines


def test_actual_sdk_map_schema_dispatch_and_version_privacy_refusals(tmp_path):
    source = save(tmp_path / "v3.json", snapshot_v3())
    assert len(replay_snapshot_sdk(source, tmp_path / "sdk")) == 2


@pytest.mark.parametrize("mixed,expected,code", [(False, "failed", 1), (True, "unavailable", 2)])
def test_saved_v3_gate_keeps_state_failure_and_mixed_unavailable(
    tmp_path, monkeypatch, mixed, expected, code
):
    import socket
    import subprocess
    from cli.utils import play_scenario_regression as regression
    from .test_play_scenario_regression_gates import _ordinary_player

    def forbidden(*args, **kwargs):
        pytest.fail("Saved UI gate attempted external execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before, after = snapshot_v3(), snapshot_v3()
    before["nodes"][1]["state"] = {"toggle": {"value": False}}
    after["nodes"][1]["state"] = {"toggle": {"value": True}}
    if mixed:
        before = snapshot_v2()
    save(tmp_path / "before.json", before)
    save(tmp_path / "after.json", after)
    source = save(
        tmp_path / "profile.json",
        {
            "schema_version": 1,
            "name": "Explicit UI gate",
            "execution": {"build_directory": "build"},
            "analyze": False,
            "ui_checks": [{"baseline": "before.json", "candidate": "after.json"}],
        },
    )
    monkeypatch.setattr(regression, "run_player", _ordinary_player)
    result = regression.run_regression(source, tmp_path / "results")
    gates = {gate.name: gate for gate in result.gates}
    assert result.execution_exit_code == 0 and gates["execution"].status == "passed"
    assert result.exit_code == code and gates["saved-ui-0"].status == expected
    if not mixed:
        assert gates["saved-ui-0"].detail["changed"][0]["fields"] == ["state"]


def test_cli_invalid_assignment_never_echoes_unknown_field_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )

    def forbidden(*args):
        pytest.fail("Invalid assignment must not dispatch")

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", forbidden)
    source = save(
        tmp_path / "map.json", {"identity_map": [{"element_path": "/0", "id": "A", SECRET: 1}]}
    )
    result = CliRunner().invoke(
        capture_command,
        [
            "--framework",
            "ugui",
            "--target",
            "Canvas",
            "--identity-map-file",
            str(source),
            "--output",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code != 0
    assert SECRET not in result.output
    assert len(result.output) < 300
