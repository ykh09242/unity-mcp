"""Runner ownership/exit regressions; fake process handles, never a real Editor."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import unity_editor_transport_qa as qa


class OwnedFakeProcess:
    pid = 987654321

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def wait(self, timeout):
        result = next(self.outcomes)
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


class UnityEditorTransportRunnerTests(unittest.TestCase):
    def setUp(self):
        parent = (qa.ROOT / "reports/CS-20261006-mcp-usability/phase7/editor-qa/runner-unit").resolve()
        self.assertTrue(parent.is_relative_to(qa.ROOT.resolve()))
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="held-process-", dir=parent)
        self.output = Path(self.temporary.name).resolve()
        self.project = self.output / "project"
        self.project.mkdir()
        self.editor = self.output / "fake-editor.exe"
        self.editor.write_bytes(b"")
        (self.output / "preparation.json").write_text(json.dumps({"projectAbsolute": str(self.project)}))
        self.args = SimpleNamespace(output=str(self.output), editor=self.editor, isolation_verified=True,
                                    label="fake", filter="OwnedFixture", timeout=1,
                                    stdio_command_timeout_ms=None)

    def tearDown(self):
        self.temporary.cleanup()

    def passed_xml(self):
        (self.output / "fake-results.xml").write_text(
            '<test-run result="Passed" total="1" passed="1" failed="0" skipped="0">'
            '<test-case fullname="OwnedFixture.Criterion" result="Passed"/></test-run>')

    def record(self):
        return json.loads((self.output / "fake-launch.json").read_text())

    def run_fake(self, process):
        with patch.object(qa.subprocess, "Popen", return_value=process) as popen, redirect_stdout(io.StringIO()):
            result = qa.launch(self.args)
        self.assertEqual(1, popen.call_count)
        return result

    def test_pass_requires_clean_exit_and_positive_passed_xml(self):
        self.passed_xml()
        process = OwnedFakeProcess([0])
        self.assertEqual(0, self.run_fake(process))
        self.assertEqual("PASS", self.record()["status"])
        self.assertEqual(0, process.terminated)
        self.assertEqual(0, process.killed)

    def test_nonzero_exit_cannot_pass_even_with_passed_xml(self):
        self.passed_xml()
        process = OwnedFakeProcess([2])
        self.assertEqual(2, self.run_fake(process))
        self.assertEqual("FAIL", self.record()["status"])

    def test_timeout_remains_blocked_even_if_passed_xml_exists(self):
        self.passed_xml()
        process = OwnedFakeProcess([subprocess.TimeoutExpired("owned", 1), 0])
        self.assertEqual(3, self.run_fake(process))
        record = self.record()
        self.assertEqual("BLOCKED", record["status"])
        self.assertIn("timeout", record["reason"])
        self.assertEqual("Passed", record["nunit"]["result"])
        self.assertEqual(1, process.terminated)
        self.assertEqual(0, process.killed)

    def test_timeout_kills_only_held_process_when_terminate_wait_times_out(self):
        process = OwnedFakeProcess([subprocess.TimeoutExpired("owned", 1), subprocess.TimeoutExpired("owned", 10), -1])
        self.assertEqual(3, self.run_fake(process))
        self.assertEqual(1, process.terminated)
        self.assertEqual(1, process.killed)
        self.assertEqual("kill-after-terminate-timeout", self.record()["ownedProcessCleanup"])

    def test_keyboard_interrupt_cleans_held_process_and_records_blocker(self):
        process = OwnedFakeProcess([KeyboardInterrupt(), 0])
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake(process)
        self.assertEqual(1, process.terminated)
        self.assertEqual("BLOCKED", self.record()["status"])
        self.assertEqual("KeyboardInterrupt", self.record()["exceptionType"])

    def test_unexpected_wait_failure_cleans_held_process_and_records_blocker(self):
        process = OwnedFakeProcess([RuntimeError("fake wait failure"), 0])
        with self.assertRaises(RuntimeError):
            self.run_fake(process)
        self.assertEqual(1, process.terminated)
        self.assertEqual("BLOCKED", self.record()["status"])
        self.assertEqual("RuntimeError", self.record()["exceptionType"])

    def test_invalid_xml_is_blocked_and_does_not_pass_from_exit_alone(self):
        (self.output / "fake-results.xml").write_text("<partial")
        process = OwnedFakeProcess([0])
        self.assertEqual(3, self.run_fake(process))
        self.assertEqual("BLOCKED", self.record()["status"])

    def test_owned_deadline_configuration_changes_child_environment_only(self):
        self.passed_xml()
        self.args.stdio_command_timeout_ms = 1000
        process = OwnedFakeProcess([0])
        with patch.dict(qa.os.environ, {"UNITY_MCP_ALLOW_BATCH": "do-not-forward", "UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS": "9000"}):
            with patch.object(qa.subprocess, "Popen", return_value=process) as popen, redirect_stdout(io.StringIO()):
                self.assertEqual(0, qa.launch(self.args))
            child = popen.call_args.kwargs["env"]
            self.assertNotIn("UNITY_MCP_ALLOW_BATCH", child)
            self.assertEqual("1000", child["UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS"])
            self.assertEqual("9000", qa.os.environ["UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS"])
            self.assertEqual("do-not-forward", qa.os.environ["UNITY_MCP_ALLOW_BATCH"])
            self.assertEqual(1000, self.record()["stdioCommandTimeoutMs"])

    def test_owned_integration_opt_in_changes_child_environment_only(self):
        self.passed_xml()
        process = OwnedFakeProcess([0])
        with patch.dict(qa.os.environ, {"UNITY_MCP_OWNED_TRANSPORT_TESTS": "disabled"}):
            with patch.object(qa.subprocess, "Popen", return_value=process) as popen, redirect_stdout(io.StringIO()):
                self.assertEqual(0, qa.launch(self.args))
            self.assertEqual("1", popen.call_args.kwargs["env"]["UNITY_MCP_OWNED_TRANSPORT_TESTS"])
            self.assertEqual("disabled", qa.os.environ["UNITY_MCP_OWNED_TRANSPORT_TESTS"])
            self.assertTrue(self.record()["ownedTransportTests"])


if __name__ == "__main__":
    unittest.main()
