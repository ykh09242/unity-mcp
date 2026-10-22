"""Async receipt coordination using existing scope authorization and workers."""

from __future__ import annotations

import asyncio
import math
import time

from services.reference_graph.mutations import MutationReceipts
from services.reference_graph.reconcile import drained_thread
from services.reference_graph.store import RevisionConflict


def checkpoint(editor):
    return {"epoch": editor["epoch"], "revision": editor["revision"]}


async def probe(entry, paths):
    MutationReceipts.paths(paths)
    for path in paths:
        if not await drained_thread(entry.workspace.admitted, path):
            raise ValueError("Mutation target is excluded by this scope's collection policy")
    result = await entry.collect("probe", paths=paths)
    rows = result.get("targets")
    if not isinstance(rows, list) or [row.get("path") for row in rows] != paths:
        raise RevisionConflict("Native probe did not return the requested target page")
    return rows, checkpoint(result)


async def step(entry, receipts, identity):
    receipt = await drained_thread(receipts.read, identity)
    if receipt["state"] != "pending":
        return receipt, "finish_required" if receipt["state"] == "open" else None
    editor = await entry.collect("status")
    if any(
        editor.get(key)
        for key in ("compiling", "importing", "compilation_failed", "addressables_event_error")
    ):
        return receipt, "editor_not_ready"
    workspace = entry.workspace
    progress = await drained_thread(workspace.progress)
    status = await drained_thread(workspace.store.status)
    providers = await drained_thread(workspace.provider_status)
    if (
        not progress.get("initialized")
        or progress.get("phase") != "idle"
        or progress.get("epoch") != editor["epoch"]
        or progress.get("after") != editor["revision"]
        or progress.get("editor", {}).get("compilation_context")
        != editor.get("compilation_context")
        or not status["queue_idle"]
        or providers["addressables"]["errors"]
    ):
        return receipt, "awaiting_collection"
    position = receipt["checkpoint"]
    replacement = editor["epoch"] != receipt["baseline"]["epoch"]
    if not replacement:
        if position["after"] < editor["revision"]:
            page = await entry.collect(
                "changes", epoch=position["epoch"], after=position["after"], page_size=1000
            )
            if page.get("requires_census"):
                return receipt, "journal_changed"
            if page["next_after"] <= position["after"]:
                raise RevisionConflict("Import journal did not advance")
            await drained_thread(
                receipts.events, identity, page["changes"], page["epoch"], page["next_after"]
            )
            position = {"epoch": page["epoch"], "after": page["next_after"]}
            await entry.check()
            if position["after"] < editor["revision"]:
                return await drained_thread(receipts.read, identity), "reading_imports"
    after = await drained_thread(
        receipts.validation, identity, checkpoint(editor), status["revision"]
    )
    targets = await drained_thread(receipts.targets, identity, after)
    if targets:
        rows, observed = await probe(entry, [row["path"] for row in targets])
        if observed != checkpoint(editor):
            return receipt, "journal_changed"
        reason = await drained_thread(
            receipts.check_targets, identity, rows, replacement_epoch=replacement
        )
        if reason:
            if reason == "awaiting_collection":
                await drained_thread(receipts.request_collection, identity, rows)
                entry.wake.set()
            return receipt, reason
        await drained_thread(
            receipts.validation,
            identity,
            checkpoint(editor),
            status["revision"],
            targets[-1]["path"],
        )
        if len(targets) == 128:
            return await drained_thread(receipts.read, identity), "verifying_targets"
    final = await entry.collect("status")
    if (
        checkpoint(final) != checkpoint(editor)
        or any(
            final.get(key)
            for key in ("compiling", "importing", "compilation_failed", "addressables_event_error")
        )
        or final.get("compilation_context") != editor.get("compilation_context")
    ):
        return receipt, "journal_changed"
    await entry.check()
    await drained_thread(receipts.complete, identity, checkpoint(editor), status["revision"])
    return await drained_thread(receipts.read, identity), None


async def execute(service, entry, editor, action, options):
    receipts = await drained_thread(entry.workspace.mutations)
    identity = options.get("id")
    if action in {"begin_mutation", "add_mutation_targets"}:
        if ("paths" in options) == ("guids" in options):
            raise ValueError("Supply exactly one target page: paths or inventoried GUIDs")
        paths = (
            await drained_thread(receipts.resolve_guids, options["guids"])
            if "guids" in options
            else options["paths"]
        )
        rows, observed = await probe(entry, paths)
        if "guids" in options and [row.get("guid") for row in rows] != options["guids"]:
            raise RevisionConflict("GUID paths changed; synchronize and retry target preparation")
        if observed != checkpoint(editor):
            raise RevisionConflict("Import state changed while preparing the mutation")
        if action == "begin_mutation":
            data = await drained_thread(receipts.begin, rows, observed)
        else:
            data = await drained_thread(receipts.add, identity, rows, observed)
        return {"receipt": data}
    lock_key = "mutation:" + entry.workspace.store.scope.key + ":" + identity
    if action != "await_mutation":
        async with service._coordinate(lock_key):
            return await immediate(receipts, identity, action, options)
    wait = options.get("wait_seconds", 0)
    if (
        isinstance(wait, bool)
        or not isinstance(wait, (int, float))
        or not math.isfinite(wait)
        or wait < 0
    ):
        raise ValueError(
            "wait_seconds must be a nonnegative finite request wait; it never expires the receipt"
        )
    service.start(entry, wake=True)
    deadline = time.monotonic() + wait
    while True:
        await entry.check()
        async with service._coordinate(lock_key):
            data, reason = await step(entry, receipts, identity)
        if reason is None or data["state"] == "open" or time.monotonic() >= deadline:
            return {"receipt": data, "pending_reason": reason}
        # Advancing pages yield cooperatively; waiting for an import backs off.
        await asyncio.sleep(
            0
            if reason in {"reading_imports", "verifying_targets"}
            else min(0.25, max(0, deadline - time.monotonic()))
        )


async def immediate(receipts, identity, action, options):
    if action == "finish_mutation":
        return {
            "receipt": await drained_thread(
                receipts.finish, identity, options.get("outcome", "unknown")
            )
        }
    if action == "release_mutation":
        return {"released": await drained_thread(receipts.release, identity)}
    if action == "mutation_diff":
        return await drained_thread(
            receipts.diff,
            identity,
            **{key: value for key, value in options.items() if key != "id"},
        )
