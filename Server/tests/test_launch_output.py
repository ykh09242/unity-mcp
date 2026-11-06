"""Managed launch logging stays bounded without changing stdio protocol streams."""

import os
import subprocess
import sys

import pytest


LIMIT = 8 * 1024 * 1024


def child_environment(tmp_path, launch_log=None):
    env = {
        **os.environ,
        "UNITY_MCP_LOG_DIR": str(tmp_path / "application"),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("UNITY_MCP_LAUNCH_LOG", None)
    if launch_log is not None:
        env["UNITY_MCP_LAUNCH_LOG"] = str(launch_log)
    return env


@pytest.mark.parametrize(
    "write_output",
    [
        "for _ in range(10000): logging.warning('x' * 1024)",
        "assert sys.stderr.buffer.write(b'x' * (10 * 1024 * 1024)) == 10 * 1024 * 1024",
        "sys.stdout.write('한글 로그\\n' * (2 * 1024 * 1024))",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "with ThreadPoolExecutor(max_workers=4) as pool:\n"
        "    list(pool.map(lambda n: (sys.stdout if n % 2 else sys.stderr).buffer.write("
        "b'x' * (256 * 1024)), range(64)))",
    ],
    ids=["logging", "binary", "unicode_stdout", "concurrent_streams"],
)
def test_managed_launch_output_has_a_byte_ceiling(tmp_path, write_output):
    log = tmp_path / "launch log.log"
    program = (
        "import sys, logging\nsys.path.insert(0, 'src')\nimport main\n"
        + write_output
        + "\nsys.stdout.flush()\nsys.stderr.write('final diagnostic 한글\\n')\n"
    )
    with log.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-c", program],
            env=child_environment(tmp_path, log),
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    assert result.returncode == 0, log.read_bytes()[-4096:]
    assert 0 < log.stat().st_size <= LIMIT
    content = log.read_bytes()
    assert b"older launch output discarded" in content
    assert content.decode("utf-8").rstrip().endswith("final diagnostic 한글")


@pytest.mark.parametrize("configured", [False, True])
def test_stdio_pipe_is_unchanged_and_never_truncates_configured_path(tmp_path, configured):
    unrelated = tmp_path / "unrelated.log"
    unrelated.write_text("keep this content", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src'); import main; "
            'sys.stdout.write(\'{"jsonrpc":"2.0","id":1}\\n\')',
        ],
        env=child_environment(tmp_path, unrelated if configured else None),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"jsonrpc":"2.0","id":1}\n'
    assert unrelated.read_text(encoding="utf-8") == "keep this content"


def test_mismatched_redirect_is_unchanged(tmp_path):
    unrelated = tmp_path / "unrelated.log"
    unrelated.write_text("keep this content", encoding="utf-8")
    redirected = tmp_path / "actual.log"
    with redirected.open("wb") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'src'); import main; sys.stderr.write('diagnostic')",
            ],
            env=child_environment(tmp_path, unrelated),
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    assert result.returncode == 0
    assert redirected.read_text(encoding="utf-8") == "diagnostic"
    assert unrelated.read_text(encoding="utf-8") == "keep this content"


def test_bounded_stderr_does_not_change_stdout_protocol_pipe(tmp_path):
    log = tmp_path / "stderr.log"
    program = (
        "import sys; sys.path.insert(0, 'src'); import main; "
        "sys.stderr.buffer.write(b'x' * (10 * 1024 * 1024)); "
        'sys.stdout.write(\'{"jsonrpc":"2.0","id":1}\\n\')'
    )
    with log.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-c", program],
            env=child_environment(tmp_path, log),
            stdout=subprocess.PIPE,
            stderr=stream,
            text=True,
            timeout=30,
        )
    assert result.returncode == 0
    assert 0 < log.stat().st_size <= LIMIT
    assert result.stdout == '{"jsonrpc":"2.0","id":1}\n'
