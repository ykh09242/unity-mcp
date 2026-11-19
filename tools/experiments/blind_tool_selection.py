"""Launch and score blind Unity tool-selection trials with a read-only stdio relay."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from hashlib import sha256
from itertools import product
from pathlib import Path, PurePosixPath
from uuid import uuid4

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_answers import answer_instruction
from tools.experiments.blind_tool_comparison import compare_directories, paired_schedule
from tools.experiments.blind_tool_trace import score_case, score_directory

TASKS = ("raycast", "graph", "graph-saved", "graph-open", "game-view", "editor", "context")
LANGUAGES = ("en", "ko", "ja", "es", "ar", "tr")
ROLES = ("main", "subagent")
STUDY_TASKS = ("game-view", "graph-saved", "graph-open")
STUDY = "consumer-discovery"


def task_prompt(task: str, language: str, graph_asset: str) -> str:
    """Return the identical user task text for either execution role."""
    with Path(__file__).with_name("blind_tool_tasks.json").open(encoding="utf-8") as stream:
        prompts = json.load(stream)
    request = prompts[task][language].format(graph_asset=graph_asset)
    return f"{request}\n\n{prompts['limits'][language]}"


def delivery_prompt(args: argparse.Namespace, task: str, language: str, role: str) -> str:
    prompt = task_prompt(task, language, args.graph_asset)
    if getattr(args, "study", None) == STUDY:
        with Path(__file__).with_name("blind_tool_tasks.json").open(encoding="utf-8") as stream:
            prompt += "\n" + json.load(stream)["observation_scope"][language]
    if getattr(args, "answer_format", "prose") == "json":
        prompt += "\n\n" + answer_instruction(task, getattr(args, "answer_protocol", "v1"))
    if role == "main":
        return f"Perform this task yourself, without delegating.\n\n{prompt}"
    return (
        f"Delegate the quoted request verbatim to one fresh read-only subagent using "
        f"{args.model} and {args.effort} reasoning. Do not inherit this chat. Use the same "
        f"workspace and connection. Announce that model and effort as benchmark-selected "
        f"and explain each choice before launching. Wait for the subagent and relay its "
        f"findings using the requested answer format. Do not inspect the Editor, files, "
        f"or tool catalog yourself.\n\n"
        f"<request>\n{prompt}\n</request>"
    )


def proxy_args(args: argparse.Namespace, case_dir: Path) -> list[str]:
    """Keep the same scoped Unity transport for isolated and configured comparisons."""
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
    return proxy


def codex_command(args: argparse.Namespace, case_dir: Path) -> list[str]:
    """Use the installed interactive login and an isolated MCP configuration."""
    settings = {
        "model_reasoning_effort": args.effort,
        "mcp_servers.unityMCP.command": Path(sys.executable).as_posix(),
        "mcp_servers.unityMCP.args": proxy_args(args, case_dir),
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
    prompt = delivery_prompt(args, task, language, role)
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
        "backend": "installed-guarded"
        if args.installed
        else "trusted-stdio-relay"
        if args.relay
        else "unity-mcp-loopback",
        "host_mode": args.host_mode,
        "runner_status": "launch_failed",
        "project": str(args.project),
        "instance": args.instance,
        "answer_format": getattr(args, "answer_format", "prose"),
        "answer_protocol": getattr(args, "answer_protocol", "v1"),
        "prompt_digest": sha256(prompt.encode()).hexdigest(),
        **getattr(args, "case_metadata", {}),
    }
    environment = {**os.environ, "PYTHONUTF8": "1"}
    try:
        if args.host_mode == "configured":
            import anyio
            from tools.experiments.blind_tool_host import run_configured

            anyio.run(run_configured, args, case_dir, prompt, metadata)
            metadata.update(runner_status="finished", exit_code=0)
            return
        command = codex_command(args, case_dir)
        with (
            (case_dir / "events.jsonl").open("wb") as events,
            (case_dir / "stderr.txt").open("wb") as errors,
        ):
            process = subprocess.run(
                command,
                cwd=args.project,
                env=environment,
                input=prompt.encode(),
                stdout=events,
                stderr=errors,
                timeout=args.timeout,
                check=False,
            )
            metadata["runner_status"] = "finished" if process.returncode == 0 else "cli_failed"
            metadata["exit_code"] = process.returncode
    except (subprocess.TimeoutExpired, TimeoutError):
        metadata["runner_status"] = "timed_out"
    except ExceptionGroup as error:
        metadata["runner_status"] = "timed_out" if error.subgroup(TimeoutError) else "host_failed"
        metadata["failure_type"] = type(error.exceptions[0]).__name__
    except (OSError, ValueError) as error:
        metadata["runner_status"] = "launch_failed"
        metadata["failure_type"] = type(error).__name__
    finally:
        if (getattr(args, "study", None) or getattr(args, "pair_gate", None)) and metadata.get(
            "runner_phase"
        ) == "model_turn":
            metadata["editor_end_state"] = probe_state(args)
        (case_dir / "case.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    launch = argparse.ArgumentParser(add_help=False)
    launch.add_argument("--project", type=Path, required=True)
    launch.add_argument("--instance", help="Exact Editor Name@hash on the HTTP endpoint")
    launch.add_argument("--output", type=Path, required=True, help="New private output directory")
    launch.add_argument("--codex", default=shutil.which("codex.exe") or shutil.which("codex"))
    launch.add_argument("--model", required=True)
    launch.add_argument("--effort", required=True)
    launch.add_argument("--task", choices=TASKS, action="append")
    launch.add_argument("--language", choices=LANGUAGES, action="append")
    launch.add_argument("--role", choices=ROLES, action="append")
    launch.add_argument(
        "--host-mode",
        choices=("isolated", "configured"),
        help="Use the normal Codex host and competing tools, or the isolated control",
    )
    launch.add_argument(
        "--unity-server",
        default="unityMCP",
        help="Installed Unity MCP server name in configured mode",
    )
    launch.add_argument("--graph-asset", default="")
    launch.add_argument(
        "--installed-endpoint",
        help="Loopback endpoint for forwarding target-pinned reads to the verified installed server",
    )
    launch.add_argument("--repeat", type=int, default=1)
    launch.add_argument("--timeout", type=int, default=240, help="Seconds per trial")
    launch.add_argument("--approval", choices=("never", "auto-review"), default="never")
    launch.add_argument("--answer-format", choices=("json", "prose"))
    launch.add_argument(
        "--answer-protocol",
        choices=("v1", "v2", "v3"),
        help="JSON answer schema (v1 reproduces the earlier prompt)",
    )
    launch.add_argument("--state-probe", type=Path, help="Trusted read-only Python state probe")
    launch.add_argument(
        "--live-child",
        action="store_true",
        help="Capture a v2 subagent's own host events and directly verify its parent and session",
    )
    launch.add_argument(
        "--delegation",
        choices=("v2", "legacy"),
        default="v2",
        help="Use legacy native delegation if v2 omits spawn receipts from CLI events",
    )
    single = modes.add_parser("run", parents=[launch], help="Run an observation matrix")
    matched = modes.add_parser("matched", parents=[launch], help="Run counterbalanced pairs")
    matched.add_argument(
        "--prepare-pairs",
        action="store_true",
        help="Prepare both configured trial hosts before delivering either prompt",
    )
    matched.add_argument(
        "--competitor",
        action="append",
        help="Require this exact set of competing MCP namespaces in both prepared hosts (repeatable)",
    )
    matched.add_argument(
        "--disable-competitor",
        action="append",
        help="Disable an unselected MCP namespace in the two ephemeral study processes only",
    )
    matched.add_argument(
        "--study", choices=[STUDY], help="Run the predeclared six-language consumer study"
    )
    for command, prefix in [(single, ""), (matched, "baseline-"), (matched, "candidate-")]:
        backend = command.add_mutually_exclusive_group(required=True)
        backend.add_argument(
            f"--{prefix}relay", type=Path, help="Trusted read-only Python MCP relay"
        )
        backend.add_argument(f"--{prefix}endpoint", help="Running Unity MCP loopback endpoint")
        backend.add_argument(
            f"--{prefix}installed",
            action="store_true",
            help="Use the configured host's installed Unity server unchanged",
        )
    compare = modes.add_parser("compare", help="Re-score two private matrix directories")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    score = modes.add_parser("score", help="Score stored observations without launching a client")
    score.add_argument("output", type=Path)
    return parser


def validate_backend(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.installed:
        if (
            args.host_mode != "configured"
            or not args.instance
            or "@" not in args.instance
            or not args.installed_endpoint
        ):
            parser.error(
                "--installed requires --host-mode configured, --installed-endpoint and the exact --instance"
            )
    matched.add_argument(
        "--disable-plugins",
        action="store_true",
        help="Disable dynamic plugins in both ephemeral trial processes for a declared competitor cohort",
    )
        if "raycast" in args.task:
            parser.error("raycast requires a trusted --relay that guarantees read-only raycasts")
        from tools.experiments.blind_tool_http import loopback_port

        try:
            loopback_port(args.installed_endpoint)
        except ValueError:
            parser.error(
                "installed endpoint must be an HTTP loopback URL without credentials or query"
            )
    elif args.relay:
        args.relay = args.relay.resolve()
        if not args.relay.is_file():
            parser.error("trusted relay script does not exist")
    elif not args.instance or "@" not in args.instance:
        parser.error("--endpoint requires the exact --instance Name@hash")
    elif "raycast" in args.task:
        parser.error("raycast requires a trusted --relay that guarantees read-only raycasts")
    else:
        from tools.experiments.blind_tool_http import loopback_port

        try:
            loopback_port(args.endpoint)
        except ValueError:
            parser.error("trial endpoint must be an HTTP loopback URL without credentials or query")


def probe_state(args: argparse.Namespace) -> dict | None:
    if not args.state_probe:
        return None
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(args.state_probe), str(args.project)],
            capture_output=True,
            timeout=min(args.timeout, 60),
            check=True,
        )
        state = json.loads(result.stdout)
        return state if isinstance(state, dict) and state else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.mode in {"run", "matched"}:
        study = getattr(args, "study", None)
        if study:
            if any((args.task, args.language, args.role)):
                parser.error("--study supplies its task, language and role axes")
            if (
                args.host_mode == "isolated"
                or not args.state_probe
                or args.answer_format == "prose"
            ):
                parser.error(
                    "--study requires a configured host, a state probe and structured answers"
                )
            args.task, args.language, args.role = STUDY_TASKS, LANGUAGES, ROLES
        elif not all((args.task, args.language, args.role)):
            parser.error("provide --task, --language and --role or select --study")
        prepared = getattr(args, "prepare_pairs", False)
        args.host_mode = args.host_mode or ("configured" if study or prepared else "isolated")
        args.answer_protocol = args.answer_protocol or ("v3" if study or prepared else "v2")
        if prepared and (
            args.host_mode != "configured"
            or not args.state_probe
            or args.answer_format == "prose"
            or args.answer_protocol != "v3"
        ):
            parser.error(
                "prepared pairs require a configured host, a state probe and V3 JSON answers"
            )
        allowed = set(getattr(args, "competitor", None) or ())
        disabled = set(getattr(args, "disable_competitor", None) or ())
        if allowed or disabled or getattr(args, "disable_plugins", False):
            from tools.experiments.blind_tool_host import IDENTIFIER, RECORDED_SERVER

            if (
                not prepared
                or not allowed
                or allowed & disabled
                or any(
                    not IDENTIFIER.fullmatch(n)
                    or "." in n
                    or n in (args.unity_server, RECORDED_SERVER)
                    for n in allowed | disabled
                )
            ):
                parser.error("declare separate exact competitor namespaces for a prepared pair")
        args.project, args.output = map(Path.resolve, (args.project, args.output))
        if args.host_mode == "configured":
            from tools.experiments.blind_tool_host import IDENTIFIER, RECORDED_SERVER

            if (
                not IDENTIFIER.fullmatch(args.unity_server)
                or args.unity_server == RECORDED_SERVER
                or "." in args.unity_server
            ):
                parser.error("invalid configured --unity-server name")
        if not args.codex or not args.project.is_dir():
            parser.error("run requires an installed Codex CLI and the Unity project directory")
        args.answer_format = args.answer_format or ("json" if args.mode == "matched" else "prose")
        if args.state_probe:
            args.state_probe = args.state_probe.resolve()
            if not args.state_probe.is_file():
                parser.error("trusted state probe script does not exist")
        if args.output.is_relative_to(args.project) or args.output.exists():
            parser.error("output must be a new private directory outside the Unity project")
        asset = PurePosixPath(args.graph_asset)
        if any(task.startswith("graph") for task in args.task) and not args.graph_asset:
            parser.error("--graph-asset is required for graph tasks")
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
        arms = {}
        for arm in ("baseline", "candidate") if args.mode == "matched" else ("single",):
            selected = argparse.Namespace(**vars(args))
            if arm != "single":
                selected.output = args.output / arm
                selected.relay = getattr(args, arm + "_relay")
                selected.endpoint = getattr(args, arm + "_endpoint")
                selected.installed = getattr(args, arm + "_installed")
            validate_backend(parser, selected)
            arms[arm] = selected
        for selected in arms.values():
            selected.output.mkdir(parents=True)
            (selected.output / "matrix.json").write_text(json.dumps(cells) + "\n", encoding="utf-8")
        schedule = (
            paired_schedule(cells) if args.mode == "matched" else [("single", c) for c in cells]
        )
        if study:
            schedule = []
            for cell in cells:
                task, language, role, repeat = cell
                offset = (
                    STUDY_TASKS.index(task) + LANGUAGES.index(language) + ROLES.index(role) + repeat
                )
                order = ("baseline", "candidate") if offset % 2 else ("candidate", "baseline")
                schedule.extend((arm, cell) for arm in order)
            manifest = {
                "study": study,
                "tasks": args.task,
                "languages": LANGUAGES,
                "held_out_language": "tr",
                "roles": ROLES,
                "repeats": args.repeat,
                "answer_protocol": args.answer_protocol,
                "planned_pairs": len(cells),
                "scheduled_turns": len(schedule),
                "pilot_task": "game-view",
                "pilot_language": "en",
                "pilot_included_in_score": False,
            }
            (args.output / "study.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
        schedule_id = str(uuid4())
        (args.output / "schedule.json").write_text(json.dumps(schedule) + "\n", encoding="utf-8")
        pilots = {}
        if study:
            from tools.experiments.blind_tool_preflight import prepared_pilots, run_pilots

            pilots = (prepared_pilots if prepared else run_pilots)(
                arms, args.output, args.role, run_case, score_case
            )
        for index, (arm, cell) in enumerate(schedule, 1):
            if study and not pilots[cell[2]]["supported"]:
                continue
            selected = arms[arm]
            selected.case_metadata = {
                "arm": arm,
                "schedule_id": schedule_id,
                "execution_order": index,
                "editor_start_state": None if study or prepared else probe_state(selected),
                **({"pair_mode": "prepared"} if prepared else {}),
                **({"study": study} if study else {}),
                "backend_identity": sha256(selected.relay.read_bytes()).hexdigest()
                if selected.relay
                else selected.endpoint,
            }
            print(f"{arm}/{cell}", file=sys.stderr, flush=True)
            if not prepared:
                run_case(selected, *cell)
            elif index % 2 == 0:
                from tools.experiments.blind_tool_pair import prepared_pair

                prepared_pair(arms, [schedule[index - 2][0], arm], cell, args.output, run_case)
        if args.mode == "matched":
            args.baseline, args.candidate = arms["baseline"].output, arms["candidate"].output
    if args.mode in {"matched", "compare"}:
        report = compare_directories(args.baseline, args.candidate)
        print(json.dumps(report, indent=2))
        return int(
            report["eligible_pairs"] != report["planned_pairs"] or not report["planned_pairs"]
        )
    report = score_directory(args.output)
    print(json.dumps(report, indent=2))
    return int(bool(report["runner_failures"] or report["unrun_cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
