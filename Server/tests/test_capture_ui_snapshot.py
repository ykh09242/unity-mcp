"""Capture request validation and explicit new-file persistence without a live Editor."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_ui import capture_ui_snapshot as capture_command
from services.tools.capture_ui_snapshot import capture_ui_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,max_nodes", [("", 10), (True, 10), ("Canvas", True), ("Canvas", 1001)]
)
async def test_invalid_request_stays_local_when_capture_parameters_fail(
    monkeypatch: pytest.MonkeyPatch, target, max_nodes
) -> None:
    # Given
    transport = AsyncMock()
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", transport)
    # When
    result = await capture_ui_snapshot(SimpleNamespace(), "ugui", target, max_nodes)
    # Then
    assert result["success"] is False
    transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_dispatches_one_read_when_request_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    transport = AsyncMock(return_value={"success": True, "data": {"complete": True}})
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", transport)
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value="Editor@fixture"),
    )
    # When
    result = await capture_ui_snapshot(SimpleNamespace(), "uitoolkit", "Canvas/Menu", 17)
    # Then
    assert result["success"] is True
    assert transport.await_count == 1
    assert transport.await_args.args[1:] == (
        "Editor@fixture",
        "capture_ui_snapshot",
        {"framework": "uitoolkit", "target": "Canvas/Menu", "max_nodes": 17},
    )


def test_existing_output_blocks_capture_before_network_when_file_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    def transport_forbidden(*_args, **_kwargs):
        pytest.fail("Existing output should prevent native capture")

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", transport_forbidden)
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    path = tmp_path / "snapshot.json"
    path.write_text("keep", encoding="utf-8")
    # When
    result = CliRunner().invoke(
        capture_command, ["--framework", "ugui", "--target", "Canvas", "--output", str(path)]
    )
    # Then
    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == "keep"


def test_incomplete_native_evidence_creates_no_artifact_when_capture_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.run_command",
        lambda *_args: {"result": {"success": True, "data": {"complete": False}}},
    )
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    path = tmp_path / "snapshot.json"
    # When
    result = CliRunner().invoke(
        capture_command, ["--framework", "ugui", "--target", "Canvas", "--output", str(path)]
    )
    # Then
    assert result.exit_code != 0
    assert not path.exists()
