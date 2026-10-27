"""Owned launcher cleanup remains bounded when process reaps and daemons stall."""

from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import local_harness as lh


@pytest.mark.parametrize("phase", ["warmup", "teardown"])
def test_local_child_reap_has_a_deadline(monkeypatch, tmp_path, phase):
    # Given: an owned child whose graceful and forceful waits expire.
    waits = []

    class Child:
        pid = 12345

        def wait(self, timeout=None):
            waits.append(timeout)
            assert timeout is not None, "post-kill wait must have its own deadline"
            raise subprocess.TimeoutExpired("owned-fake", timeout)

        def terminate(self):
            pass

        def kill(self):
            pass

    child = Child()
    launcher = lh.LocalLauncher(lh.build_arg_parser().parse_args([]))
    monkeypatch.setattr(lh.subprocess, "Popen", lambda *_a, **_kw: child)
    # When: the child cannot be reaped even after kill.
    with pytest.raises(subprocess.TimeoutExpired):
        if phase == "warmup":
            launcher.warmup("owned-fake", tmp_path, tmp_path / "log", 0.01)
        else:
            launcher.teardown(lh.Handle(proc=child, pid=child.pid), grace_s=0.01)
    # Then: every reap is bounded independently of execution.
    assert len(waits) == 2
    assert all(value > 0 for value in waits)


@pytest.mark.parametrize("operation", ["is_alive", "teardown"])
def test_docker_daemon_calls_have_deadlines(monkeypatch, tmp_path, operation):
    # Given: an owned container and a Docker daemon that never answers.
    launcher = lh.DockerLauncher(lh.build_arg_parser().parse_args(["--ci"]))
    launcher._owned_containers["owned"] = tmp_path

    def stalled(argv, **kwargs):
        assert kwargs.get("timeout", 0) > 0, "Docker RPC must have a deadline"
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(lh, "_run_docker", stalled)
    # When: cleanup or liveness reaches the stalled daemon.
    if operation == "is_alive":
        alive = launcher.is_alive(lh.Handle(container="owned"))
        # Then: timeout cannot prove that the container is gone.
        assert alive
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            launcher.teardown(lh.Handle(container="owned"))
        assert "owned" in launcher._owned_containers


@pytest.mark.parametrize("phase", ["normal-cleanup", "execution", "expired-cleaned"])
def test_hard_exit_does_not_wait_for_stalled_cleanup(tmp_path, phase):
    # Given: a completely synthetic launcher inside an owned Python process.
    code = r"""
import sys, threading
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import local_harness as h
work = Path(sys.argv[2])
def stop(handle):
    with (work / "cleanup-attempts").open("a") as log:
        log.write("attempt\n")
    if sys.argv[3] != "expired-cleaned":
        threading.Event().wait()
h.make_launcher = lambda _: SimpleNamespace(
    resolve_editor=lambda *_: h.EditorSpec("fake", "6000"),
    launch=lambda *a: h.Handle(pid=12345), teardown=stop,
    is_alive=lambda _: sys.argv[3] != "expired-cleaned", tail_log=lambda *_: "")
h.wait_for_ready = lambda *a: h.ReadyInfo(6400, "fake@12345678", "fake")
def smoke(*a, **kw):
    if sys.argv[3] == "execution":
        threading.Event().wait()
    if sys.argv[3] == "expired-cleaned":
        threading.Event().wait(1.05)
    return h.LegOutcome("smoke", "pass", blocking=True)
h.run_smoke_leg = smoke
raise SystemExit(h.main(["--project-path", str(work / "project"), "--no-warmup",
    "--legs", "smoke", "--overall-timeout", "1", "--cleanup-timeout", "1" if sys.argv[3] == "expired-cleaned" else "0.15",
    "--status-dir", str(work / "status"), "--reports", str(work / "reports"),
    "--junit", str(work / "reports/junit.xml")]))
"""
    # When: either normal teardown or the execution watchdog encounters a stall.
    result = subprocess.run(
        [sys.executable, "-c", code, str(TOOLS), str(tmp_path), phase],
        capture_output=True,
        text=True,
        timeout=8,
    )
    # Then: hard failure bypasses the blocked cleanup, with recovery evidence.
    assert result.returncode == 2, result.stdout + result.stderr
    assert (tmp_path / "cleanup-attempts").read_text().splitlines() == ["attempt"]
    if phase != "expired-cleaned":
        receipt = json.loads((tmp_path / "reports/reuse.json").read_text())
        assert Path(receipt["status_dir"]).is_dir()
        assert "local harness summary" not in result.stdout
    if phase != "execution":
        report = ET.parse(tmp_path / "reports/junit-all.xml")
        assert report.find(".//failure") is not None


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_cleanup_budget_rejects_invalid_values(value):
    # Given / When: an invalid operator cleanup budget reaches the CLI.
    with pytest.raises(SystemExit) as error:
        lh.build_arg_parser().parse_args(["--cleanup-timeout", value])
    # Then: execution never starts with an unbounded/negative budget.
    assert error.value.code == 2


@pytest.mark.parametrize("result", ["timeout", "success", "failure"])
def test_docker_cli_observes_bounded_reap_without_piped_output(monkeypatch, result):
    # Given: the CLI can time out after kill or return ordinary output/status.
    waits = []

    class Child:
        def __init__(self, argv, stdout, stderr):
            assert stdout != subprocess.PIPE and stderr != subprocess.PIPE
            if result == "success":
                stdout.write(b"owned-output")

        def wait(self, timeout=None):
            waits.append(timeout)
            assert timeout is not None and timeout > 0
            if result == "timeout":
                raise subprocess.TimeoutExpired("owned-cli", timeout)
            return 0 if result == "success" else 1

        def kill(self):
            pass

    monkeypatch.setattr(lh.subprocess, "Popen", Child)
    # When / Then: CLI output succeeds normally or timeout/failure propagates.
    if result == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            lh._run_docker(["owned-cli"], timeout=0.01, cleanup_timeout=0.02)
        assert waits == [0.01, 0.02]
    elif result == "success":
        observed = lh._run_docker(["owned-cli"], timeout=1, capture_output=True, check=True)
        assert observed.returncode == 0 and observed.stdout == "owned-output"
    else:
        with pytest.raises(subprocess.CalledProcessError):
            lh._run_docker(["owned-cli"], timeout=1, check=True)


def test_powershell_postkill_wait_is_bounded_and_preserves_logs(tmp_path):
    # Given: execute the actual timeout block against an owned fake process.
    import shutil

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    fixture = TOOLS / "tests/fixtures/phase12_websocket/RunPhase12WebSocket.ps1"
    (tmp_path / "stdout.log").write_text("owned stdout evidence")
    (tmp_path / "stderr.log").write_text("owned stderr evidence")
    script = r"""
param([string]$Fixture, [string]$Work)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Fixture, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw ($errors | Out-String) }
$block = $ast.FindAll({param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text -eq '!$process.WaitForExit($ProcessTimeoutMilliseconds)'
}, $true)
if ($block.Count -ne 1) { throw 'Expected one process timeout block' }
$runtimeOutput = Join-Path $Work 'stdout.log'
$runtimeError = Join-Path $Work 'stderr.log'
$ProcessTimeoutMilliseconds = 1
$CleanupTimeoutMilliseconds = 2
$process = [pscustomobject]@{}
$process | Add-Member ScriptMethod WaitForExit {
    param($Milliseconds)
    if ($null -eq $Milliseconds -or $Milliseconds -le 0) { throw 'Unbounded wait' }
    return $false
}
$process | Add-Member ScriptMethod Kill { Write-Output 'owned kill attempted' }
$process | Add-Member ScriptMethod Dispose { Write-Output 'owned process disposed' }
try {
    Invoke-Expression $block[0].Extent.Text
    throw 'Expected cleanup failure'
} catch {
    if (!$_.Exception.Message.Contains('exit could not be confirmed within the cleanup budget')) { throw }
    Write-Output 'bounded cleanup failure observed'
}
"""
    runner = tmp_path / "exercise.ps1"
    runner.write_text(script)
    # When: both the execution wait and force-kill reap stall.
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(runner), str(fixture), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    # Then: post-kill observation is bounded and both original logs are present.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "owned stdout evidence" in result.stdout
    assert "owned stderr evidence" in result.stdout
    assert "bounded cleanup failure observed" in result.stdout


def test_timed_out_docker_launch_retains_unconfirmed_identity_without_removal(
    tmp_path, monkeypatch
):
    # Given: launch may reach a daemon but never returns a confirmed container ID.
    launcher = lh.DockerLauncher(lh.build_arg_parser().parse_args(["--ci"]))
    calls = []

    def stalled(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(lh, "_run_docker", stalled)
    # When: the owned CLI launch times out.
    with pytest.raises(subprocess.TimeoutExpired):
        launcher.launch("ignored", tmp_path, tmp_path, tmp_path / "log", [])
    pending = launcher.pending_handle
    launcher.teardown(pending)
    # Then: unknown launch is retained, never claimed stopped or removed by name.
    assert pending.container in calls[0]
    assert launcher.is_alive(pending)
    assert len(calls) == 1 and launcher._owned_containers == {}
