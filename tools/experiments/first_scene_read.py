"""Measure a Unity MCP installation path through a project-bound native scene read."""

import argparse
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any


def invoke(command: Sequence[str], timeout: float, *, json_reply: bool) -> dict[str, Any]:
    """Record wall time and status without retaining command output or credentials."""
    start = perf_counter()
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE if json_reply else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"seconds": perf_counter() - start, "error": type(error).__name__}
    sample: dict[str, Any] = {"seconds": perf_counter() - start, "exit_code": result.returncode}
    if json_reply:
        sample["stdout_bytes"] = len(result.stdout)
        try:
            payload = json.loads(result.stdout)
        except (UnicodeError, ValueError):
            payload = None
        sample["payload"] = payload if isinstance(payload, dict) else {}
    return sample


def measure(args: argparse.Namespace) -> dict[str, Any]:
    """Verify the requested target, report pin diagnosis and match an independent scene path."""
    start = perf_counter()
    command = [
        args.cli,
        "--no-project-context",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--instance",
        args.instance,
        "--expect-project",
        str(args.project.resolve()),
        "--format",
        "json",
    ]
    stages: dict[str, Any] = {}
    report: dict[str, Any] = {
        "schema_version": "unity-mcp/first-scene-read@1",
        "arm": args.arm,
        "setup_included": bool(args.setup_command),
        "target": args.instance,
        "project": str(args.project.resolve()),
        "expected_scene": args.scene,
        "outcome": "setup_failed",
        "stages": stages,
    }

    def with_elapsed() -> dict[str, Any]:
        return {**report, "seconds_to_outcome": perf_counter() - start}

    if args.setup_command:
        stages["setup"] = invoke(args.setup_command, args.timeout, json_reply=False)
        if stages["setup"].get("exit_code") != 0:
            return with_elapsed()

    stages["doctor"] = invoke([*command, "doctor"], args.timeout, json_reply=True)
    envelope = stages["doctor"].pop("payload", {})
    diagnostic = envelope.get("data") or {}
    target = diagnostic.get("target") or {}
    verified = (
        envelope.get("schema_version") == "unity-mcp/doctor-cli@1"
        and envelope.get("requested_target") == args.instance
        and envelope.get("connection_verified") is True
        and diagnostic.get("connection_verified") is True
        and target.get("instance_id") == args.instance
        and target.get("execution_environment") == "editor"
        and diagnostic.get("checks", {}).get("native_read", {}).get("status") == "passed"
    )
    comparison = diagnostic.get("source_comparison")
    report["doctor"] = {
        "reason": envelope.get("reason"),
        "next_action": envelope.get("next_action"),
        "source_comparison": comparison,
        "protocol": envelope.get("observed_protocol"),
        "editor_version": diagnostic.get("editor_version"),
        "package": diagnostic.get("package"),
        "server": diagnostic.get("server"),
        "target": target,
    }
    report["outcome"] = "connection_unverified"
    if not verified:
        return with_elapsed()
    if comparison == "mismatch":
        server_commit = (diagnostic.get("server") or {}).get("source_commit")
        pin = (diagnostic.get("package") or {}).get("effective_server_commit")
        actionable = (
            isinstance(server_commit, str)
            and isinstance(pin, str)
            and server_commit != pin
            and envelope.get("reason") == "source_mismatch"
            and envelope.get("next_action") == "restart_client_server"
        )
        report["outcome"] = "mismatch_diagnosed" if actionable else "mismatch_unverified"
        return with_elapsed()
    if args.arm == "mismatched-pin":
        report["outcome"] = "mismatch_unverified"
        return with_elapsed()

    stages["scene"] = invoke([*command, "scene", "active"], args.timeout, json_reply=True)
    scene = stages["scene"].pop("payload", {})
    report["outcome"] = "scene_unverified"
    if stages["scene"].get("exit_code") == 0 and scene.get("success") is True:
        actual = scene.get("data") or {}
        report["observed_scene"] = {key: actual.get(key) for key in ("name", "path", "isLoaded")}
        if actual.get("path") == args.scene and actual.get("isLoaded") is True:
            report["outcome"] = (
                "scene_verified" if comparison == "matched" else "scene_verified_source_unknown"
            )
    return with_elapsed()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("cold", "warm", "mismatched-pin"), required=True)
    parser.add_argument("--cli", default="unity-mcp", help="CLI executable from the installation")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--scene", required=True, help="Independent exact Assets/*.unity path")
    parser.add_argument("--output", type=Path, required=True, help="New private JSON receipt")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--setup-command-file", type=Path, help="Optional JSON array of trusted setup command argv"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 < args.timeout <= 3600:
        parser.error("invalid port or timeout")
    if not re.fullmatch(r"[^/\\\x00-\x1f\x7f]{1,128}@[0-9a-fA-F]{8,64}", args.instance):
        parser.error("an exact Name@hash Editor instance is required")
    scene = PurePosixPath(args.scene)
    if (
        not args.scene.startswith("Assets/")
        or scene.suffix != ".unity"
        or scene.as_posix() != args.scene
        or ".." in scene.parts
    ):
        parser.error("use the exact project-relative saved scene path")
    args.setup_command = None
    if args.setup_command_file:
        command = json.loads(args.setup_command_file.read_bytes())
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
        ):
            parser.error("setup command must be a JSON array of nonempty argument strings")
        args.setup_command = command
    with args.output.open("x", encoding="utf-8") as output:
        report = measure(args)
        json.dump(report, output, indent=2)
        output.write("\n")
    return int(report["outcome"] not in {"scene_verified", "mismatch_diagnosed"})


if __name__ == "__main__":
    raise SystemExit(main())
