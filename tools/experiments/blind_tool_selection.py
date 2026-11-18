"""Launch and score blind Unity tool-selection trials with a read-only stdio relay."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from itertools import product
from pathlib import Path, PurePosixPath

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_trace import score_directory

TASKS = ("raycast", "graph", "graph-saved", "game-view", "editor", "context")
LANGUAGES = ("en", "ko", "ja", "es", "ar")
ROLES = ("main", "subagent")


def task_prompt(task: str, language: str, graph_asset: str) -> str:
    """Return the identical user task text for either execution role."""
    with Path(__file__).with_name("blind_tool_tasks.json").open(encoding="utf-8") as stream:
        prompts = json.load(stream)
    request = prompts[task][language].format(graph_asset=graph_asset)
    return f"{request}\n\n{prompts['limits'][language]}"


def delivery_prompt(args: argparse.Namespace, task: str, language: str, role: str) -> str:
    prompt = task_prompt(task, language, args.graph_asset)
    if role == "main":
        return f"Perform this task yourself, without delegating.\n\n{prompt}"
    return (
        f"Delegate the quoted request verbatim to one fresh read-only subagent using "
        f"{args.model} and {args.effort} reasoning. Do not inherit this chat. Use the same "
        f"workspace and connection. Announce that model and effort as benchmark-selected "
        f"and explain each choice before launching. Wait for the subagent and relay its "
        f"findings. Do not inspect the Editor, files, or tool catalog yourself.\n\n"
        f"<request>\n{prompt}\n</request>"
    )


def codex_command(args: argparse.Namespace, case_dir: Path) -> list[str]:
    """Use the installed interactive login and an isolated MCP configuration."""
    proxy = [
        "-X",
        "utf8",
        Path(__file__).with_name("blind_tool_trace.py").resolve().as_posix(),
        "--audit-dir",
        case_dir.as_posix(),
        "--graph-asset",
        args.graph_asset,
    ]
    if args.relay:
        proxy.extend(["--relay", args.relay.as_posix()])
    else:
        proxy.extend(
            [
                "--endpoint",
                args.endpoint,
                "--instance",
                args.instance,
                "--project",
                args.project.as_posix(),
            ]
        )
    settings = {
        "model_reasoning_effort": args.effort,
        "mcp_servers.unityMCP.command": Path(sys.executable).as_posix(),
        "mcp_servers.unityMCP.args": proxy,
        "mcp_servers.unityMCP.startup_timeout_sec": 60,
        "mcp_servers.unityMCP.tool_timeout_sec": 60,
    }
    policy = "never"
    if args.approval == "auto-review":
        settings["approvals_reviewer"] = "auto_review"
        policy = "on-request"
    command = [
        args.codex,
        "-a",
        policy,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-C",
        str(args.project),
        "--model",
        args.model,
        "--disable" if args.delegation == "legacy" else "--enable",
        "multi_agent_v2",
        "--json",
        "--output-last-message",
        str(case_dir / "answer.txt"),
    ]
    if args.delegation == "legacy":
        command.extend(["--enable", "multi_agent"])
    for key, value in settings.items():
        command.extend(["-c", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    return [*command, "-"]


def run_case(
    args: argparse.Namespace, task: str, language: str, role: str, repetition: int
) -> None:
    case_dir = args.output / f"{task}-{language}-{role}-{repetition:02d}"
    case_dir.mkdir()
    metadata = {
        "task": task,
        "language": language,
        "role": role,
        "repetition": repetition,
        "graph_asset": args.graph_asset,
        "model": args.model,
        "effort": args.effort,
        "approval_policy": args.approval,
        "delegation": args.delegation,
        "backend": "trusted-stdio-relay" if args.relay else "unity-mcp-loopback",
        "runner_status": "launch_failed",
    }
    command = codex_command(args, case_dir)
    environment = {**os.environ, "PYTHONUTF8": "1"}
    try:
        with (
            (case_dir / "events.jsonl").open("wb") as events,
            (case_dir / "stderr.txt").open("wb") as errors,
        ):
            process = subprocess.run(
                command,
                cwd=args.project,
                env=environment,
                input=delivery_prompt(args, task, language, role).encode(),
                stdout=events,
                stderr=errors,
                timeout=args.timeout,
                check=False,
            )
            metadata["runner_status"] = "finished" if process.returncode == 0 else "cli_failed"
            metadata["exit_code"] = process.returncode
    except subprocess.TimeoutExpired:
        metadata["runner_status"] = "timed_out"
    except OSError:
        metadata["runner_status"] = "launch_failed"
    finally:
        (case_dir / "case.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    launch = modes.add_parser("run", help="Opt in to authenticated trials against an open Editor")
    launch.add_argument("--project", type=Path, required=True)
    backend = launch.add_mutually_exclusive_group(required=True)
    backend.add_argument("--relay", type=Path, help="Trusted read-only Python MCP relay")
    backend.add_argument("--endpoint", help="Running Unity MCP HTTP loopback endpoint")
    launch.add_argument("--instance", help="Exact Editor Name@hash on the HTTP endpoint")
    launch.add_argument("--output", type=Path, required=True, help="New private output directory")
    launch.add_argument("--codex", default=shutil.which("codex.exe") or shutil.which("codex"))
    launch.add_argument("--model", required=True)
    launch.add_argument("--effort", required=True)
    launch.add_argument("--task", choices=TASKS, action="append", required=True)
    launch.add_argument("--language", choices=LANGUAGES, action="append", required=True)
    launch.add_argument("--role", choices=ROLES, action="append", required=True)
    launch.add_argument("--graph-asset", default="")
    launch.add_argument("--repeat", type=int, default=1)
    launch.add_argument("--timeout", type=int, default=240, help="Seconds per trial")
    launch.add_argument("--approval", choices=("never", "auto-review"), default="never")
    launch.add_argument(
        "--delegation",
        choices=("v2", "legacy"),
        default="v2",
        help="Use legacy native delegation if v2 omits spawn receipts from CLI events",
    )
    score = modes.add_parser("score", help="Score stored observations without launching a client")
    score.add_argument("output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.mode == "run":
        args.project, args.output = map(Path.resolve, (args.project, args.output))
        if not args.codex or not args.project.is_dir():
            parser.error("run requires an installed Codex CLI and the Unity project directory")
        if args.relay:
            args.relay = args.relay.resolve()
            if not args.relay.is_file():
                parser.error("trusted relay script does not exist")
        elif not args.instance or "@" not in args.instance:
            parser.error("--endpoint requires the exact --instance Name@hash")
        elif "raycast" in args.task:
            parser.error("raycast requires a trusted --relay that guarantees read-only raycasts")
        if args.output.is_relative_to(args.project) or args.output.exists():
            parser.error("output must be a new private directory outside the Unity project")
        asset = PurePosixPath(args.graph_asset)
        if {"graph", "graph-saved"}.intersection(args.task) and not args.graph_asset:
            parser.error("--graph-asset is required for graph and graph-saved")
        if args.graph_asset and (
            asset.parts[:1] != ("Assets",)
            or args.graph_asset != asset.as_posix()
            or any(character in args.graph_asset for character in "\\\n\r%?#")
            or ".." in asset.parts
            or asset.suffix != ".shadergraph"
            or args.graph_asset.casefold().startswith("assets/resources/gamedata/")
        ):
            parser.error("graph asset must be a project-relative Shader Graph outside GameData")
        if args.graph_asset:
            graph_path = (args.project / args.graph_asset).resolve()
            assets = (args.project / "Assets").resolve()
            denied = (assets / "Resources" / "GameData").resolve()
            if not graph_path.is_relative_to(assets) or graph_path.is_relative_to(denied):
                parser.error("graph asset must resolve inside the project's permitted Assets tree")
        if min(args.repeat, args.timeout) < 1:
            parser.error("repeat and timeout must be positive")
        cells = list(
            product(
                dict.fromkeys(args.task),
                dict.fromkeys(args.language),
                dict.fromkeys(args.role),
                range(1, args.repeat + 1),
            )
        )
        args.output.mkdir(parents=True)
        (args.output / "matrix.json").write_text(json.dumps(cells) + "\n", encoding="utf-8")
        for task, language, role, repetition in cells:
            print(f"{task}/{language}/{role}/{repetition}", file=sys.stderr, flush=True)
            run_case(args, task, language, role, repetition)
    report = score_directory(args.output)
    print(json.dumps(report, indent=2))
    return int(bool(report["runner_failures"] or report["unrun_cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
