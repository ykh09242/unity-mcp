"""CLI test exports preserve actual chunk bytes and refuse incomplete artifacts."""

import hashlib
import importlib
import json

from click.testing import CliRunner
import pytest

from cli.utils.config import CLIConfig


@pytest.mark.parametrize("format", ["json", "junit"])
def test_cli_export_streams_public_command_chunks(tmp_path, monkeypatch, format):
    editor = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(editor, "get_config", lambda: CLIConfig(format="json"))
    text = (
        json.dumps({"message": '한국어<&>\nquoted"'}, ensure_ascii=False)
        if format == "json"
        else '<testsuite><testcase name="한국어"/></testsuite>'
    )
    content = text.encode("utf-8")
    calls = []

    def send(command, params, config, **kwargs):
        assert command == "export_test_job"
        calls.append(params)
        first = params.get("cursor") is None
        return {
            "success": True,
            "data": {
                "content": text[:10] if first else text[10:],
                "next_cursor": "next" if first else None,
                "has_more": first,
                "generation": "g",
                "sha256": hashlib.sha256(content).hexdigest(),
                "total_bytes": len(content),
            },
        }

    monkeypatch.setattr(editor, "run_command", send)
    output = tmp_path / ("test results." + format)
    result = CliRunner().invoke(
        editor.editor, ["export-test", "job", "--format", format, "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == content
    assert len(calls) == 2


def test_cli_export_bad_digest_leaves_no_final_or_temporary_file(tmp_path, monkeypatch):
    editor = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(editor, "get_config", lambda: CLIConfig(format="json"))
    monkeypatch.setattr(
        editor,
        "run_command",
        lambda *a, **kw: {
            "success": True,
            "data": {
                "content": "payload",
                "next_cursor": None,
                "has_more": False,
                "generation": "g",
                "sha256": "0" * 64,
                "total_bytes": 7,
            },
        },
    )
    output = tmp_path / "results.json"
    result = CliRunner().invoke(editor.editor, ["export-test", "job", "--output", str(output)])
    assert result.exit_code != 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "failure",
    ["changed_generation", "changed_digest", "changed_length", "cursor_cycle", "native_error"],
)
def test_cli_export_rejects_midstream_change_and_removes_partial(tmp_path, monkeypatch, failure):
    editor = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(editor, "get_config", lambda: CLIConfig(format="json"))
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        data = {
            "content": "a",
            "generation": "g",
            "sha256": hashlib.sha256(b"aa").hexdigest(),
            "total_bytes": 2,
            "has_more": len(calls) == 1,
            "next_cursor": "next" if len(calls) == 1 else None,
        }
        if len(calls) > 1:
            if failure == "native_error":
                return {"success": False, "error": "unavailable"}
            if failure == "changed_generation":
                data["generation"] = "different"
            if failure == "changed_digest":
                data["sha256"] = "0" * 64
            if failure == "changed_length":
                data["total_bytes"] = 3
            if failure == "cursor_cycle":
                data.update(has_more=True, next_cursor="next")
        return {"success": True, "data": data}

    monkeypatch.setattr(editor, "run_command", send)
    result = CliRunner().invoke(
        editor.editor, ["export-test", "job", "--output", str(tmp_path / "result.json")]
    )
    assert result.exit_code == 1
    assert len(calls) == 2
    assert not list(tmp_path.iterdir())


def test_cli_export_preserves_existing_destination_without_dispatch(tmp_path, monkeypatch):
    editor = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(editor, "get_config", lambda: CLIConfig(format="json"))
    monkeypatch.setattr(
        editor,
        "run_command",
        lambda *args: pytest.fail("Existing output must be rejected before dispatch"),
    )
    output = tmp_path / "existing.json"
    output.write_bytes(b"original")
    result = CliRunner().invoke(editor.editor, ["export-test", "job", "--output", str(output)])
    assert result.exit_code == 1
    assert output.read_bytes() == b"original"


@pytest.mark.parametrize("path", ["con", ".env.export"])
def test_cli_export_rejects_protected_paths_before_dispatch(tmp_path, monkeypatch, path):
    editor = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(editor, "get_config", lambda: CLIConfig(format="json"))
    monkeypatch.setattr(
        editor,
        "run_command",
        lambda *args: pytest.fail("Unsafe output must be rejected before dispatch"),
    )
    result = CliRunner().invoke(
        editor.editor, ["export-test", "job", "--output", str(tmp_path / path)]
    )
    assert result.exit_code == 1
    assert not list(tmp_path.iterdir())
