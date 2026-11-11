"""Run real native profile stages and host reload in an owned inactive Windows Editor.

The file bridge replaces only transport/routing in this acceptance fixture. The
production preparation, runner, source checks, diagnostics and native test handlers
execute unchanged. Public SDK/permission/admission contracts have separate tests.
"""

import argparse
import asyncio
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid


def validate_profiles(record):
    errors = []
    profiles = record.get("profiles", {})
    if set(profiles) != {"script-check", "editmode-check", "playmode-check"}:
        errors.append("missing_profile")
    for name, report in profiles.items():
        stages = {stage["name"]: stage for stage in report.get("stages", [])}
        for required in ("freshness", "final_freshness"):
            if stages.get(required, {}).get("state") != "passed":
                errors.append(name + ":" + required)
        analysis = stages.get("analysis", {})
        evidence = analysis.get("evidence", {})
        # Unity's project generators are intentionally never executed by this host.
        # Accept their explicit coverage gap as an exercised incomplete outcome,
        # never as proof that the production verification report passed.
        expected_gap = (
            analysis.get("state") == "incomplete"
            and report.get("state") == "incomplete"
            and evidence.get("coverage_gaps") == ["project_analyzers_and_generators_not_executed"]
            and evidence.get("providers")
            == {
                "compiler": "5.9.0",
                "Microsoft.CodeAnalysis.CSharp.CodeStyle": "5.9.0",
                "Microsoft.Unity.Analyzers": "1.28.0",
            }
            and bool(evidence.get("source_token"))
            and bool(evidence.get("analysis_id"))
            and evidence.get("active_severity_counts") == {"error": 0, "warning": 0, "info": 0}
        )
        if analysis.get("state") != "passed" and not expected_gap:
            errors.append(name + ":analysis")
        if name != "script-check":
            if stages.get("tests", {}).get("state") != "passed":
                errors.append(name + ":tests")
        if report.get("native_settled") is not True:
            errors.append(name + ":unsettled")
    reload = record.get("host_reload", {})
    if (
        reload.get("old_process_exited") is not True
        or reload.get("domain_changed") is not True
        or reload.get("occupied") != 0
    ):
        errors.append("host_reload_unproven")
    if not record.get("focus_samples") or record.get("pid") in record["focus_samples"]:
        errors.append("foreground_editor")
    return errors


class Bridge:
    def __init__(self, process, directory, record):
        self.process, self.directory, self.record = process, directory, record
        self.lock = asyncio.Lock()

    async def call(self, command, parameters=None, timeout=120):
        from tools.source_freshness_background_qa import foreground_pid

        async with self.lock:
            started = time.monotonic()
            identity = uuid.uuid4().hex
            payload = {"id": identity, "command": command, "parameters": parameters or {}}
            path = self.directory / "request.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            while True:
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    # The Unity reader may briefly deny rename on Windows. Only
                    # publish this same request ID; never redispatch a new command.
                    if time.monotonic() - started >= min(5, timeout):
                        raise
                    await asyncio.sleep(0.025)
            output = self.directory / (identity + ".json")
            self.record["commands"].append({"id": identity, "command": command})
            while self.process.poll() is None and time.monotonic() - started < timeout:
                self.record["focus_samples"].append(foreground_pid())
                self.process.decline_script_update()
                if output.exists():
                    try:
                        if output.stat().st_size > 2 * 1024 * 1024:
                            raise ValueError("native_reply_limit")
                        return json.loads(output.read_text(encoding="utf-8"))
                    except PermissionError:
                        # Observe this immutable reply again within the same deadline.
                        # No command is republished and no new native owner is created.
                        pass
                await asyncio.sleep(0.1)
            raise TimeoutError("owned_native_observation_timeout:" + command)


async def exercise(bridge, record, probe_id):
    from core.config import config
    from services.registry.operation_scheduler import OperationScope
    from services.registry.operation_target import OperationTarget
    from services.verification.service import BoundContext, VerificationService

    bootstrap = await bridge.call("bootstrap", timeout=300)
    record["bootstrap"] = bootstrap
    if bootstrap.get("source_freshness", {}).get("status") != "observed_current":
        raise ValueError("bootstrap_not_current")
    record["host_reload"] = await bridge.call("host_reload", timeout=90)
    instance = "OwnedVerification@" + probe_id
    scope = OperationScope("local", probe_id, probe_id)
    target = OperationTarget(
        scope, lambda: bridge.process.poll() is None, resume_generation=probe_id
    )

    async def capture(selected, **_kwargs):
        if selected != instance:
            raise ValueError("wrong_owned_target")
        return target

    async def send(_sender, selected, command, params, **kwargs):
        if selected != instance or kwargs.get("retry_on_reload") is not False:
            raise ValueError("unbound_or_replayed_native_request")
        timeout = max(0.001, kwargs.get("_deadline", time.monotonic() + 120) - time.monotonic())
        return await bridge.call(command, params, timeout)

    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "http_remote_hosted", False))
        for module in ("services.verification.service", "services.script_quality.service"):
            stack.enter_context(patch(module + ".capture_target", capture))
            stack.enter_context(patch(module + ".send_with_unity_instance", send))
        stack.enter_context(patch("transport.unity_transport.send_with_unity_instance", send))
        service = VerificationService()
        context = BoundContext(SimpleNamespace(), instance, None)
        record["profiles"] = {}
        for name, selector in (
            ("script-check", None),
            (
                "editmode-check",
                "MCPForUnityTests.Editor.Tools.ScriptQualityTests.ExplicitSelectionIsRequiredAndDuplicatesAreRefused",
            ),
            (
                "playmode-check",
                "MCPForUnityTests.PlayMode.PlayModeBasicTests.GameObjectCreation_InPlayMode_Succeeds",
            ),
        ):
            profiles = await service.execute(context, "profiles", {})
            if profiles.get("success") is not True:
                raise ValueError("profile_read_failed")
            if selector:
                profile = profiles["data"]["profiles"][name]
                profile["test_names"] = [selector]
                write = await service.execute(
                    context,
                    "set_profile",
                    {
                        "profile_id": name,
                        "profile": profile,
                        "expected_hash": profiles["data"]["byte_hash"],
                    },
                )
                if write.get("success") is not True:
                    raise ValueError("profile_write_failed")
            prepared = await service.execute(
                context,
                "prepare",
                {
                    "profile_id": name,
                    "paths": ["Assets/Scripts/TestAsmdef/SourceFreshnessProbeValue.cs"],
                },
            )
            if prepared.get("success") is not True:
                raise ValueError("prepare_failed")
            started = await service.execute(
                context, "run", {"preparation_id": prepared["data"]["preparation_id"]}
            )
            run_id = started["data"]["run_id"]
            until = time.monotonic() + 720
            while time.monotonic() < until:
                report = await service.execute(context, "status", {"run_id": run_id})
                record["profiles"][name] = report["data"]
                if report["data"]["state"] != "running":
                    break
                await asyncio.sleep(0.2)
            else:
                await service.execute(context, "cancel", {"run_id": run_id})
                raise TimeoutError("profile_observation_timeout")
        # These settings were explicitly created through the native CAS API in the
        # owned fixture; retain them as run evidence, never write a user project.


def run(output, editor, label):
    from tools.native_test_inputs import operation_lock, verify_inputs
    from tools.source_freshness_background_qa import DesktopProcess, foreground_pid
    from tools.unity_editor_transport_qa import owned_path

    if sys.platform != "win32":
        raise ValueError("The owned GUI acceptance fixture requires Windows")
    output = owned_path(output)
    if not label or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in label
    ):
        raise ValueError("Use a fresh plain evidence label")
    with operation_lock(output):
        prep = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
        project = Path(prep["projectAbsolute"]).resolve()
        if not project.is_relative_to(output) or project == output or prep.get("refreshPending"):
            raise ValueError("Owned preparation is incomplete")
        verify_inputs(output, prep, editor, prep["inputs"])
        if (project / "Temp/UnityLockfile").exists():
            raise ValueError("The owned project is already open")
        result_path = output / (label + "-verification.json")
        log = output / (label + "-verification.log")
        if result_path.exists() or log.exists():
            raise ValueError("Fresh run artifacts are required")
        probe_id = uuid.uuid4().hex
        directory = project / "Library/MCPVerificationQA" / probe_id
        env = os.environ.copy()
        env.pop("UNITY_MCP_ALLOW_BATCH", None)
        env.update(
            UNITY_MCP_VERIFICATION_QA_PROJECT=str(project),
            UNITY_MCP_VERIFICATION_QA_ID=probe_id,
            UNITY_MCP_OWNED_TRANSPORT_TESTS="1",
            UNITY_MCP_STATUS_DIR=str(output / "stdio-status"),
        )
        command = [
            str(editor),
            "-forgetProjectPath",
            "-projectPath",
            str(project),
            "-executeMethod",
            "MCPForUnityTests.Editor.Services.VerificationBackgroundProbe.Begin",
            "-logFile",
            str(log),
        ]
        process = DesktopProcess(command, project, env)
        record = {
            "pid": process.pid,
            "project": str(project),
            "inactive_desktop": process.name,
            "profiles": {},
            "commands": [],
            "focus_samples": [],
            "status": "INCOMPLETE",
            "scope": "real_native_handlers_and_server_composition_with_owned_file_bridge",
            "limitations": [
                "No public transport or remote-grant proof in this fixture.",
                "Test profiles use explicit single-test selectors, not full-mode coverage.",
            ],
        }
        bridge = Bridge(process, directory, record)
        source = project / "Assets/Scripts/TestAsmdef/SourceFreshnessProbeValue.cs"
        original = source.read_bytes()
        changed = original.replace(b"Read() => 1;", b"Read() => 2;")
        wrote = False
        result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        try:
            until = time.monotonic() + 180
            while (
                process.poll() is None
                and time.monotonic() < until
                and not (directory / "ready.json").exists()
            ):
                record["focus_samples"].append(foreground_pid())
                process.decline_script_update()
                time.sleep(0.1)
            ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            if ready["pid"] != process.pid:
                raise ValueError("wrong_owned_process")
            if original.count(b"Read() => 1;") != 1 or source.read_bytes() != original:
                raise ValueError("unexpected_owned_source")
            timestamp = source.stat()
            source.write_bytes(changed)
            os.utime(source, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
            wrote = True
            record["external_write"] = {
                "before_sha256": hashlib.sha256(original).hexdigest(),
                "after_sha256": hashlib.sha256(changed).hexdigest(),
                "same_timestamp": True,
            }
            asyncio.run(exercise(bridge, record, probe_id))
            record["failures"] = validate_profiles(record)
            if not record["failures"]:
                record["status"] = "PASS"
        except (OSError, ValueError, TimeoutError, KeyError) as error:
            record["reason"] = str(error)[:2048]
        finally:
            if process.poll() is None:
                try:
                    asyncio.run(bridge.call("quit", timeout=15))
                    process.wait(timeout=15)
                except (TimeoutError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=10)
            record["exit_code"] = process.returncode
            if wrote and source.read_bytes() == changed:
                source.write_bytes(original)
            elif source.read_bytes() != original:
                record["status"] = "INCOMPLETE"
                record["reason"] = "source_changed_by_another_writer"
            if process.returncode != 0:
                record["status"] = "INCOMPLETE"
            result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            process.close()
        print(
            json.dumps(
                {
                    "status": record["status"],
                    "evidence": str(result_path),
                    "reason": record.get("reason"),
                    "failures": record.get("failures"),
                }
            )
        )
        return 0 if record["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Server/src"))
    return run(args.output, args.editor, args.label)


if __name__ == "__main__":
    # Module execution (-m tools.verification_background_qa) retains root imports.
    raise SystemExit(main())
