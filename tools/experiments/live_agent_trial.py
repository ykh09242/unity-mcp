"""Run sealed original requests through actual Codex and a disposable Unity editor."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import agent_family_evaluation as previous
from tools.experiments import live_agent_wire as wire

HERE = Path(__file__).resolve().parent
NEW_SHA = "cd8e8fd0edcf63a2b9a295066a2c0cc20ee6a53204fb925ac1f53d4f1d0905b9"
MODEL = "gpt-6.2-sol"
EFFORT = "medium"
TURN_SECONDS = 360


def write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def actor_version(codex):
    with Path(codex).open("rb") as executable:
        sha = hashlib.file_digest(executable, "sha256").hexdigest()
    version = subprocess.check_output([codex, "--version"], text=True).strip()
    return {"version": version, "sha256": sha, "model": MODEL, "reasoning_effort": EFFORT}


def pairs():
    original = [
        (c.request.query, c.request.task, c.request.language, "existing", c.panel)
        for c in previous.cases()
        if c.profile == "core"
    ]
    sealed = (HERE / "live_agent_originals.json").read_bytes()
    if hashlib.sha256(sealed).hexdigest() != NEW_SHA:
        raise ValueError("New original seal mismatch")
    original.extend(
        (query, task, language, "new", "new")
        for task, data in json.loads(sealed)["tasks"].items()
        for language, query in data["queries"].items()
    )
    if len(original) != 71 or len({row[0] for row in original}) != 71:
        raise ValueError("Original request panel changed")
    rows = []
    for i, (query, task, language, panel, prior) in enumerate(
        sorted(original, key=lambda row: previous.digest(row[0]))
    ):
        row = dict(
            query=query,
            task=task,
            language=language,
            panel=panel,
            previous_panel=prior,
            seed=int(previous.digest(query)[:7], 16),
        )
        profiles = ("core", "full") if i % 2 == 0 else ("full", "core")
        rows.append(
            [
                row | {"id": previous.digest([profile, query])[:16], "profile": profile}
                for profile in profiles
            ]
        )
    return rows


def actor_command(codex, cwd, folder, server, instance, row, *, status_dir):
    proxy = [
        "-B",
        "-X",
        "utf8",
        (HERE / "live_agent_wire.py").as_posix(),
        "--server",
        server.as_posix(),
        "--instance",
        instance,
        "--status-dir",
        status_dir.as_posix(),
        "--journal-dir",
        folder.as_posix(),
        "--profile",
        row["profile"],
    ]
    cmd = [
        codex,
        "exec",
        "--approve-for-me",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "-C",
        str(cwd),
        "--model",
        MODEL,
        "--json",
        "--output-last-message",
        str(folder / "answer.txt"),
    ]
    for feature in (
        "apps",
        "plugins",
        "shell_tool",
        "unified_exec",
        "computer_use",
        "multi_agent",
        "multi_agent_v2",
        "shell_snapshot",
    ):
        cmd.extend(["--disable", feature])
    for key, value in {
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "model_reasoning_effort": EFFORT,
        "mcp_servers.unityMCP.command": Path(sys.executable).as_posix(),
        "mcp_servers.unityMCP.args": proxy,
        "mcp_servers.unityMCP.startup_timeout_sec": 90,
        "mcp_servers.unityMCP.tool_timeout_sec": 75,
    }.items():
        cmd.extend(["-c", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    return [*cmd, "-"]


def read_wire(folder):
    calls = []
    inventory = []
    for path in sorted(folder.glob("wire-*.jsonl")):
        requests = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            kind = event.pop("event")
            if kind == "request":
                requests[event.pop("id")] = event
                event.update(success=None, result=None)
                calls.append(event)
            elif kind == "reply":
                request = requests[event["id"]]
                request.update(success=event["success"], result=event["result"])
            else:
                inventory.append(event)
    return sorted(calls, key=lambda c: c["time_ns"]), inventory


async def preflight(server, lane, instance, profile, *, timeout=45):
    transport = StdioTransport(
        command=str(server),
        args=wire.server_arguments(profile, instance),
        # A disposable probe needs replay protection only for its own request budget.
        env={
            **wire.server_environment(lane / "status"),
            "UNITY_MCP_COMMAND_TOTAL_TIMEOUT": str(timeout),
        },
    )
    async with Client(transport, timeout=timeout, init_timeout=60) as client:
        registered = await client.read_resource_mcp("mcpforunity://instances")
        selection = json.loads(registered.contents[0].text)
        if not selection.get("success") or [
            i.get("id") for i in selection.get("instances", [])
        ] != [instance]:
            raise RuntimeError("The trial server must only expose its own disposable editor")
        project = await client.read_resource_mcp("mcpforunity://project/info")
        info = json.loads(project.contents[0].text)
        data = info.get("data") if isinstance(info.get("data"), dict) else {}
        if info.get("success") is not True:
            metrics = data.get("capacity") if isinstance(data.get("capacity"), dict) else {}
            raise RuntimeError(
                f"Isolated project probe refused: code={info.get('code')}, "
                f"execution={data.get('execution')}, hint={info.get('hint')}, "
                f"retry_after_ms={metrics.get('retry_after_ms')}."
            )
        root = data.get("projectRoot")
        if (
            not isinstance(root, str)
            or not root
            or Path(root).resolve() != (lane / "project").resolve()
        ):
            raise RuntimeError("The selected MCP instance could not verify the isolated project")
        tools = sorted(item.name for item in await client.list_tools())
        resources = sorted(str(item.uri) for item in await client.list_resources())
        templates = sorted(
            str(item.uri_template) for item in await client.list_resource_templates()
        )
    if not {"search_unity_tools", "manage_tools"} <= set(tools):
        raise RuntimeError("Expected discovery surface missing from the actual server")
    return {"tools": tools, "resources": resources, "templates": templates}


def run_actor(cmd, query, folder):
    # No role instruction or annotation is added to these exact UTF-8 bytes.
    encoded = query.encode("utf-8")
    ended = False
    errors = []
    started = False
    usage = {}
    item_types = {}
    other_integrations = set()
    host_calls = []
    with tempfile.TemporaryFile() as events, (folder / "host.stderr").open("xb") as log:
        try:
            run = subprocess.run(
                cmd,
                input=encoded,
                stdout=events,
                stderr=log,
                timeout=TURN_SECONDS,
                check=False,
                env=os.environ | {"PYTHONUTF8": "1"},
            )
            status = "completed" if run.returncode == 0 else "host_error"
        except subprocess.TimeoutExpired:
            status = "timeout"
        events.seek(0)
        for line in events:
            try:
                event = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            kind = event.get("type")
            item = event.get("item") or {}
            if kind == "item.completed":
                item_type = item.get("type", "")
                item_types[item_type] = item_types.get(item_type, 0) + 1
                if item_type == "mcp_tool_call":
                    server_name = item.get("server")
                    if server_name not in (None, "codex", "unityMCP"):
                        other_integrations.add(str(server_name))
                    tool_result = item.get("result") or {}
                    failure = item.get("error") or (
                        tool_result.get("content") if tool_result.get("isError") else None
                    )
                    host_calls.append(
                        {
                            "server": server_name,
                            "tool": item.get("tool"),
                            "status": item.get("status"),
                            "error": str(wire.without_secrets(failure))[:1200] if failure else None,
                        }
                    )
            elif kind == "turn.started":
                started = True
            elif kind == "turn.completed":
                ended = True
                usage = event.get("usage", {})
            elif kind in {"error", "turn.failed"}:
                errors.append(str(event.get("message") or event.get("error") or "")[:1600])
                if kind == "turn.failed":
                    status = "model_error"
    answer_file = folder / "answer.txt"
    answer = answer_file.read_text(encoding="utf-8") if answer_file.is_file() else ""
    if status == "completed" and not ended:
        status = "host_error"
    return dict(
        status=status,
        turn_started=started,
        turn_completed=ended,
        answer=answer,
        errors=errors,
        usage=usage,
        prompt_sha256=hashlib.sha256(encoded).hexdigest(),
        host_items=item_types,
        host_calls=host_calls,
        other_integrations=sorted(other_integrations),
    )


def run_case(row, lane, receipts, codex, server, instance):
    from tools.experiments.live_editor_fixture.lane import request

    folder = receipts / row["id"]
    folder.mkdir(exist_ok=True)
    result_path = folder / "row.json"
    prior = json.loads(result_path.read_bytes()) if result_path.exists() else {}
    attempts = prior.get("attempts", [])
    if prior and (prior.get("delivered") or len(attempts) >= 2):
        return prior
    run_dir = folder / f"attempt-{len(attempts) + 1}"
    run_dir.mkdir()
    started = time.monotonic()
    result = row | dict(
        lane=lane.name,
        model=MODEL,
        effort=EFFORT,
        delivered=False,
        calls=[],
        answer="",
        status="precondition",
        attempts=list(attempts),
    )
    prepared = False
    commissioning = False
    try:
        before = request(lane, "prepare", row["task"], row["language"], row["seed"])
        if not before.get("ok") or before.get("instance") != instance:
            raise RuntimeError(before.get("error") or "Native fixture identity mismatch")
        result.update(before=before["state"], availability=before.get("availability", {}))
        prepared = True
        if result["availability"].get("ready") is False:
            result["error"] = "Fixture reported a missing prerequisite"
        else:
            result["status"] = "host_error"
            with tempfile.TemporaryDirectory(prefix="unity-observation-client-") as neutral:
                commissioning = True
                cmd = actor_command(
                    codex, Path(neutral), run_dir, server, instance, row, status_dir=lane / "status"
                )
                commissioning = False
                result.update(run_actor(cmd, row["query"], run_dir))
    except (OSError, ValueError, IndexError, KeyError, RuntimeError, TimeoutError) as exc:
        result["error"] = str(exc)
        if commissioning:
            result["commissioning_failed"] = True
    finally:
        if prepared:
            try:
                after = request(lane, "snapshot", row["task"], row["language"], row["seed"])
                if not after.get("ok") or after.get("instance") != instance:
                    raise RuntimeError(after.get("error") or "Native snapshot identity mismatch")
                result["after"] = after["state"]
            except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as exc:
                result["snapshot_error"] = str(exc)
        calls, inventory = read_wire(run_dir)
        result.update(
            calls=calls, inventory=inventory, seconds=round(time.monotonic() - started, 3)
        )
        result["delivered"] = bool(result.get("turn_completed") or result["answer"] or calls)
        result["attempts"].append(
            {key: result.get(key) for key in ("status", "delivered", "seconds")}
        )
        write(run_dir / "row.json", result)
        write(result_path, result)
    return result


def run_lane(lane, assigned, receipts, codex, server):
    from tools.experiments.live_editor_fixture.lane import request

    ping = request(lane, "ping")
    instance = ping.get("instance")
    if (
        not ping.get("ok")
        or not instance
        or Path(ping["project_root"]).resolve() != (lane / "project").resolve()
    ):
        raise RuntimeError(f"Private editor identity unavailable for {lane.name}")
    inventories = {
        profile: asyncio.run(preflight(server, lane, instance, profile))
        for profile in ("core", "full")
    }
    if not set(inventories["core"]["tools"]) < set(inventories["full"]["tools"]):
        raise RuntimeError("Core and full did not expose distinct expected inventories")
    write(receipts / (lane.name + "-inventories.json"), inventories)
    for row in assigned:
        result = run_case(row, lane, receipts, codex, server, instance)
        print(
            row["id"],
            row["profile"],
            row["task"],
            result["status"],
            len(result["calls"]),
            flush=True,
        )
        if result.get("commissioning_failed"):
            raise RuntimeError(f"Lane stopped before the next submission: {result['error']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("manifest", "run"))
    parser.add_argument(
        "--root", required=True, type=Path, help="Private lane and receipt directory"
    )
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument(
        "--lane", type=int, action="append", help="Run only the specified lane indices"
    )
    parser.add_argument("--case", action="append", help="Resume only the specified sealed case IDs")
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--server",
        type=Path,
        default=Path(sys.executable).with_name(
            "mcp-for-unity.exe" if os.name == "nt" else "mcp-for-unity"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(exist_ok=True, parents=True)
    rows = pairs()
    write(root / "manifest.json", [row for pair in rows for row in pair])
    if args.action == "manifest":
        return
    codex = shutil.which(args.codex)
    if not codex or not args.server.is_file() or not 1 <= args.lanes <= 16:
        parser.error(
            "An installed Codex CLI, production Unity MCP server and valid lane count are required"
        )
    fingerprint = actor_version(codex)
    record = root / "actor-version.json"
    if record.exists() and json.loads(record.read_bytes()) != fingerprint:
        raise RuntimeError("The actor executable or model settings changed within this run")
    write(record, fingerprint)
    receipts = root / "receipts-reviewed"
    receipts.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.lanes) as executor:
        futures = []
        for index in range(args.lanes):
            assigned = [
                row
                for pair in rows[index :: args.lanes]
                for row in pair
                if not args.case or row["id"] in args.case
            ]
            if assigned and (args.lane is None or index in args.lane):
                futures.append(
                    executor.submit(
                        run_lane,
                        root / f"lane-{index}",
                        assigned,
                        receipts,
                        codex,
                        args.server.resolve(),
                    )
                )
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
