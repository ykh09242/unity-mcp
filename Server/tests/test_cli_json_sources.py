"""Explicit JSON source admission is local, bounded and shared by real CLI callbacks."""

import copy
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import textwrap

import httpx
import click
import pytest
from click.testing import CliRunner


# One case for each declared JSON boundary, including inputs normalized by private helpers.
BOUNDARIES = [
    (("raw", "owned_tool"), None, {}),
    (
        ("animation", "clip", "add-curve", "Assets/Owned.anim", "--property", "localPosition.x"),
        "keys",
        [],
    ),
    (
        ("animation", "clip", "set-curve", "Assets/Owned.anim", "--property", "localPosition.x"),
        "keys",
        [],
    ),
    (
        (
            "animation",
            "clip",
            "set-vector-curve",
            "Assets/Owned.anim",
            "--property",
            "localPosition",
        ),
        "keys",
        [],
    ),
    (
        ("animation", "controller", "add-transition", "Assets/Owned.controller", "Idle", "Walk"),
        "conditions",
        [],
    ),
    (("animation", "raw", "owned_action"), "params", {}),
    (("asset", "create", "Assets/Owned.mat", "Material"), "properties", {}),
    (("batch", "inline"), None, []),
    (("camera", "set-body", "Owned"), "props", {}),
    (("camera", "set-aim", "Owned"), "props", {}),
    (("camera", "add-extension", "Owned", "CinemachineImpulseListener"), "props", {}),
    (("component", "add", "Owned", "Fixture"), "properties", {}),
    (("component", "modify", "Owned", "Fixture"), "properties", {}),
    (("editor", "custom-tool", "Owned"), "params", {}),
    (("material", "create", "Assets/Owned.mat"), "properties", {}),
    (("prefab", "modify", "Assets/Owned.prefab"), "create-child", {}),
    (("probuilder", "create-shape", "Cube"), "params", {}),
    (("probuilder", "create-poly"), "points", []),
    (("probuilder", "extrude-faces", "Owned"), "faces", []),
    (("probuilder", "extrude-edges", "Owned"), "edges", []),
    (("probuilder", "bevel-edges", "Owned"), "edges", []),
    (("probuilder", "delete-faces", "Owned"), "faces", []),
    (("probuilder", "subdivide", "Owned"), "faces", []),
    (("probuilder", "select-faces", "Owned"), "grow-from", []),
    (("probuilder", "move-vertices", "Owned", "--offset", "1", "2", "3"), "vertices", []),
    (("probuilder", "weld-vertices", "Owned"), "vertices", []),
    (("probuilder", "set-material", "Owned", "--material", "Assets/Owned.mat"), "faces", []),
    (("probuilder", "set-smoothing", "Owned", "--group", "1"), "faces", []),
    (("probuilder", "raw", "owned_action"), "params", {}),
    (("script", "edit", "Assets/Owned.cs"), "edits", []),
    (("sprite", "setup-clips", "Assets/Owned.png"), "clips", []),
    (("sprite", "setup-controller", "Assets/Owned.controller"), "clips", []),
    (("sprite", "full-setup", "Assets/Owned.png", "--cols", "1"), "clips", []),
    (("texture", "create", "Assets/Owned.png"), "palette", []),
    (("texture", "create", "Assets/Owned.png"), "import-settings", {}),
    (("texture", "sprite", "Assets/Owned.png"), "pivot", [0.5, 0.5]),
    (("texture", "modify", "Assets/Owned.png"), "set-pixels", {"color": [255, 0, 0]}),
    (("vfx", "line", "set-positions", "Owned"), "positions", []),
    (("vfx", "raw", "owned_action", "Owned"), "params", {}),
    (("physics", "set-settings", "gravity"), None, [0, -9.81, 0]),
]


@pytest.fixture
def admission(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Network and token access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    calls = []

    def capture(tool, params, config):
        calls.append((tool, copy.deepcopy(params), config.unity_instance))
        return {"success": True, "data": {"sha256": "a" * 64}}

    main = importlib.import_module("cli.main")
    monkeypatch.setattr(main, "run_command", capture)
    for module in {command[0] for command, _, _ in BOUNDARIES if command[0] != "raw"}:
        monkeypatch.setattr(
            importlib.import_module(f"cli.commands.{module}"), "run_command", capture
        )
    return main.cli, calls


def invoke(cli, command, flag, value, *, input=None):
    args = ["--format", "json", "--instance", "Owned@fixture", *command]
    if flag:
        args.append(f"--{flag}")
    args.append(value)
    return CliRunner().invoke(cli, args, input=input)


@pytest.mark.parametrize("command,flag,payload", BOUNDARIES)
def test_every_json_boundary_accepts_file_without_changing_dispatch(
    admission, tmp_path, command, flag, payload
):
    cli, calls = admission
    assert len(BOUNDARIES) == 40
    inline = invoke(cli, command, flag, json.dumps(payload))
    assert inline.exit_code == 0, inline.output
    expected = list(calls)
    calls.clear()
    path = tmp_path / "입력 with spaces.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    external = invoke(cli, command, flag, "@" + str(path))
    assert external.exit_code == 0, external.output
    assert calls == expected


@pytest.mark.parametrize("source", ["file", "@-", "-"])
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("consumer", ["raw", "script"])
def test_unicode_quotes_newlines_preserve_payload_and_script_sha(
    admission, tmp_path, source, bom, consumer
):
    cli, calls = admission
    text = '한글 "quoted"\nnext\r\n@literal'
    payload = (
        {"text": text}
        if consumer == "raw"
        else [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 2, "newText": text}]
    )
    encoded = (b"\xef\xbb\xbf" if bom else b"") + json.dumps(payload, ensure_ascii=False).encode(
        "utf-8"
    )
    path = tmp_path / "한글 edits.json"
    path.write_bytes(encoded)
    value = "@" + str(path) if source == "file" else source
    command, flag = (
        (("raw", "owned_tool"), None)
        if consumer == "raw"
        else (("script", "edit", "Assets/Owned.cs"), "edits")
    )
    result = invoke(cli, command, flag, value, input=encoded if source != "file" else None)
    assert result.exit_code == 0, result.output
    if consumer == "raw":
        assert calls == [("owned_tool", payload, "Owned@fixture")]
    else:
        assert len(calls) == 2
        assert calls[0][1]["action"] == "get_sha"
        assert calls[1][1]["edits"] == payload
        assert calls[1][1]["precondition_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        b'{"secret":"PRIVATE_MARKER",',
        b'{"secret":"PRIVATE_MARKER","x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":1e999}',
        b'{"x":"\xff"}',
        '{"secret":"PRIVATE_MARKER"}'.encode("utf-16"),
    ],
)
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_invalid_external_input_never_dispatches_or_echoes_payload(
    admission, tmp_path, payload, source
):
    cli, calls = admission
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    result = invoke(
        cli,
        ("raw", "owned_tool"),
        None,
        "@" + str(path) if source == "file" else "@-",
        input=payload,
    )
    assert result.exit_code == 1
    assert result.stdout == "" and result.stderr
    assert "PRIVATE_MARKER" not in result.stderr
    assert calls == []


def test_all_stdin_conflicts_fail_before_any_input_read(admission, monkeypatch, tmp_path):
    cli, calls = admission
    import cli.utils.parsers as parsers

    monkeypatch.setattr(
        parsers.click, "open_file", lambda *_a, **_kw: pytest.fail("Conflict read input")
    )
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--palette",
            "@-",
            "--import-settings",
            "-",
        ],
    )
    assert result.exit_code == 1 and result.stderr
    assert calls == []
    for sources in (("@-", "@file.json"), ("@file.json", "{}"), ("-", "@-")):
        result = CliRunner().invoke(cli, ["--format", "json", "raw", "owned_tool", *sources])
        assert result.exit_code == 1 and calls == []


def test_source_contents_and_nested_values_are_never_expanded(admission, tmp_path):
    cli, calls = admission
    missing = "@" + str(tmp_path / "PRIVATE_MARKER-not-opened.json")
    path = tmp_path / "once.json"
    path.write_text(json.dumps({"nested": missing}), encoding="utf-8")
    result = invoke(cli, ("raw", "owned_tool"), None, "@" + str(path))
    assert result.exit_code == 0
    assert calls[-1][1] == {"nested": missing}
    path.write_text(json.dumps(missing), encoding="utf-8")
    calls.clear()
    result = invoke(cli, ("raw", "owned_tool"), None, "@" + str(path))
    assert result.exit_code == 1
    assert "expected an object" in result.stderr and "PRIVATE_MARKER" not in result.stderr
    assert not calls


def test_short_binary_reads_enforce_exact_limit_and_stdin_claim_resets(admission, monkeypatch):
    from io import BytesIO
    import cli.utils.parsers as parsers

    cli, calls = admission
    monkeypatch.setattr(parsers, "MAX_JSON_INPUT_BYTES", 32)

    class ShortReads(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 33
            return super().read(min(size, 3))

    for size, success in ((32, True), (33, False), (32, True)):
        encoded = b'{"x":0}' + b" " * (size - 7)
        stream = ShortReads(encoded)
        monkeypatch.setattr(parsers.click, "open_file", lambda *_a, _stream=stream, **_kw: _stream)
        before = len(calls)
        result = invoke(cli, ("raw", "owned_tool"), None, "@-")
        assert (result.exit_code == 0) is success, result.output
        assert len(calls) == before + int(success)


@pytest.mark.parametrize("command,flag,_payload", BOUNDARIES)
def test_boundary_help_and_callback_signatures_are_preserved(admission, command, flag, _payload):
    cli, calls = admission
    current = cli
    for token in command:
        if not isinstance(current, click.Group) or token not in current.commands:
            break
        current = current.commands[token]
    declared = set(inspect.signature(current.callback).parameters) - {"ctx"}
    assert declared == {parameter.name for parameter in current.params}
    result = CliRunner().invoke(cli, [*command, "--help"])
    assert result.exit_code == 0, result.output
    assert "@file" in result.stdout and "stdin" in result.stdout
    assert calls == []


@pytest.mark.parametrize("kind", ["missing", "directory", "double_bom", "oversize", "multibyte"])
def test_source_failures_precede_script_sha_and_keep_diagnostics_private(
    admission, tmp_path, monkeypatch, kind
):
    from cli.utils import parsers

    cli, calls = admission
    path = tmp_path / "PRIVATE_MARKER.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "double_bom":
        path.write_bytes(b"\xef\xbb\xbf\xef\xbb\xbf[]")
    elif kind in {"oversize", "multibyte"}:
        monkeypatch.setattr(parsers, "MAX_JSON_INPUT_BYTES", 32)
        path.write_bytes(
            b" " * 33
            if kind == "oversize"
            else json.dumps(["한" * 11], ensure_ascii=False).encode()
        )
    result = invoke(cli, ("script", "edit", "Assets/Owned.cs"), "edits", "@" + str(path))
    assert result.exit_code == 1 and result.stdout == ""
    assert "PRIVATE_MARKER" not in result.stderr
    assert not calls


def test_file_and_stdin_are_independent_inputs_and_nested_sources_stay_literal(admission, tmp_path):
    cli, calls = admission
    path = tmp_path / "palette.json"
    path.write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--palette",
            "@" + str(path),
            "--import-settings",
            "@-",
        ],
        input=b"{}",
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][1]["palette"] == [] and calls[0][1]["importSettings"] == {}
    calls.clear()
    path.write_text(json.dumps({"properties": "@PRIVATE_MARKER-do-not-open"}), encoding="utf-8")
    result = invoke(cli, ("animation", "raw", "owned_action"), "params", "@" + str(path))
    assert result.exit_code == 1 and not calls
    assert "Cannot read JSON input" not in result.stderr and "PRIVATE_MARKER" not in result.stderr


@pytest.mark.parametrize(
    "command,flag,payload",
    [
        (
            ("texture", "create", "Assets/Owned.png"),
            "palette",
            [{"r": "PRIVATE_MARKER", "g": 0, "b": 0}],
        ),
        (
            ("texture", "modify", "Assets/Owned.png"),
            "set-pixels",
            {"color": {"r": "PRIVATE_MARKER", "g": 0, "b": 0}},
        ),
    ],
)
def test_external_normalization_errors_do_not_echo_values(
    admission, tmp_path, command, flag, payload
):
    cli, calls = admission
    path = tmp_path / "invalid-values.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = invoke(cli, command, flag, "@" + str(path))
    assert result.exit_code != 0 and result.stdout == "" and not calls
    assert "PRIVATE_MARKER" not in result.stderr


def test_inline_literals_repairs_and_mixed_literal_fields_do_not_read_files(admission, monkeypatch):
    from cli.utils import parsers

    cli, calls = admission
    monkeypatch.setattr(
        parsers.click, "open_file", lambda *_a, **_kw: pytest.fail("Inline input opened a file")
    )
    assert parsers.parse_json_or_exit('"@literal.json"') == "@literal.json"
    result = invoke(
        cli,
        ("component", "modify", "Owned", "Fixture"),
        "properties",
        "{'text':'@literal','enabled':True}",
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][1]["properties"] == {"text": "@literal", "enabled": True}
    result = CliRunner().invoke(
        cli, ["--format", "json", "camera", "screenshot", "--view-target", "@literal-target"]
    )
    assert result.exit_code == 0 and calls[-1][1]["viewTarget"] == "@literal-target"
    result = CliRunner().invoke(
        cli, ["--format", "json", "physics", "set-settings", "unrelated", "@literal-value"]
    )
    assert result.exit_code == 0 and calls[-1][1]["settings"] == {"unrelated": "@literal-value"}


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_powershell_passes_unicode_file_and_pipe_bytes_to_fake_client(tmp_path, source):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    payload = {"text": '한글 "quotes"\nnext\r\n@literal'}
    data = tmp_path / "한글 input with spaces.json"
    data.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
    runner = tmp_path / "fake_cli.py"
    runner.write_text(
        textwrap.dedent(
            """
            import importlib, json, os, socket, sys
            os.environ["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
            sys.path.insert(0, sys.argv.pop(1))
            def denied(*args, **kwargs):
                raise AssertionError("PowerShell CLI test attempted network or token access")
            socket.socket.connect = denied
            socket.socket.connect_ex = denied
            main = importlib.import_module("cli.main")
            connection = importlib.import_module("cli.utils.connection")
            connection.read_local_auth_token = denied
            main.run_command = lambda tool, params, config: {"success": True, "data": params}
            main.cli()
            """
        ),
        encoding="utf-8",
    )

    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"

    command = "& " + " ".join(
        quoted(value)
        for value in (
            sys.executable,
            "-B",
            runner,
            Path(__file__).resolve().parents[1] / "src",
            "--format",
            "json",
            "raw",
            "owned_tool",
            "@" + str(data) if source == "file" else "-",
        )
    )
    if source == "stdin":
        command = "Get-Content -Raw -Encoding utf8 -LiteralPath " + quoted(data) + " | " + command
    command = (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding = $OutputEncoding; "
        + command
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["data"] == payload
