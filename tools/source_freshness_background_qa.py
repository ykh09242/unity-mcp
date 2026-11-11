"""Witness externally changed code compiling in a separately owned, unfocused GUI Editor.

Prepare the project with unity_editor_transport_qa.py first. This Windows witness
never adopts another Editor or changes foreground focus or global preferences.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class DesktopProcess:
    """Hold the process and inactive desktop handles; never activate that desktop."""

    def __init__(self, command: list[str], cwd: Path, env: dict[str, str]):
        from ctypes import wintypes as w

        class Startup(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("reserved", w.LPWSTR),
                ("desktop", w.LPWSTR),
                ("title", w.LPWSTR),
                ("x", w.DWORD),
                ("y", w.DWORD),
                ("width", w.DWORD),
                ("height", w.DWORD),
                ("xchars", w.DWORD),
                ("ychars", w.DWORD),
                ("fill", w.DWORD),
                ("flags", w.DWORD),
                ("show", w.WORD),
                ("reserved_bytes", w.WORD),
                ("reserved_pointer", ctypes.c_void_p),
                ("stdin", w.HANDLE),
                ("stdout", w.HANDLE),
                ("stderr", w.HANDLE),
            ]

        class Information(ctypes.Structure):
            _fields_ = [
                ("process", w.HANDLE),
                ("thread", w.HANDLE),
                ("pid", w.DWORD),
                ("tid", w.DWORD),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.CreateDesktopW.argtypes = [
            w.LPCWSTR,
            w.LPCWSTR,
            ctypes.c_void_p,
            w.DWORD,
            w.DWORD,
            ctypes.c_void_p,
        ]
        self.user.CreateDesktopW.restype = w.HANDLE
        self.user.CloseDesktop.argtypes = [w.HANDLE]
        self.user.GetThreadDesktop.argtypes = [w.DWORD]
        self.user.GetThreadDesktop.restype = w.HANDLE
        self.user.SetThreadDesktop.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        self.kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.WaitForSingleObject.restype = w.DWORD
        self.kernel.CreateProcessW.argtypes = [
            w.LPCWSTR,
            w.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            w.BOOL,
            w.DWORD,
            ctypes.c_void_p,
            w.LPCWSTR,
            ctypes.POINTER(Startup),
            ctypes.POINTER(Information),
        ]
        self.name = "McpFreshness_" + uuid.uuid4().hex
        original = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
        self.desktop = self.user.CreateDesktopW(self.name, None, None, 0, 0x01FF, None)
        if not self.desktop:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = None
        self.returncode = None
        try:
            if not self.user.SetThreadDesktop(original):
                raise ctypes.WinError(ctypes.get_last_error())
            startup = Startup()
            startup.cb = ctypes.sizeof(startup)
            startup.desktop = self.name
            startup.flags = 1  # STARTF_USESHOWWINDOW
            startup.show = 0  # SW_HIDE; the desktop itself also remains inactive.
            information = Information()
            environment = ctypes.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0"
            )
            arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            if not self.kernel.CreateProcessW(
                command[0],
                arguments,
                None,
                None,
                False,
                0x00000400,
                environment,
                str(cwd),
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = information.process
            self.pid = information.pid
            self.kernel.CloseHandle(information.thread)
        except BaseException:
            self.close()
            raise

    def poll(self):
        if self.returncode is None:
            code = ctypes.c_ulong(0)
            if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            if code.value != 259:
                self.returncode = code.value
        return self.returncode

    def wait(self, timeout=10):
        result = self.kernel.WaitForSingleObject(self.handle, int(timeout * 1000))
        if result == 258:
            raise subprocess.TimeoutExpired("owned desktop process", timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self.kernel.TerminateProcess(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def decline_script_update(self) -> bool:
        """Decline only this fixture's known optional source-migration dialog."""
        from ctypes import wintypes as w

        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumDesktopWindows.argtypes = [w.HANDLE, callback_type, w.LPARAM]
        self.user.EnumChildWindows.argtypes = [w.HWND, callback_type, w.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.SendMessageTimeoutW.argtypes = [
            w.HWND,
            w.UINT,
            w.WPARAM,
            w.LPARAM,
            w.UINT,
            w.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        declined = False

        def attributes(window):
            pid = w.DWORD(0)
            self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
            title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(128)
            self.user.GetWindowTextW(window, title, 512)
            self.user.GetClassNameW(window, kind, 128)
            return pid.value, title.value, kind.value

        def child(window, _):
            nonlocal declined
            if attributes(window) == (self.pid, "&No", "Button"):
                result = ctypes.c_size_t(0)
                declined = bool(
                    self.user.SendMessageTimeoutW(
                        window, 0x00F5, 0, 0, 2, 500, ctypes.byref(result)
                    )
                )
            return True

        child_callback = callback_type(child)

        def top(window, _):
            if attributes(window) == (self.pid, "Script Updating Consent", "#32770"):
                self.user.EnumChildWindows(window, child_callback, 0)
            return True

        self.user.EnumDesktopWindows(self.desktop, callback_type(top), 0)
        return declined

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.desktop:
            self.user.CloseDesktop(self.desktop)
            self.desktop = None


def validate_witness(witness: dict, pid: int) -> list[str]:
    """Require both OS and Editor evidence and a changed loaded return value."""
    failures = []
    for stage, value in (("before", 1), ("after", 2)):
        sample = witness.get(stage, {})
        if (
            sample.get("pid") != pid
            or sample.get("batch_mode") is not False
            or sample.get("focused") is not False
            or sample.get("value") != value
        ):
            failures.append(f"invalid_{stage}_gui_observation")
    samples = witness.get("focus_samples", [])
    if len(samples) < 2 or any(
        type(value) is not int or value <= 0 or value == pid for value in samples
    ):
        failures.append("unfocused_os_observation_missing")
    if witness.get("observed_old_value_after_write") != 1:
        failures.append("stale_code_not_reproduced")
    freshness = witness.get("after", {}).get("source_freshness", {})
    if (
        freshness.get("status") != "observed_current"
        or freshness.get("source_revision_confirmed") is not False
    ):
        failures.append("source_evidence_incomplete")
    return failures


def foreground_pid() -> int:
    """Read OS focus without switching windows."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


def run(output: Path, editor: Path, label: str, timeout: float) -> int:
    # Lazy imports keep the pure evidence validator usable on every platform.
    try:
        from .unity_editor_transport_qa import owned_path
        from .native_test_inputs import operation_lock, verify_inputs
    except ImportError:
        from unity_editor_transport_qa import owned_path
        from native_test_inputs import operation_lock, verify_inputs

    if sys.platform != "win32":
        raise ValueError("This GUI focus witness currently requires Windows")
    output = owned_path(output)
    if not label or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in label
    ):
        raise ValueError("A plain evidence label is required")
    with operation_lock(output):
        prep = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
        project = Path(prep["projectAbsolute"]).resolve()
        if not project.is_relative_to(output) or project == output or prep.get("refreshPending"):
            raise ValueError("The prepared project is not owned or preparation is incomplete")
        verify_inputs(output, prep, editor, prep["inputs"])
        if (project / "Temp/UnityLockfile").exists():
            raise ValueError("The owned fixture is already open or has an unresolved lock")
        probe_id = uuid.uuid4().hex
        protocol_directory = project / "Library/MCPFreshnessQA" / probe_id
        state_path = protocol_directory / "state.json"
        marker = protocol_directory / "written"
        result_path = output / f"{label}-background.json"
        log_path = output / f"{label}-background.log"
        if any(path.exists() for path in (state_path, marker, result_path, log_path)):
            raise ValueError("Fresh witness artifacts are required")
        source = project / "Assets/Scripts/TestAsmdef/SourceFreshnessProbeValue.cs"
        original = source.read_bytes()
        if original.count(b"Read() => 1;") != 1:
            raise ValueError("The owned return-value probe is unexpected")
        changed = original.replace(b"Read() => 1;", b"Read() => 2;")
        stat = source.stat()
        command = [
            str(editor),
            "-forgetProjectPath",
            "-projectPath",
            str(project),
            "-executeMethod",
            "MCPForUnityTests.Editor.Services.SourceFreshnessBackgroundProbe.Begin",
            "-logFile",
            str(log_path),
        ]
        env = os.environ.copy()
        env.pop("UNITY_MCP_ALLOW_BATCH", None)
        env["UNITY_MCP_FRESHNESS_QA_PROJECT"] = str(project)
        env["UNITY_MCP_FRESHNESS_QA_ID"] = probe_id
        env["UNITY_MCP_OWNED_TRANSPORT_TESTS"] = "1"
        env["UNITY_MCP_STATUS_DIR"] = str(output / "stdio-status")
        started = time.monotonic()
        process = DesktopProcess(command, project, env)
        record = {
            "pid": process.pid,
            "inactive_desktop": process.name,
            "protocol_directory": str(protocol_directory),
            "command": command,
            "project": str(project),
            "input_source_sha256": hashlib.sha256(original).hexdigest(),
            "changed_source_sha256": hashlib.sha256(changed).hexdigest(),
            "focus_samples": [],
            "status": "INCOMPLETE",
            "optional_script_updates_declined": 0,
        }
        result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        wrote = False
        try:
            while process.poll() is None and time.monotonic() - started < timeout:
                record["focus_samples"].append(foreground_pid())
                if (
                    record["optional_script_updates_declined"] < 8
                    and process.decline_script_update()
                ):
                    record["optional_script_updates_declined"] += 1
                if state_path.exists():
                    try:
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        state = {}
                    if "before" in state and not wrote:
                        source.write_bytes(changed)
                        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                        marker.write_text("external-write-complete", encoding="utf-8")
                        wrote = True
                time.sleep(0.1)
            if process.poll() is None:
                record["reason"] = "owned_gui_timeout"
            else:
                record["exit_code"] = process.returncode
                if state_path.exists():
                    record.update(json.loads(state_path.read_text(encoding="utf-8")))
                record["observed_old_value_after_write"] = record.get(
                    "after_external_write", {}
                ).get("value")
                record["failures"] = validate_witness(record, process.pid)
                if process.returncode == 0 and not record["failures"]:
                    record["status"] = "PASS"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            # Restore only the exact write owned by this run, after the held process exits.
            if wrote and source.read_bytes() == changed:
                source.write_bytes(original)
            elif source.read_bytes() != original:
                record["status"] = "INCOMPLETE"
                record["reason"] = "probe_changed_by_another_writer"
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                verify_inputs(output, prep, editor, prep["inputs"])
            except (OSError, ValueError) as error:
                record["status"] = "INCOMPLETE"
                record["reason"] = str(error)
            result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            process.close()
        print(
            json.dumps(
                {
                    "status": record["status"],
                    "evidence": str(result_path),
                    "reason": record.get("reason"),
                    "failures": record.get("failures", []),
                }
            )
        )
        return 0 if record["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    return run(args.output, args.editor, args.label, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
