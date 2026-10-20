"""Exports never follow links or leave a failed overwrite as mixed evidence."""

import os
from pathlib import Path

import pytest
from cli.main import cli
from cli.utils.play_scenario_reports import write_suite_artifacts, write_player_artifacts
from click.testing import CliRunner

REPORT = {"suite": {"name": "new"}, "status": "succeeded", "scenarios": []}


def test_second_write_failure_preserves_previous_complete_pair(tmp_path, monkeypatch):
    json_path, xml_path = tmp_path / "suite.json", tmp_path / "junit.xml"
    json_path.write_bytes(b"previous JSON")
    xml_path.write_bytes(b"previous XML")
    write = Path.write_bytes

    def fail_xml(path, data):
        if path.name == "junit.xml":
            raise OSError("disk full")
        return write(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_xml)
    with pytest.raises(OSError, match="disk full"):
        write_suite_artifacts(REPORT, tmp_path)
    assert json_path.read_bytes() == b"previous JSON"
    assert xml_path.read_bytes() == b"previous XML"


def test_nonregular_final_entry_refused_before_changing_previous_report(tmp_path):
    json_path = tmp_path / "suite.json"
    json_path.write_bytes(b"previous JSON")
    (tmp_path / "junit.xml").mkdir()
    with pytest.raises((OSError, ValueError)):
        write_suite_artifacts(REPORT, tmp_path)
    assert json_path.read_bytes() == b"previous JSON"


def test_linked_final_entry_cannot_overwrite_external_file(tmp_path):
    external = tmp_path / "outside.xml"
    external.write_bytes(b"external")
    output = tmp_path / "output"
    output.mkdir()
    try:
        (output / "junit.xml").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises((OSError, ValueError)):
        write_suite_artifacts(REPORT, output)
    assert external.read_bytes() == b"external"
    assert not (output / "suite.json").exists()


def test_second_publish_failure_rolls_back_first_file(tmp_path, monkeypatch):
    (tmp_path / "suite.json").write_bytes(b"old JSON")
    (tmp_path / "junit.xml").write_bytes(b"old XML")
    replace = os.replace
    failed = False

    def fail_second(source, target):
        nonlocal failed
        if Path(target) == tmp_path / "junit.xml" and not failed:
            failed = True
            raise OSError("publish failed")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="publish failed"):
        write_suite_artifacts(REPORT, tmp_path)
    assert (tmp_path / "suite.json").read_bytes() == b"old JSON"
    assert (tmp_path / "junit.xml").read_bytes() == b"old XML"


def test_suite_output_is_preflighted_before_native_execution(tmp_path, monkeypatch):
    import cli.commands.play_scenario as commands

    (tmp_path / "junit.xml").mkdir()
    calls = []
    monkeypatch.setattr(commands, "wait_for_suite", lambda *a, **kw: calls.append(a) or REPORT)
    result = CliRunner().invoke(
        cli,
        [
            "--instance",
            "Owned@fixture",
            "play-scenario",
            "suite-run",
            "smoke",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert calls == []


def test_player_export_preserves_exact_native_bytes(tmp_path):
    original = b'{"status":"succeeded", "native":"exact bytes"}\n'
    (tmp_path / "run.json").write_bytes(original)
    json_path, xml_path = write_player_artifacts({"status": "succeeded"}, tmp_path)
    assert json_path.read_bytes() == original
    assert xml_path.read_bytes().startswith(b"<?xml")
