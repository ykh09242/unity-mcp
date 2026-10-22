"""Optional tracking at AI write boundaries; inactive graphs cost no I/O."""

from __future__ import annotations

import asyncio
import sqlite3
from contextvars import ContextVar

from core.config import config
from services.credential_lease import CredentialLeaseError, current_credential_lease
from services.operation_permissions import OperationPermissionError
from services.reference_graph.reconcile import drained_task, drained_thread
from services.reference_graph.service import ReferenceGraphService, local_user
from services.reference_graph.store import RevisionConflict


_uncertain_dispatch = ContextVar("reference_graph_uncertain_dispatch", default=None)


def mark_uncertain_dispatch():
    """Read-back recovery observes state but cannot establish write causation."""
    value = _uncertain_dispatch.get()
    if value is not None:
        value[0] = True


def target_paths(command, params):
    options = params.get("options")
    if params.get("preview") or isinstance(options, dict) and options.get("preview"):
        return None
    action = params.get("action")
    if command == "manage_script" and action in {
        "create",
        "update",
        "edit",
        "delete",
        "apply_text_edits",
        "apply_edits",
        "apply_prepared_edit",
    }:
        path, name = params.get("path"), params.get("name")
        if isinstance(path, str) and isinstance(name, str):
            return [path.rstrip("/") + "/" + name.removesuffix(".cs") + ".cs"]
    if command == "manage_asset" and action in {
        "create",
        "create_folder",
        "modify",
        "move",
        "rename",
        "delete",
        "duplicate",
        "set_labels",
        "add_labels",
        "remove_labels",
    }:
        paths = [params.get("path"), params.get("destination")]
        paths.extend(
            target.get("path")
            for target in params.get("labelTargets", [])
            if isinstance(target, dict)
        )
        return list(dict.fromkeys(path for path in paths if isinstance(path, str) and path)) or None
    return None


def active_service(ctx, instance):
    service = getattr(getattr(ctx, "fastmcp", None), "_reference_graph_service", None)
    if not isinstance(service, ReferenceGraphService) or service.closed or not service.entries:
        return None
    lease = current_credential_lease() if config.http_remote_hosted else None
    if config.http_remote_hosted and lease is None:
        return None
    user = "remote:" + lease.user_id if lease else local_user()
    if any(
        entry.instance == instance
        and entry.workspace.store.scope.user_id == user
        and entry.watching
        and not entry.retired
        for entry in service.entries.values()
    ):
        return service
    return None


def outcome(response):
    response = response.model_dump() if hasattr(response, "model_dump") else response
    if not isinstance(response, dict):
        return "unknown"
    if response.get("success") is True:
        return "accepted"
    message = str(response.get("error") or response.get("message") or "").lower()
    data = response.get("data")
    if (
        any(
            word in message
            for word in (
                "timeout",
                "timed out",
                "disconnect",
                "connection closed",
                "aborted",
                "unconfirmed",
            )
        )
        or isinstance(data, dict)
        and (data.get("execution_status") == "unknown" or data.get("reason") == "outcome_unknown")
    ):
        return "unknown"
    return "failed" if response.get("success") is False else "unknown"


def attach_diagnostic(response, diagnostic):
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    if isinstance(payload, dict):
        payload = dict(payload)
        data = payload.get("data")
        if data is None or isinstance(data, dict):
            payload["data"] = {**(data or {}), "reference_graph": diagnostic}
        else:
            payload["reference_graph"] = diagnostic
        return payload
    return response


async def prepare_duplicate(ctx, instance, params):
    """Choose the native unique destination before capturing its absent state."""
    service = active_service(ctx, instance)
    if service is None or params.get("action") != "duplicate" or params.get("destination"):
        return params, None
    try:
        entry, _ = await service.resolve(ctx, "begin_mutation")
        if entry.instance != instance or not await drained_thread(
            entry.workspace.admitted, params["path"]
        ):
            raise RevisionConflict("Duplicate preparation scope changed")
        observed = await entry.collect("copy_destination", path=params["path"])
        await entry.check()
        destination = observed.get("path")
        if not isinstance(destination, str) or not await drained_thread(
            entry.workspace.admitted, destination
        ):
            raise RevisionConflict("Duplicate destination is outside the collection policy")
        return {**params, "destination": destination}, None
    except CredentialLeaseError:
        raise
    except (
        OperationPermissionError,
        ValueError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        KeyError,
        TypeError,
    ) as error:
        return params, type(error).__name__


async def tracked_mutation(
    ctx, instance, command, params, invoke, *, paths=None, preparation_error=None
):
    service = active_service(ctx, instance)
    if service is not None and preparation_error:
        return attach_diagnostic(
            await invoke(), {"state": "unavailable", "reason": preparation_error}
        )
    targets = paths if paths is not None else target_paths(command, params)
    if service is None or not targets:
        return await invoke()
    receipt = None
    scope = None
    diagnostic = None
    try:
        result = await service.execute(ctx, "begin_mutation", {"paths": targets[:1000]})
        scope, receipt = result["data"]["scope"], result["data"]["receipt"]
        for offset in range(1000, len(targets), 1000):
            await service.execute(
                ctx,
                "add_mutation_targets",
                {"id": receipt["id"], "paths": targets[offset : offset + 1000]},
            )
    except CredentialLeaseError:
        raise
    except (OperationPermissionError, ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        # Derived diagnostics must not turn an otherwise-authorized write into
        # a retry that could duplicate side effects. Do not echo native paths.
        diagnostic = {"state": "unavailable", "reason": type(error).__name__}
        if receipt is not None:
            diagnostic.update(id=receipt["id"], scope=scope, state="preparation_failed")
            receipt = None

    async def finish(result):
        entry, _ = await service.resolve(ctx, "finish_mutation")
        if entry.workspace.store.scope.key != scope:
            raise RevisionConflict("Mutation receipt belongs to the previous Editor scope")
        await entry.check()
        receipts = await drained_thread(entry.workspace.mutations)
        finished = await drained_thread(receipts.finish, receipt["id"], result, "tool_response")
        await entry.check()
        service.start(entry, wake=True)
        return {
            "id": finished["id"],
            "scope": scope,
            "state": finished["state"],
            "outcome": finished["outcome"],
        }

    uncertain = [False]
    token = _uncertain_dispatch.set(uncertain)
    try:
        response = await invoke()
    except BaseException:
        if receipt is not None:
            try:
                await drained_task(asyncio.create_task(finish("unknown")))
            except (asyncio.CancelledError, Exception):
                # The durable open receipt remains recoverable after restart or
                # cancellation. Never replay the native mutation for graph work.
                pass
        raise
    finally:
        _uncertain_dispatch.reset(token)
    if receipt is not None:
        try:
            diagnostic = await finish("unknown" if uncertain[0] else outcome(response))
        except CredentialLeaseError:
            raise
        except (
            OperationPermissionError,
            ValueError,
            OSError,
            RuntimeError,
            sqlite3.Error,
        ) as error:
            diagnostic = {
                "id": receipt["id"],
                "scope": scope,
                "state": "unconfirmed",
                "reason": type(error).__name__,
            }
    if diagnostic is not None:
        return attach_diagnostic(response, diagnostic)
    return response
