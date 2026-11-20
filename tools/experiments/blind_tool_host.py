"""Run a blind observation in an ephemeral session with the user's configured MCP servers."""

from __future__ import annotations

import hashlib
from copy import copy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID

import anyio

from tools.check_codex_interop import AppServer, InteropFailure, close_process

RECORDED_SERVER = "unityMCP_trial"
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
DOCTOR = "mcpforunity://doctor"
TRIAL_SOURCE = "mcpforunity://trial/source"


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def observed_permissions(settings):
    sandbox = settings.get("sandbox") or settings.get("sandboxPolicy")
    approval = settings.get("approvalPolicy")
    if not isinstance(sandbox, dict) or not isinstance(approval, str):
        return None
    return {
        "approval": approval,
        "sandbox": sandbox.get("type"),
        "digest": _digest(
            [
                sandbox,
                approval,
                settings.get("approvalsReviewer"),
                settings.get("activePermissionProfile"),
            ]
        ),
    }


def model_settings(settings):
    thread = settings.get("thread") or {}
    model = settings.get("model") or thread.get("model")
    effort = (
        settings.get("reasoningEffort") or settings.get("effort") or thread.get("reasoningEffort")
    )
    return {
        k: v
        for k, v in (("model", model), ("effort", effort))
        if isinstance(v, str) and IDENTIFIER.fullmatch(v)
    }


async def owned_model_settings(client, actor, known):
    if {"model", "effort"} <= known.keys() or not _host_id(actor):
        return known
    try:
        reply = await client.request("thread/read", {"threadId": actor, "includeTurns": False})
        if (reply.get("thread") or {}).get("id") == actor:
            return {**model_settings(reply), **known}
    except (InteropFailure, TimeoutError, ValueError, TypeError, AttributeError):
        pass
    return known


@lru_cache(maxsize=8)
def executable_digest(codex):
    path = shutil.which(codex)
    if not path:
        return None
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=8)
def supports_parent_lookup(codex):
    """Only query a binary that declares bounded direct-parent and subagent filters."""
    try:
        with TemporaryDirectory(prefix="unity-mcp-host-schema-") as folder:
            subprocess.run(
                [codex, "app-server", "generate-json-schema", "--experimental", "--out", folder],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=25,
            )
            schema = json.loads(
                (Path(folder) / "v2" / "ThreadListParams.json").read_text(encoding="utf-8")
            )
            fields = schema["properties"]
            return {
                "parentThreadId",
                "limit",
                "sourceKinds",
                "useStateDbOnly",
            } <= fields.keys() and (
                "subAgentThreadSpawn" in schema["definitions"]["ThreadSourceKind"]["enum"]
            )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


async def scoped_children(client, parent):
    if not _host_id(parent):
        return set(), "invalid_parent"
    try:
        reply = await client.request(
            "thread/list",
            {
                "parentThreadId": parent,
                "sourceKinds": ["subAgentThreadSpawn"],
                "limit": 4,
                "useStateDbOnly": True,
            },
        )
        rows = reply["data"]
        if reply.get("nextCursor") or not isinstance(rows, list) or len(rows) > 4:
            return set(), "incomplete"
        children = {direct_child(row, parent) for row in rows}
        if None in children:
            return set(), "unverified_parent"
        return children, "parent_linked" if children else "no_link"
    except (InteropFailure, TimeoutError, KeyError, AttributeError, TypeError, ValueError):
        return set(), "lookup_unavailable"


def launch_settings(args, case_dir):
    """CLI overrides never merge a stdio command into an installed HTTP transport."""
    server = args.unity_server
    command = [args.codex, "app-server"]
    if getattr(args, "disable_plugins", False):
        command.extend(["--disable", "plugins"])
    for competitor in getattr(args, "disable_competitor", None) or ():
        if competitor == "codex_apps":
            command.extend(["--disable", "apps"])
        else:
            command.extend(["-c", f"mcp_servers.{competitor}.enabled=false"])
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
    if args.delegation == "v2" and (
        getattr(args, "pair_gate", None) or getattr(args, "live_child", False)
    ):
        command.extend(["--enable", "code_mode"])
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
        "namespace": unity_server,
        "unity": {"tools": [], "resources": [], "templates": []},
        "competing_servers": {},
        "service_runtime_status": {},
        "competitor_digest": None,
        "source": source,
    }
    params = {"threadId": thread_id, "limit": 64}
    competitor_signatures = {}
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
                runtime = item.get("runtimeStatus")
                if runtime == "disabled":
                    continue
                result["service_runtime_status"][name] = (
                    runtime if runtime in ("connected", "notStarted") else "unknown"
                )
                descriptors_present = bool(
                    tools or item.get("resources") or item.get("resourceTemplates")
                )
                if item.get("toolsError") or not (
                    runtime == "connected" or (runtime == "notStarted" and descriptors_present)
                ):
                    healthy = False
                if name != unity_server:
                    resources = item.get("resources")
                    templates = item.get("resourceTemplates")
                    if not isinstance(resources, list) or not isinstance(templates, list):
                        healthy = False
                    if tools or resources or templates:
                        result["competing_servers"][name] = sorted(tools)
                        # Hash capabilities rather than persisting another service's URIs or schemas.
                        descriptors = {
                            "tools": tools,
                            "resources": sorted(
                                resources or [], key=lambda row: row.get("uri", "")
                            ),
                            "templates": sorted(
                                templates or [], key=lambda row: row.get("uriTemplate", "")
                            ),
                        }
                        competitor_signatures[name] = hashlib.sha256(
                            json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode()
                        ).hexdigest()
                    continue
                result["unity"]["tools"] = sorted(tools)
                result["tool_schema_digest"] = _digest(tools)
                if not isinstance(item.get("resources"), list) or not isinstance(
                    item.get("resourceTemplates"), list
                ):
                    healthy = False
                actions = {}
                for name_, tool in tools.items():
                    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
                    if name_ == "manage_ugui":
                        properties = schema.get("properties") or {}
                        strict = properties.get("strict_read") or {}
                        result["unity"]["strict_raycast_arguments"] = (
                            {"target", "screen_position", "allow_custom_filters"}
                            <= properties.keys()
                            and strict.get("type") == "boolean"
                            and strict.get("const", True) is True
                        )
                    action = schema.get("properties", {}).get("action") or {}
                    choices = action.get("enum", [action.get("const")])
                    if (
                        isinstance(choices, list)
                        and choices
                        and all(
                            isinstance(value, str) and IDENTIFIER.fullmatch(value)
                            for value in choices
                        )
                    ):
                        actions[name_] = sorted(choices)
                result["unity"]["actions"] = actions
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
                    result["competition_observed"] = bool(competitor_signatures)
                    result["competitor_digest"] = hashlib.sha256(
                        json.dumps(competitor_signatures, sort_keys=True).encode()
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


def direct_child(record, parent):
    """Only a documented host parent link establishes ownership."""
    from tools.experiments.blind_tool_trace import mapping

    source = mapping(mapping(mapping(record.get("source")).get("subAgent")).get("thread_spawn"))
    declared = [p for p in (record.get("parentThreadId"), source.get("parent_thread_id")) if p]
    child = _host_id(record.get("id"))
    return (
        child
        if child and child != parent and declared and all(p == parent for p in declared)
        else None
    )


async def collect_children(
    client, spawned, received, emit, case_dir, server, asset, project="", fixture=None
):
    """Read only direct trial children attested by a spawn receipt or thread-start notification."""
    from tools.experiments.blind_tool_trace import thread_digest

    for child in sorted(spawned):
        digest = thread_digest(child)
        emit({"type": "host.child_linked", "child_actor": digest})
        rows = received.get(child, [])
        # Hydrate only the known child's own turn. Hosts can send wait receipts
        # while omitting nested tool calls from the parent's notification stream.
        try:
            response = await client.request(
                "thread/turns/list",
                {
                    "threadId": child,
                    "itemsView": "full",
                    "limit": 8,
                    "sortDirection": "asc",
                },
            )
            turns = response["data"]
            if (
                not response.get("nextCursor")
                and turns
                and all(
                    turn.get("status") == "completed" and turn.get("itemsView") == "full"
                    for turn in turns
                )
            ):
                rows = [
                    {"type": "item.completed", "item": item, "host_actor": digest}
                    for turn in turns
                    for raw in turn.get("items", [])
                    if (item := project_item(raw, server, asset, project, fixture))
                ]
                rows.append({"type": "turn.completed", "host_actor": digest})
        except (InteropFailure, KeyError, ValueError, TimeoutError):
            pass
        if rows:
            (case_dir / f"child-{digest}.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )


def _tool_error(text):
    from tools.experiments.blind_tool_trace import APPROVAL

    return "approval blocked" if APPROVAL.search(str(text)) else "tool failed"


def project_item(item, server, asset, project="", fixture=None):
    """Convert only evidence needed by the scorer. Discard other tools' arguments and results."""
    from tools.experiments.blind_tool_shell import shell_receipt

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
            channel = decision(route, arguments, asset, fixture=fixture)
            # A captured host read is evidence on the installed route, separate from wire evidence.
            if channel in ("unity", "discovery"):
                clean = {
                    k: v for k, v in args.items() if k in {"action", "page_number", "page_size"}
                }
                if asset and args.get("path") == asset:
                    clean["path"] = asset
                if fixture and fixture.get("ui_asset") and args.get("path") == fixture["ui_asset"]:
                    clean["path"] = fixture["ui_asset"]
                if fixture and name in ("get_compile_job", "inspect_ugui_hit_order", "manage_ugui"):
                    clean.update(
                        {
                            k: v
                            for k, v in args.items()
                            if k in ("target", "screen_position", "strict_read", "compile_id")
                        }
                    )
                if name == "read_mcp_resource":
                    clean["uri"] = args.get("uri")
                projected["arguments"] = clean
                status, data = unpack({"result": item.get("result")}, route)
                if route == RAYCAST and data.get("status") == "failed":
                    status = "error"
                projected["observed_result"] = {
                    "status": status,
                    "facts": observation(route, data, fixture),
                    "has_decoded_data": bool(data),
                }
        if item.get("error"):
            projected["error"] = {"message": _tool_error(item["error"])}
        return projected
    if kind == "commandExecution":
        projected.update(type="command_execution", exit_code=item.get("exitCode"))
        command, output = item.get("command") or "", item.get("aggregatedOutput") or ""
        receipt = shell_receipt(
            command,
            output,
            item.get("exitCode"),
            (fixture or {}).get("ui_asset") or asset,
            project,
            item.get("cwd"),
        )
        projected["shell_receipt"] = receipt
        if (
            item.get("exitCode") == 0
            and asset
            and (
                receipt["purpose"] == "named_asset_read"
                or asset.casefold() in command.replace("\\", "/").casefold()
            )
        ):
            facts = receipt.get("facts", {})
            observed = len(facts.get("node_types", [])) >= 2
            projected["saved_read"] = {
                "grade": "observed" if observed else "review",
                "facts": facts if observed else {},
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


async def _project_verified(client, thread, server, instance, project):
    reply = await client.request(
        "mcpServer/resource/read",
        {
            "threadId": thread,
            "server": server,
            "uri": "mcpforunity://project/info",
            "_meta": {"unity_instance": instance},
        },
    )
    for block in reply.get("contents", []):
        if isinstance(block.get("text"), str):
            receipt = json.loads(block["text"])
            root = (receipt.get("data") or {}).get("projectRoot")
            if (
                receipt.get("success") is True
                and isinstance(root, str)
                and root
                and Path(root).resolve() == project.resolve()
            ):
                return True
    return False


def same_installation(normal, guarded):
    return (
        normal.get("status") == guarded.get("status") == "observed"
        and normal.get("project_verified") is True
        and guarded.get("project_verified") is True
        and normal.get("source", {}).get("server_commit")
        and normal["source"] == guarded["source"]
        and normal.get("unity") == guarded.get("unity")
        and normal.get("tool_schema_digest") == guarded.get("tool_schema_digest")
        and normal.get("permissions") == guarded.get("permissions")
    )


async def run_configured(args, case_dir, prompt, metadata):
    """Probe the installed configuration before handing the model a guarded equivalent surface."""
    expected = None
    if args.installed:
        metadata["runner_phase"] = "installed_preflight"
        expected = await _configured_session(args, case_dir, prompt, metadata)
        if (
            not expected
            or not expected.get("project_verified")
            or not expected["source"]["server_commit"]
        ):
            raise ValueError("Installed server identity unavailable")
        args = copy(args)
        args.installed = False
        args.endpoint = args.installed_endpoint
    metadata["runner_phase"] = "configured_preflight"
    await _configured_session(args, case_dir, prompt, metadata, expected)


async def _configured_session(args, case_dir, prompt, metadata, expected=None):
    """Use the app-server protocol to observe the exact ephemeral thread before its model turn."""
    from tools.experiments.blind_tool_trace import thread_digest, wire_checkpoint
    from tools.experiments.blind_tool_child import (
        child_permissions as delivered_permissions,
        verify_live_child,
    )

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
            gate = getattr(args, "pair_gate", None)
            with anyio.fail_after(args.timeout * (4 if gate else 1)):
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
                live_child = args.delegation == "v2" and (
                    bool(gate) or getattr(args, "live_child", False)
                )
                if live_child:
                    options["experimentalRawEvents"] = True
                if args.approval == "auto-review":
                    options["approvalsReviewer"] = "auto_review"
                if getattr(args, "study", None) or gate:
                    options["config"] = {"model_reasoning_effort": args.effort}
                    options["allowProviderModelFallback"] = False
                started = await client.request("thread/start", options)
                thread = _host_id(started["thread"]["id"])
                if not thread:
                    raise ValueError("No host thread ID")
                surface = await observe_host(
                    client,
                    thread,
                    server,
                    args.instance,
                    installed=args.installed or expected is not None,
                )
                if gate and surface["status"] != "observed":
                    for delay in (1, 3):
                        await anyio.sleep(delay)
                        surface = await observe_host(
                            client,
                            thread,
                            server,
                            args.instance,
                            installed=args.installed or expected is not None,
                        )
                        if surface["status"] == "observed":
                            break
                surface["permissions"] = observed_permissions(started)
                settings = model_settings(started)
                if getattr(args, "study", None) or gate:
                    settings = await owned_model_settings(client, thread, settings)
                surface.update(settings)
                if args.installed or expected is not None or getattr(args, "study", None) or gate:
                    surface["project_verified"] = await _project_verified(
                        client, thread, server, args.instance, args.project
                    )
                (case_dir / ("installed-host.json" if args.installed else "host.json")).write_text(
                    json.dumps(surface, indent=2) + "\n", encoding="utf-8"
                )
                if args.installed:
                    return surface
                if expected is not None and not same_installation(expected, surface):
                    raise ValueError(
                        "Guarded server does not match installed catalog, source and target"
                    )
                if expected is not None:
                    metadata["installed_competitors_match"] = (
                        expected["competitor_digest"] == surface["competitor_digest"]
                    )
                    metadata["installed_competitor_digest"] = expected["competitor_digest"]
                metadata["competitor_digest"] = surface["competitor_digest"]
                metadata["competition_observed"] = surface.get("competition_observed")
                metadata["observed_server_commit"] = surface["source"]["server_commit"]
                if getattr(args, "study", None) or gate:
                    from tools.experiments.blind_tool_selection import probe_state

                    if not surface.get("project_verified"):
                        raise ValueError("Study host could not verify the selected project")
                    metadata["host_binary_digest"] = executable_digest(args.codex)
                    metadata["editor_start_state"] = await anyio.to_thread.run_sync(
                        probe_state, args
                    )
                if gate:
                    metadata["runner_phase"] = "pair_preflight"
                    arm = metadata["arm"]
                    from tools.experiments.blind_tool_observations import fixture_availability

                    metadata["fixture_availability"] = fixture_availability(
                        getattr(args, "observation_fixture", {}), metadata["editor_start_state"]
                    )
                    if not await anyio.to_thread.run_sync(
                        gate.arrive, arm, thread_digest(thread), surface, metadata
                    ):
                        metadata["pair_reasons"] = list(gate.reasons)
                        raise ValueError("Prepared hosts did not pass paired controls")
                    current = await anyio.to_thread.run_sync(probe_state, args)
                    receipt = gate.start(arm, current)
                    if receipt is None:
                        metadata["pair_reasons"] = list(gate.reasons)
                        raise ValueError("Editor state changed before prompt delivery")
                    metadata["prepared_pair"] = receipt
                    receipt["prepared_actor"] = (
                        "delegating_parent" if metadata["role"] == "subagent" else "main"
                    )
                if metadata["observed_server_commit"]:
                    metadata["backend_identity"] = metadata["observed_server_commit"]
                metadata["runner_phase"] = "model_turn"
                metadata["wire_before_turn"] = wire_checkpoint(case_dir)
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
                child_permissions = {}
                child_models = {}
                verified_live = set()
                attempted_live = set()
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
                            if (
                                live_child
                                and actor
                                and actor != thread
                                and method in ("turn/started", "item/started")
                                and actor not in attempted_live
                                and len(attempted_live) < 8
                            ):
                                attempted_live.add(actor)
                                verified = await verify_live_child(
                                    client, thread, started["thread"].get("sessionId"), actor
                                )
                                if verified is not None:
                                    verified_live.add(actor)
                                    parents.add(actor)
                                    child_models.setdefault(actor, {}).update(verified)
                                    metadata["child_lookup"] = "live_parent_and_session"
                            if method == "rawResponseItem/completed" and actor in verified_live:
                                if permission := delivered_permissions(item):
                                    child_permissions.setdefault(actor, permission)
                            if method == "thread/settings/updated" and actor and actor != thread:
                                child_models.setdefault(actor, {}).update(
                                    model_settings(params.get("threadSettings") or {})
                                )
                                if permission := observed_permissions(
                                    params.get("threadSettings") or {}
                                ):
                                    child_permissions[actor] = permission
                            if method == "thread/started":
                                linked = direct_child(params.get("thread", {}), thread)
                                if linked and (not live_child or linked in verified_live):
                                    parents.add(linked)
                                    child_models.setdefault(linked, {}).update(
                                        model_settings(params.get("thread") or {})
                                    )
                                    if not children.get(linked):
                                        child_surface = await observe_host(
                                            client,
                                            linked,
                                            server,
                                            args.instance,
                                            installed=expected is not None,
                                        )
                                        (
                                            case_dir / f"host-child-{thread_digest(linked)}.json"
                                        ).write_text(
                                            json.dumps(child_surface, indent=2) + "\n",
                                            encoding="utf-8",
                                        )
                            if method == "turn/completed" and actor and actor != thread:
                                if params.get("turn", {}).get("status") == "completed":
                                    children.setdefault(actor, []).append(
                                        {
                                            "type": "turn.completed",
                                            "host_actor": thread_digest(actor),
                                        }
                                    )
                            if method in ("item/started", "item/completed") and actor:
                                projected = project_item(
                                    item,
                                    server,
                                    args.graph_asset,
                                    args.project,
                                    getattr(args, "observation_fixture", None),
                                )
                                if not projected:
                                    continue
                                event = {
                                    "type": method.replace("/", "."),
                                    "item": projected,
                                    "host_actor": thread_digest(actor),
                                }
                                if actor == thread and params.get("turnId") == turn:
                                    if live_child and projected.get("tool") == "spawn_agent":
                                        projected["receiver_thread_ids"] = []
                                    emit(event)
                                    if (
                                        projected.get("tool") == "spawn_agent"
                                        and projected["status"] == "completed"
                                    ):
                                        if not live_child:
                                            parents.update(projected["receiver_thread_ids"])
                                elif actor != thread and (not live_child or actor in verified_live):
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
                                metadata["wire_at_completion"] = wire_checkpoint(case_dir)
                                if metadata["role"] == "subagent" or parents:
                                    if (
                                        not parents
                                        and not live_child
                                        and supports_parent_lookup(args.codex)
                                    ):
                                        parents, metadata["child_lookup"] = await scoped_children(
                                            client, thread
                                        )
                                    if live_child:
                                        parents.intersection_update(verified_live)
                                    for linked in parents:
                                        path = case_dir / f"host-child-{thread_digest(linked)}.json"
                                        if path.is_file():
                                            child_surface = json.loads(
                                                path.read_text(encoding="utf-8")
                                            )
                                        else:
                                            child_surface = {}
                                        if child_surface.get("status") != "observed":
                                            child_surface = await observe_host(
                                                client,
                                                linked,
                                                server,
                                                args.instance,
                                                installed=expected is not None,
                                            )
                                        if getattr(args, "study", None) or gate or live_child:
                                            child_surface[
                                                "project_verified"
                                            ] = await _project_verified(
                                                client, linked, server, args.instance, args.project
                                            )
                                        child_surface["permissions"] = child_permissions.get(linked)
                                        settings = child_models.get(linked, {})
                                        if getattr(args, "study", None) or gate or live_child:
                                            settings = await owned_model_settings(
                                                client, linked, settings
                                            )
                                        child_surface.update(settings)
                                        child_surface["actor_controls_observed"] = "child_session"
                                        path.write_text(
                                            json.dumps(child_surface, indent=2) + "\n",
                                            encoding="utf-8",
                                        )
                                    await collect_children(
                                        client,
                                        parents,
                                        children,
                                        emit,
                                        case_dir,
                                        server,
                                        args.graph_asset,
                                        args.project,
                                        getattr(args, "observation_fixture", None),
                                    )
                                return
                        if client.reader_error:
                            raise client.reader_error
                        await changed.wait()
        finally:
            with anyio.CancelScope(shield=True):
                await close_process(process)
            workers.cancel_scope.cancel()
