"""Run an existing lifecycle sample through MCP stdio and its owned native bridge."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from player_e2e_process import Invocation, execute

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/fixtures/play_scenario_mcp"
METHOD = "UnityMcpLifecycleTransport.Tests.TransportScenarioTests.McpCallsReachTheOwnedEditor"
SCENARIOS = (
    ("sample-normal", "succeeded", 2),
    ("sample-cancel", "cancelled", 1),
    ("sample-retained", "failed", 1),
    ("sample-release", "succeeded", 1),
    ("sample-normal", "succeeded", 1),
)


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_ready(ready: dict, project: Path) -> str:
    """Reject any endpoint that is not the explicit owned sample."""
    if (
        ready.get("host") != "127.0.0.1"
        or type(ready.get("port")) is not int
        or not 1024 <= ready["port"] <= 65535
        or ready["port"] in (6400, 6401, 8080)
        or type(ready.get("pid")) is not int
        or ready["pid"] <= 0
        or Path(ready.get("project", "")).resolve() != project.resolve()
        or not re.fullmatch(r"project@[a-f0-9]{8}", ready.get("instance", ""))
    ):
        raise ValueError("Endpoint does not identify the owned loopback sample")
    return ready["instance"]


def validate_report(
    report: dict,
    expected_id: str,
    expected_status: str,
    repeats: int,
    retained: bool = False,
    *,
    scenario: str,
    source_revision: str,
) -> None:
    """Require terminal resource and iteration evidence, including negative controls."""
    if (
        report.get("job_id") != expected_id
        or report.get("scenario", {}).get("name") != scenario
        or report.get("reproduction", {}).get("source_revision") != source_revision
        or report.get("status") != expected_status
        or report.get("phase") != "finished"
        or report.get("runner_resources_released") is not True
        or report.get("report_error") is not None
        or report.get("report_path") != f"Library/MCPForUnity/PlayScenarioRuns/{expected_id}.json"
        or report.get("repeat_count") != repeats
        or report.get("iteration_results_version") != 1
        or len(report.get("iteration_results", [])) != repeats
        or len(report.get("resource_checks", [])) != repeats
        or len(report.get("cleanup_failures", [])) != int(retained)
    ):
        raise ValueError("Incomplete or mismatched terminal run report")
    expected_iteration = "passed" if expected_status == "succeeded" else expected_status
    for index, (iteration, check) in enumerate(
        zip(report["iteration_results"], report["resource_checks"]), 1
    ):
        if (
            iteration.get("iteration") != index
            or iteration.get("status") != expected_iteration
            or check.get("iteration") != index
            or check.get("passed") is not (not retained)
            or any(
                check.get(key) != int(retained)
                for key in ("new_scriptable_objects", "new_subscriptions", "new_handles")
            )
        ):
            raise ValueError("Iteration or retained-resource result mismatch")
        resources = check.get("retained_resources", [])
        if retained:
            if (
                len(resources) != 3
                or len({item.get("id") for item in resources}) != 3
                or {item.get("kind") for item in resources}
                != {"scriptable_object", "subscription", "handle"}
                or len({item.get("owner") for item in resources}) != 1
                or not str(resources[0].get("owner", "")).startswith("lifecycle-session:")
                or report.get("failure", {}).get("code") != "resource_assertion_failed"
            ):
                raise ValueError("Retained control did not identify all three owned resources")
        elif resources:
            raise ValueError("Clean report contains retained identities")
    if expected_status == "cancelled" and report.get("failure", {}).get("code") != "cancelled":
        raise ValueError("Cancellation lost its primary outcome")


def validate_history(history: dict, expected: dict[str, dict]) -> None:
    """Require exactly one complete persisted report for each run in this probe."""
    entries = history.get("data", {}).get("reports")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ValueError("MCP history must contain report objects")
    for job, stored in expected.items():
        matching = [item for item in entries if item.get("job_id") == job]
        if len(matching) != 1 or matching[0] != stored:
            raise ValueError("MCP history differs from the validated persisted report: " + job)


def decode(result) -> dict:
    values = [
        json.loads(item.text) for item in result.content if getattr(item, "type", "") == "text"
    ]
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("MCP reply must contain one JSON object")
    value = values[0]
    structured = getattr(result, "structured_content", None)
    if structured is not None and structured != value:
        raise ValueError("MCP text and structured replies differ")
    return value


async def probe(project: Path, output: Path, ready: dict, nonce: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError
    from bench_transport_process import native_python

    instance = validate_ready(ready, project)
    command = native_python(
        [
            str(FIXTURE / "server.py"),
            "--ready",
            str(output / "ready.json"),
            "--project",
            str(project),
            "--output",
            str(output),
        ],
        (ROOT / "Server/src", ROOT / "tools"),
    )
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    environment.update(
        {
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "UNITY_MCP_ALLOW_BATCH": "",
            "UNITY_MCP_DISABLE_TELEMETRY": "1",
            "UNITY_MCP_TELEMETRY_ENABLED": "0",
            "UNITY_MCP_STATUS_DIR": str(output / "unused-discovery"),
            "UNITY_MCP_LOG_DIR": str(output / "server-logs"),
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    calls = []
    reports = []
    persisted_reports = {}
    with (output / "server-stderr.log").open("w", encoding="utf-8") as errors:
        async with stdio_client(
            StdioServerParameters(command=command[0], args=command[1:], env=environment),
            errlog=errors,
        ) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as session:
                initialized = await session.initialize()
                save(output / "initialize.json", initialized.model_dump(mode="json"))

                async def call(name, arguments, *, reject=False):
                    if len(calls) >= 256:
                        raise ValueError("MCP query budget exceeded")
                    try:
                        result = await session.call_tool(name, arguments, read_timeout_seconds=15)
                    except MCPError as error:
                        calls.append(
                            {
                                "tool": name,
                                "arguments": arguments,
                                "jsonrpc_error": error.error.model_dump(mode="json"),
                            }
                        )
                        save(output / "calls.json", calls)
                        expected = (
                            "Instance 'missing@ffffffff' not found. "
                            f"Available: {instance}. "
                            "Read mcpforunity://instances for current sessions."
                        )
                        if reject and error.message == expected:
                            return None
                        raise
                    entry = {
                        "tool": name,
                        "arguments": arguments,
                        "result": result.model_dump(mode="json"),
                    }
                    calls.append(entry)
                    save(output / "calls.json", calls)
                    if reject:
                        raise ValueError(
                            "Unknown target did not produce the expected protocol error"
                        )
                    if result.is_error:
                        raise ValueError(f"MCP tool failed: {name}: {result}")
                    response = decode(result)
                    if response.get("success") is False or response.get("error"):
                        raise ValueError(f"MCP command rejected: {name}: {response}")
                    return response

                await call("manage_tools", {"action": "activate", "group": "testing"})
                tools = await session.list_tools()
                save(output / "tools.json", tools.model_dump(mode="json"))
                if "manage_play_scenario" not in {tool.name for tool in tools.tools}:
                    raise ValueError("Scenario tool is not exposed; do not bypass visibility")
                await call("set_active_instance", {"instance": instance})
                info = await session.read_resource("mcpforunity://project/info")
                save(output / "project-info.json", info.model_dump(mode="json"))
                project_info = json.loads(info.contents[0].text)
                if Path(project_info["data"]["projectRoot"]).resolve() != project:
                    raise ValueError("MCP resource identifies another project")
                await call(
                    "manage_scene",
                    {"action": "get_active", "unity_instance": "missing@ffffffff"},
                    reject=True,
                )
                for name, expected, repeats in SCENARIOS:
                    job = uuid.uuid4().hex
                    await call(
                        "manage_play_scenario",
                        {
                            "action": "run",
                            "name": name,
                            "job_id": job,
                            "repeat_count": repeats,
                            "timeout_seconds": 45,
                            "source_revision": nonce,
                            "unity_instance": instance,
                        },
                    )
                    cancelled = False
                    deadline = time.monotonic() + 55
                    while time.monotonic() < deadline:
                        response = await call(
                            "manage_play_scenario",
                            {
                                "action": "status",
                                "job_id": job,
                                "unity_instance": instance,
                            },
                        )
                        report = response["data"]
                        if report["status"] != "running":
                            break
                        if (
                            name == "sample-cancel"
                            and not cancelled
                            and any(
                                step.get("name") == "Game ready" and step.get("status") == "running"
                                for step in report.get("steps", [])
                            )
                        ):
                            await call(
                                "manage_play_scenario",
                                {
                                    "action": "cancel",
                                    "job_id": job,
                                    "unity_instance": instance,
                                },
                            )
                            cancelled = True
                        await asyncio.sleep(0.2)
                    else:
                        raise ValueError("Scenario failed to finish within the probe deadline")
                    validate_report(
                        report,
                        job,
                        expected,
                        repeats,
                        retained=name == "sample-retained",
                        scenario=name,
                        source_revision=nonce,
                    )
                    if name == "sample-cancel" and not cancelled:
                        raise ValueError("Probe did not issue a live cancellation")
                    source = project / report["report_path"]
                    raw = source.read_bytes()
                    stored = json.loads(raw)
                    for field in (
                        "job_id",
                        "status",
                        "resource_checks",
                        "iteration_results",
                        "iteration_results_version",
                        "failure",
                        "cleanup_failures",
                        "runner_resources_released",
                        "phase",
                        "reproduction",
                        "scenario",
                        "repeat_count",
                    ):
                        if report.get(field) != stored.get(field):
                            raise ValueError("Persisted report differs from MCP reply: " + field)
                    persisted_reports[job] = stored
                    (output / (job + ".json")).write_bytes(raw)
                    reports.append(
                        {
                            "job_id": job,
                            "scenario": name,
                            "status": expected,
                            "repeats": repeats,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    )
                history = await call(
                    "manage_play_scenario", {"action": "reports", "unity_instance": instance}
                )
                validate_history(history, persisted_reports)
    return {
        "success": True,
        "nonce": nonce,
        "project": str(project),
        "instance": instance,
        "mcp_tool_calls": len(calls),
        "reports": reports,
        "resource_checks": 6,
        "clean_checks": 5,
        "intentional_retained_checks": 1,
        "unknown_target_rejected": True,
        "transport": "MCP stdio -> authenticated native loopback",
    }


def run(project: Path, editor: Path, output: Path) -> dict:
    project, editor, output = project.resolve(), editor.resolve(), output.resolve()
    marker = project / "ProjectSettings/LifecycleSample.json"
    if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != {
        "sample": "play-scenario-lifecycle",
        "schema_version": 1,
    }:
        raise ValueError("Requires the existing generated lifecycle sample")
    if not editor.is_file() or (project / "Temp/UnityLockfile").exists():
        raise ValueError("Explicit installed Editor required and sample must be closed")
    for name, _, _ in SCENARIOS:
        if not (project / f"ProjectSettings/MCPForUnity/PlayScenarios/{name}.json").is_file():
            raise ValueError("Prepare the saved sample scenarios before transport verification")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("TransportScenarioTests.cs", "TransportScenarioTests.cs.meta"):
        target = project / "Assets/Tests/Editor" / name
        if target.exists() and target.read_bytes() != (FIXTURE / name).read_bytes():
            raise ValueError("Existing transport fixture differs; preserve it for review")
        shutil.copyfile(FIXTURE / name, target)
    nonce = uuid.uuid4().hex
    command = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-forgetProjectPath",
        "-projectPath",
        str(project),
        "-runTests",
        "-testPlatform",
        "EditMode",
        "-testFilter",
        METHOD,
        "-testResults",
        str(output / "results.xml"),
        "-logFile",
        "-",
    ]
    source_hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__).resolve(),
            FIXTURE / "server.py",
            FIXTURE / "TransportScenarioTests.cs",
        )
    }
    save(
        output / "request.json",
        {
            "nonce": nonce,
            "project": str(project),
            "command": command,
            "source_sha256": source_hashes,
            "started_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    invocation = Invocation(
        command,
        output / "editor-process",
        timeout=240,
        environment={
            "UNITY_MCP_ALLOW_BATCH": "",
            "UNITY_MCP_DISABLE_TELEMETRY": "1",
            "UNITY_MCP_SAMPLE_TRANSPORT_OUTPUT": str(output),
            "UNITY_MCP_SAMPLE_TRANSPORT_NONCE": nonce,
            "UNITY_MCP_STATUS_DIR": str(output / "unused-discovery"),
        },
    )
    result = None
    with ThreadPoolExecutor(max_workers=1) as executor:
        editor_job = executor.submit(execute, invocation)
        try:
            deadline = time.monotonic() + 120
            while not (output / "ready.json").exists():
                if editor_job.done() or time.monotonic() >= deadline:
                    raise ValueError("Owned Editor did not publish a fresh ready receipt")
                time.sleep(0.2)
            ready = json.loads((output / "ready.json").read_text(encoding="utf-8"))
            if ready.get("nonce") != nonce:
                raise ValueError("Stale Editor ready receipt")
            result = asyncio.run(probe(project, output, ready, nonce))
        finally:
            save(output / "complete.json", result or {"success": False, "nonce": nonce})
            launch = editor_job.result()
    if (
        launch["actual_exit_code"] != 0
        or launch["forced_termination"]
        or launch["process_id"] != ready["pid"]
    ):
        raise ValueError("Owned native test did not exit successfully")
    cases = list(ET.parse(output / "results.xml").iter("test-case"))
    if len(cases) != 1 or cases[0].get("fullname") != METHOD or cases[0].get("result") != "Passed":
        raise ValueError("Required native transport body did not pass")
    if list((output / "unused-discovery").glob("*")):
        raise ValueError("Owned transport unexpectedly published discovery metadata")
    result["editor_pid"] = launch["process_id"]
    result["native_passed"] = 1
    save(output / "verification.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--editor", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.project, args.editor, args.output)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
