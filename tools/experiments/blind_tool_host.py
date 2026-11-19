"""Run a blind observation in an ephemeral session with the user's configured MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID

import anyio

from tools.check_codex_interop import AppServer, InteropFailure, close_process

RECORDED_SERVER = "unityMCP_trial"
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
DOCTOR = "mcpforunity://doctor"
TRIAL_SOURCE = "mcpforunity://trial/source"


def launch_settings(args, case_dir):
    """CLI overrides never merge a stdio command into an installed HTTP transport."""
    server = args.unity_server
    command = [args.codex, "app-server"]
    if not args.installed:
        from tools.experiments.blind_tool_selection import proxy_args

        command.extend(["-c", f"mcp_servers.{server}.enabled=false"])
        server = RECORDED_SERVER
        for key, value in {
            "command": str(Path(sys.executable)),
            "args": proxy_args(args, case_dir),
            "startup_timeout_sec": 60,
            "tool_timeout_sec": 60,
        }.items():
            command.extend(["-c", f"mcp_servers.{server}.{key}={json.dumps(value)}"])
    command.extend(["--enable" if args.delegation == "v2" else "--disable", "multi_agent_v2"])
    if args.delegation == "legacy":
        command.extend(["--enable", "multi_agent"])
    return command, server


def _commit(value):
    return value if isinstance(value, str) and re.fullmatch("[a-f0-9]{40}", value) else None


def _doctor_source(receipt, instance):
    data = receipt.get("data") if receipt.get("success") is True else None
    if not isinstance(data, dict) or (data.get("target") or {}).get("instance_id") != instance:
        return {}
    return {
        "server_commit": _commit((data.get("server") or {}).get("source_commit")),
        "package_commit": _commit((data.get("package") or {}).get("effective_server_commit")),
    }


async def observe_host(client, thread_id, unity_server, instance, *, installed):
    """Project the same thread's catalog. Keep stable names, never credentials or descriptions."""
    source = {"kind": "unknown", "server_commit": None, "package_commit": None}
    result = {
        "status": "incomplete",
        "unity": {"tools": [], "resources": [], "templates": []},
        "competing_servers": {},
        "competitor_digest": None,
        "source": source,
    }
    params = {"threadId": thread_id, "limit": 64}
    seen = set()
    names = set()
    healthy = True
    try:
        for _ in range(16):
            page = await client.request("mcpServerStatus/list", params)
            for item in page["data"]:
                name = item["name"]
                tools = item.get("tools")
                if not IDENTIFIER.fullmatch(name) or name in names or not isinstance(tools, dict):
                    return result
                names.add(name)
                if len(tools) > 4096 or not all(IDENTIFIER.fullmatch(tool) for tool in tools):
                    return result
                # Disabled configuration entries are not selectable competitors.
                if item.get("runtimeStatus") == "disabled":
                    continue
                if item.get("toolsError") or item.get("runtimeStatus") != "connected":
                    healthy = False
                if name != unity_server:
                    if tools:
                        result["competing_servers"][name] = sorted(tools)
                    continue
                result["unity"]["tools"] = sorted(tools)
                for field, source_key, key in (
                    ("resources", "resources", "uri"),
                    ("templates", "resourceTemplates", "uriTemplate"),
                ):
                    result["unity"][field] = sorted(
                        uri
                        for row in item.get(source_key, [])
                        if isinstance(row, dict)
                        and isinstance(uri := row.get(key), str)
                        and uri.startswith("mcpforunity://")
                        and len(uri) <= 256
                        and "Assets/" not in uri
                        and "@" not in uri
                    )
            cursor = page.get("nextCursor")
            if cursor is None:
                if result["unity"]["tools"] and healthy:
                    result["status"] = "observed"
                    result["competitor_digest"] = hashlib.sha256(
                        json.dumps(result["competing_servers"], sort_keys=True).encode()
                    ).hexdigest()
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                return result
            seen.add(cursor)
            params = {**params, "cursor": cursor}
        if installed and instance and DOCTOR in result["unity"]["resources"]:
            reply = await client.request(
                "mcpServer/resource/read",
                {
                    "threadId": thread_id,
                    "server": unity_server,
                    "uri": DOCTOR,
                    "_meta": {"unity_instance": instance},
                },
            )
            blocks = reply.get("contents", [])
            if len(blocks) == 1 and isinstance(blocks[0].get("text"), str):
                receipt = json.loads(blocks[0]["text"])
                source.update(_doctor_source(receipt, instance))
                if source["server_commit"] or source["package_commit"]:
                    source["kind"] = "same_thread_doctor"
        elif not installed and TRIAL_SOURCE in result["unity"]["resources"]:
            reply = await client.request(
                "mcpServer/resource/read",
                {"threadId": thread_id, "server": unity_server, "uri": TRIAL_SOURCE},
            )
            blocks = reply.get("contents") or []
            if len(blocks) == 1:
                receipt = json.loads(blocks[0]["text"])
                commit = _commit(receipt.get("source_commit"))
                digest = receipt.get("archive_sha256")
                if (
                    receipt.get("kind") == "archived_server"
                    and commit
                    and isinstance(digest, str)
                    and re.fullmatch("[0-9a-fA-F]{64}", digest)
                ):
                    source.update(
                        kind="same_thread_archive",
                        server_commit=commit,
                        archive_sha256=digest.lower(),
                    )
    except (InteropFailure, TimeoutError, OSError, KeyError, TypeError, ValueError, AttributeError):
        pass
    return result


def _host_id(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _tool_error(text):
    from tools.experiments.blind_tool_trace import APPROVAL

    return "approval blocked" if APPROVAL.search(str(text)) else "tool failed"


def project_item(item, server, asset):
    """Convert only evidence needed by the scorer. Discard other tools' arguments and results."""
    from tools.experiments.blind_tool_trace import saved_read_grade, saved_graph_facts

    kind = item.get("type")
    projected = {"id": item.get("id"), "status": item.get("status")}
    if kind == "agentMessage":
        return {
            **projected,
            "type": "agent_message",
            "phase": item.get("phase"),
            "text": item.get("text", ""),
        }
    if kind == "collabAgentToolCall":
        tool = {"spawnAgent": "spawn_agent", "wait": "wait"}.get(item.get("tool"))
        if tool:
            return {
                **projected,
                "type": "collab_tool_call",
                "tool": tool,
                "receiver_thread_ids": [
                    peer for value in item.get("receiverThreadIds", []) if (peer := _host_id(value))
                ],
            }
        return None
    if kind == "mcpToolCall":
        name = item.get("tool")
        service = item.get("server")
        if not all(
            isinstance(value, str) and IDENTIFIER.fullmatch(value) for value in (name, service)
        ):
            return None
        projected.update(type="mcp_tool_call", server=service, tool=name)
        if service == server:
            from tools.experiments.blind_tool_trace import (
                RAYCAST,
                decision,
                route_name,
                observation,
                unpack,
            )

            args = item.get("arguments") or {}
            if not isinstance(args, dict):
                return projected
            if name == "read_mcp_resource":
                route, arguments = route_name("resources/read", args)
            else:
                route, arguments = route_name("tools/call", {"name": name, "arguments": args})
            channel = decision(route, arguments, asset)
            # A captured host read is evidence on the installed route, separate from wire evidence.
            if channel in ("unity", "discovery"):
                clean = {
                    k: v for k, v in args.items() if k in {"action", "page_number", "page_size"}
                }
                if asset and args.get("path") == asset:
                    clean["path"] = asset
                if name == "read_mcp_resource":
                    clean["uri"] = args.get("uri")
                projected["arguments"] = clean
                status, data = unpack({"result": item.get("result")})
                if route == RAYCAST and data.get("status") == "failed":
                    status = "error"
                projected["observed_result"] = {"status": status, "facts": observation(route, data)}
        if item.get("error"):
            projected["error"] = {"message": _tool_error(item["error"])}
        return projected
    if kind == "commandExecution":
        projected.update(type="command_execution", exit_code=item.get("exitCode"))
        command, output = item.get("command") or "", item.get("aggregatedOutput") or ""
        if asset and asset.casefold() in command.replace("\\", "/").casefold():
            grade = (
                saved_read_grade(command, output, asset) if item.get("exitCode") == 0 else "none"
            )
            if grade in ("observed", "review"):
                projected["saved_read"] = {
                    "grade": grade,
                    "facts": saved_graph_facts(output) if grade == "observed" else {},
                }
        if item.get("exitCode") and _tool_error(output) == "approval blocked":
            projected["aggregated_output"] = "approval blocked"
        return projected
    if kind in ("dynamicToolCall", "webSearch", "imageView"):
        name = item.get("tool") or kind
        return {
            **projected,
            "type": "other_tool",
            "tool": name if isinstance(name, str) and IDENTIFIER.fullmatch(name) else kind,
        }
    return None


async def run_configured(args, case_dir, prompt, metadata):
    """Use the app-server protocol to observe the exact ephemeral thread before its model turn."""
    from tools.experiments.blind_tool_trace import thread_digest

    command, server = launch_settings(args, case_dir)
    metadata["unity_server"] = server
    process = await anyio.open_process(
        command,
        cwd=args.project,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    async with anyio.create_task_group() as workers:
        client = AppServer(process)
        workers.start_soon(client.read_messages)
        try:
            with anyio.fail_after(args.timeout):
                await client.request(
                    "initialize",
                    {
                        "clientInfo": {"name": "unity_mcp_blind_eval", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await client.send({"method": "initialized"})
                options = {
                    "cwd": str(args.project),
                    "model": args.model,
                    "ephemeral": True,
                    "approvalPolicy": "never" if args.approval == "never" else "on-request",
                    "sandbox": "read-only",
                }
                if args.approval == "auto-review":
                    options["approvalsReviewer"] = "auto_review"
                thread = _host_id((await client.request("thread/start", options))["thread"]["id"])
                if not thread:
                    raise ValueError("No host thread ID")
                surface = await observe_host(
                    client, thread, server, args.instance, installed=args.installed
                )
                (case_dir / "host.json").write_text(
                    json.dumps(surface, indent=2) + "\n", encoding="utf-8"
                )
                metadata["competitor_digest"] = surface["competitor_digest"]
                metadata["observed_server_commit"] = surface["source"]["server_commit"]
                if metadata["observed_server_commit"]:
                    metadata["backend_identity"] = metadata["observed_server_commit"]
                reply = await client.request(
                    "turn/start",
                    {
                        "threadId": thread,
                        "effort": args.effort,
                        "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    },
                )
                turn = reply["turn"]["id"]
                parents = set()
                children = {}
                offset = 0
                with (case_dir / "events.jsonl").open("x", encoding="utf-8") as stream:

                    def emit(row):
                        stream.write(json.dumps(row) + "\n")
                        stream.flush()

                    emit({"type": "thread.started", "thread_id": thread})
                    while True:
                        changed = client.notification_changed
                        for notification in client.notifications[offset:]:
                            offset += 1
                            method, params = (
                                notification.get("method"),
                                notification.get("params") or {},
                            )
                            actor = _host_id(params.get("threadId"))
                            item = params.get("item") or {}
                            if method in ("item/started", "item/completed") and actor:
                                projected = project_item(item, server, args.graph_asset)
                                if not projected:
                                    continue
                                event = {
                                    "type": method.replace("/", "."),
                                    "item": projected,
                                    "host_actor": thread_digest(actor),
                                }
                                if actor == thread and params.get("turnId") == turn:
                                    emit(event)
                                    if (
                                        projected.get("tool") == "spawn_agent"
                                        and projected["status"] == "completed"
                                    ):
                                        parents.update(projected["receiver_thread_ids"])
                                elif actor != thread:
                                    children.setdefault(actor, []).append(event)
                            if (
                                method == "turn/completed"
                                and actor == thread
                                and params.get("turn", {}).get("id") == turn
                            ):
                                status = params["turn"].get("status")
                                if status != "completed":
                                    raise ValueError("Configured model turn did not complete")
                                emit({"type": "turn.completed"})
                                for child in parents:
                                    rows = children.get(child, [])
                                    if rows:
                                        (
                                            case_dir / f"child-{thread_digest(child)}.jsonl"
                                        ).write_text(
                                            "".join(json.dumps(row) + "\n" for row in rows),
                                            encoding="utf-8",
                                        )
                                return
                        if client.reader_error:
                            raise client.reader_error
                        await changed.wait()
        finally:
            with anyio.CancelScope(shield=True):
                await close_process(process)
            workers.cancel_scope.cancel()
