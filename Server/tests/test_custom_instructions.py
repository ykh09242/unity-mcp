"""Bounded startup instruction snapshots and the public CLI integration."""

import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.custom_instructions import (
    MAX_INSTRUCTIONS_BYTES,
    PROJECT_INSTRUCTIONS_HEADING,
    CustomInstructionsError,
    append_custom_instructions,
    load_custom_instructions,
)


def test_omitted_file_keeps_instructions_identical_and_does_not_read(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Default instructions must not access the filesystem")

    monkeypatch.setattr(Path, "stat", deny)
    monkeypatch.setattr(Path, "open", deny)
    base = "Built-in guidance.\n"
    assert load_custom_instructions(None) is None
    assert append_custom_instructions(base, None) is base


def test_unicode_text_is_preserved_in_labeled_appendix(tmp_path):
    path = tmp_path / "project-guidance.txt"
    text = "  프로젝트 지침\r\nUse the existing test scene.\n"
    path.write_bytes(text.encode("utf-8"))
    loaded = load_custom_instructions(path)
    assert loaded == text
    combined = append_custom_instructions("Original guidance.", loaded)
    assert combined.startswith("Original guidance.\n\n" + PROJECT_INSTRUCTIONS_HEADING)
    assert combined.endswith(text)


@pytest.mark.parametrize("character", ["x", "é"])
def test_size_limit_counts_utf8_bytes_and_accepts_exact_boundary(tmp_path, character):
    path = tmp_path / "instructions.txt"
    text = character * (MAX_INSTRUCTIONS_BYTES // len(character.encode("utf-8")))
    path.write_bytes(text.encode("utf-8"))
    assert load_custom_instructions(path) == text
    path.write_bytes((text + character).encode("utf-8"))
    with pytest.raises(CustomInstructionsError, match="32768-byte limit"):
        load_custom_instructions(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xffprivate-content", "valid UTF-8"),
        (b"", "non-whitespace"),
        (b" \t\r\n", "non-whitespace"),
        (b"private\x00content", "NUL"),
    ],
)
def test_invalid_text_fails_without_disclosing_contents(tmp_path, payload, message):
    path = tmp_path / "private-filename.txt"
    path.write_bytes(payload)
    with pytest.raises(CustomInstructionsError, match=message) as error:
        load_custom_instructions(path)
    assert "private" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_missing_or_directory_input_fails_without_disclosing_path(tmp_path):
    with pytest.raises(CustomInstructionsError, match="does not exist") as error:
        load_custom_instructions(tmp_path / "private-missing.txt")
    assert "private" not in str(error.value)
    with pytest.raises(CustomInstructionsError, match="regular file"):
        load_custom_instructions(tmp_path)


def test_nonregular_file_is_rejected_before_opening(monkeypatch):
    monkeypatch.setattr(Path, "stat", lambda self: SimpleNamespace(st_mode=stat.S_IFIFO))
    opened = MagicMock()
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(CustomInstructionsError, match="regular file"):
        load_custom_instructions("synthetic-pipe")
    opened.assert_not_called()


def test_read_failure_does_not_disclose_os_error_or_path(tmp_path, monkeypatch):
    path = tmp_path / "private-guidance.txt"
    path.write_text("Guidance", encoding="utf-8")
    monkeypatch.setattr(Path, "open", MagicMock(side_effect=PermissionError("private details")))
    with pytest.raises(CustomInstructionsError, match="could not be read") as error:
        load_custom_instructions(path)
    assert "private" not in str(error.value)


def test_read_is_bounded_and_snapshot_is_independent_of_later_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "instructions.txt"
    path.write_text("Before", encoding="utf-8")
    stream = io.BytesIO(b"Snapshot")
    read = MagicMock(wraps=stream.read)
    monkeypatch.setattr(stream, "read", read)
    opened = MagicMock(return_value=stream)
    with monkeypatch.context() as context:
        context.setattr(Path, "open", opened)
        loaded = load_custom_instructions(path)
    opened.assert_called_once_with("rb")
    read.assert_called_once_with(MAX_INSTRUCTIONS_BYTES + 1)
    path.write_text("After", encoding="utf-8")
    assert append_custom_instructions("Base", loaded).endswith("Snapshot")


@pytest.fixture
def entry(tmp_path, monkeypatch):
    # Import the real entry point with log output confined to the test directory.
    monkeypatch.setenv("UNITY_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("UNITY_MCP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "8080")
    import main

    return main


def test_cli_invalid_file_exits_before_server_creation_or_pidfile_write(
    entry, tmp_path, monkeypatch, capsys
):
    missing = tmp_path / "private-missing.txt"
    pidfile = tmp_path / "should-not-exist.pid"
    monkeypatch.setattr(
        sys,
        "argv",
        ["mcp-for-unity", "--instructions-file", str(missing), "--pidfile", str(pidfile)],
    )
    create = MagicMock()
    monkeypatch.setattr(entry, "create_mcp_server", create)
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
    create.assert_not_called()
    assert not pidfile.exists()
    output = capsys.readouterr()
    assert "Instructions file does not exist" in output.err
    assert str(missing) not in output.err


@pytest.mark.parametrize("custom", [None, "Project guidance: use the test scene.\n"])
def test_cli_passes_startup_snapshot_without_rereading_or_logging_it(
    entry, tmp_path, monkeypatch, capsys, caplog, custom
):
    arguments = ["mcp-for-unity", "--project-scoped-tools"]
    if custom is not None:
        path = tmp_path / "private-guidance.txt"
        path.write_bytes(custom.encode("utf-8"))
        arguments.extend(["--instructions-file", str(path)])
    monkeypatch.setattr(sys, "argv", arguments)
    loader = MagicMock(wraps=entry.load_custom_instructions)
    monkeypatch.setattr(entry, "load_custom_instructions", loader)
    server = MagicMock()
    create = MagicMock(return_value=server)
    monkeypatch.setattr(entry, "create_mcp_server", create)
    entry.main()
    loader.assert_called_once()
    assert create.call_args.kwargs["custom_instructions"] == custom
    server.run.assert_called_once_with(transport="stdio")
    output = capsys.readouterr()
    captured = output.out + output.err + caplog.text
    assert "private-guidance" not in captured
    if custom is not None:
        assert custom not in captured


def test_server_appends_guidance_to_actual_fastmcp_instructions(entry, monkeypatch):
    monkeypatch.setattr(entry, "custom_tool_service", entry.custom_tool_service)
    monkeypatch.setattr(entry.CustomToolService, "_instance", entry.CustomToolService._instance)
    monkeypatch.setattr(entry, "register_all_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry, "register_all_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry.config, "http_remote_hosted", False)
    baseline = entry._build_instructions(False)
    default = entry.create_mcp_server(False)
    custom = entry.create_mcp_server(False, custom_instructions="Project guidance.")
    assert default.instructions == baseline
    assert custom.instructions == append_custom_instructions(baseline, "Project guidance.")
