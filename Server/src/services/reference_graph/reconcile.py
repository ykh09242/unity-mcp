"""Resumable reconciliation; the transport and authenticated lifetime are injected.

Only the Editor collector reads project files. Blocking analysis/SQLite work is
drained before releasing the cross-process lease, including on cancellation.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import contextvars
from functools import partial
from collections.abc import Awaitable, Callable
from dataclasses import asdict
import hashlib
from itertools import chain, islice
import re
import time
import threading
import uuid

from services.reference_graph import analysis_cache
from services.reference_graph.analysis import (
    ANALYSIS_VERSION,
    Analysis,
    AnalysisIndex,
    Catalog,
    CatalogLookups,
    Facts,
    SerializedScanError,
    parse_csharp,
    serialized_references,
)
from services.reference_graph.compilation import ConditionalScanError
from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
    boolean,
    change_page,
    continuation,
    decoded_size,
    integer,
    ordered_guids,
    rows,
    text,
)
from services.reference_graph.external import ExternalSource, document_coverage, document_events
from services.reference_graph.external_changes import Batch, ExternalChanges, ScanPlan
from services.reference_graph.ignore import rule_directory
from services.reference_graph.store import Edge, Observation, RevisionConflict, _hash, _json
from services.reference_graph.workspace import GraphWorkspace


async def drained_task(task: asyncio.Future, *, _cancel_event: threading.Event | None = None):
    """Repeated caller cancellation cannot abandon an owned cleanup or thread."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if _cancel_event is not None:
                _cancel_event.set()
        except Exception:
            if not cancelled:
                raise
            # Cooperative work can fail as it observes the stop request. The
            # owner's cancellation/deadline remains authoritative after draining.
    if cancelled:
        # Retrieve any exception before propagating the owning cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def drained_thread(function, *args, _cancel_event: threading.Event | None = None, **kwargs):
    """Cancellation must not abandon a thread that can still publish or read."""
    # Runner teardown cancels every Task, including asyncio.to_thread wrappers.
    # Retain the executor Future itself: shielded caller cancellation cannot mark
    # physical work complete before the underlying thread actually returns.
    context = contextvars.copy_context()
    future = asyncio.get_running_loop().run_in_executor(
        None, partial(context.run, function, *args, **kwargs)
    )
    return await drained_task(future, _cancel_event=_cancel_event)


class SourceEncodingError(ValueError):
    """Optional source bytes could not be decoded."""


class RequestedAssetChanged(Exception):
    """A speculative ordering hint lost its proof. Its durable dirty job remains."""


class Reconciler:
    MAX_REQUESTED_ASSETS = 32

    def __init__(
        self,
        workspace: GraphWorkspace,
        collect: Callable[..., Awaitable[dict]],
        check: Callable[[], Awaitable[None]],
        check_now: Callable[[], None] = lambda: None,
        authorize_source: Callable[[dict], None] = lambda _: None,
        *,
        limits: CollectionLimits | None = None,
    ):
        self.workspace = workspace
        self.store = workspace.store
        self._collector = collect
        self.limits = limits or CollectionLimits.from_environment()
        self._budget = CollectionBudget(self.limits)
        self.check = check
        self.check_now = check_now
        self.authorize_source = authorize_source
        self.stopped = threading.Event()
        self.worker = uuid.uuid4().hex
        self.progress = {}
        self._gate = asyncio.Lock()
        self._catalog_cache: tuple[int, Catalog] | None = None
        self._analysis_cache = None
        self._analysis_used = 0.0
        self.external_changes = ExternalChanges(workspace)
        self.partial_proof = None
        self._requested_guids: dict[str, None] = {}

    def request_asset(self, node: str) -> None:
        """Keep only bounded opaque hints on this scope's event loop until collection."""
        if len(self._requested_guids) < self.MAX_REQUESTED_ASSETS and re.fullmatch(
            r"asset:[0-9a-f]{32}", node
        ):
            self._requested_guids[node[6:]] = None

    @staticmethod
    def supports_asset_guard(editor: dict) -> bool:
        version = editor.get("asset_guard_version")
        return type(version) is int and version == 1

    async def collect(self, action: str, **params) -> dict:
        self._budget.request()
        pending = asyncio.ensure_future(self._collector(action, **params))
        try:
            page = await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Cancel the native request once, then retain capacity until its cleanup
            # finishes. Repeated owner cancellation cannot interrupt that cleanup.
            pending.cancel()
            await drained_task(asyncio.gather(pending, return_exceptions=True))
            raise
        if not isinstance(page, dict):
            raise RevisionConflict("Collector response must be an object")
        self._budget.response(page)
        if action == "status":
            text(page.get("epoch"), "epoch")
            integer(page.get("revision"), "revision")
        return page

    def clear_caches(self) -> None:
        """Call only while this reconciler is idle; durable facts remain intact."""
        self._catalog_cache = None
        self._analysis_cache = None
        self.store.clear_summaries()

    @staticmethod
    def matches_ignore_proof(status: dict, known: dict) -> bool:
        """Check a combined Editor observation against the stored snapshot in this read."""
        try:
            integer(status.get("revision"), "revision")
            return (
                type(status.get("ignore_current_validation")) is int
                and status["ignore_current_validation"] == 1
                and status.get("ignore_current_verified") is True
                and text(status.get("epoch"), "epoch") == text(known.get("epoch"), "epoch")
                and integer(status.get("ignore_revision"), "ignore_revision")
                == integer(known.get("ignore_revision"), "ignore_revision")
                and status.get("ignore_snapshot_digest")
                == hashlib.sha256(
                    text(known.get("snapshot"), "snapshot").encode("utf-8")
                ).hexdigest()
            )
        except ValueError:
            return False

    async def validate_ignore(self, status: dict | None = None) -> bool:
        """Validate a warm native snapshot without rereading assets or external documents."""

        def validation_inputs():
            with self.store.read_snapshot():
                policy = self.workspace.policy()
                enabled = policy["respect_gitignore"]
                return (
                    enabled,
                    self.workspace.ignore_status() if enabled else {},
                    policy.get("version", 0),
                )

        enabled, known, policy_version = await drained_thread(validation_inputs)
        if not enabled:
            return True
        return await self.validate_ignore_snapshot(status, known, policy_version)

    async def validate_ignore_snapshot(
        self, status: dict | None, known: dict, policy_version: int
    ) -> bool:
        """Validate the exact enabled snapshot captured by the caller."""
        if not known.get("snapshot"):
            return False
        try:
            if status is not None and "ignore_current_verified" in status:
                if self.matches_ignore_proof(status, known):
                    return True
                await drained_thread(self.workspace.invalidate_ignore, known, policy_version)
                return False
            text(known.get("snapshot"), "snapshot")
            known_epoch = text(known.get("epoch"), "epoch")
            known_revision = integer(known.get("ignore_revision"), "ignore_revision")
            if status is not None:
                text(status.get("epoch"), "epoch")
                integer(status.get("revision"), "revision")
                integer(status.get("ignore_revision"), "ignore_revision")
            page = await self._collector(
                "ignore_rules", snapshot=known["snapshot"], offset=0, page_size=1, validate=True
            )
            if (
                page.get("ignore_state") == "available"
                and text(page.get("snapshot"), "snapshot") == known["snapshot"]
                and text(page.get("epoch"), "epoch") == known_epoch
                and integer(page.get("ignore_revision"), "ignore_revision") == known_revision
            ):
                return True
        except (OSError, ValueError, RuntimeError):
            # Changed/expired proofs force normal acquisition; never an allow-all policy.
            pass
        await drained_thread(self.workspace.invalidate_ignore, known, policy_version)
        return False

    async def _phase(self, phase: str, **values) -> None:
        self.progress.update(phase=phase, **values)
        await drained_thread(self.workspace.set_progress, self.worker, self.progress)

    async def _heartbeat(self, parent: asyncio.Task) -> None:
        try:
            while True:
                await asyncio.sleep(15)
                await self.check()
                if not await drained_thread(self.workspace.renew, self.worker):
                    raise RevisionConflict("Reconciliation ownership changed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stopped.set()
            parent.cancel()

    async def reconcile(
        self, *, force_external: bool = False, wait: bool = True, external_mode: str = "full"
    ) -> bool | None:
        # Serialize use of the durable worker identity. Foreground requests
        # must not wait while holding an Editor operation needed by the owner.
        if wait:
            await self._gate.acquire()
        else:
            try:
                # A foreground Editor operation cannot wait for a background
                # owner that may itself be queued for the same Editor.
                async with asyncio.timeout(0):
                    await self._gate.acquire()
            except TimeoutError:
                return None
        try:
            self.partial_proof = None
            self._budget = CollectionBudget(self.limits)
            return await self._reconcile(force_external=force_external, external_mode=external_mode)
        finally:
            self.partial_proof = None
            self._gate.release()

    async def _reconcile(self, *, force_external: bool, external_mode: str) -> bool:
        await self.check()
        self.stopped.clear()
        heartbeat = None
        try:
            if not await drained_thread(self.workspace.acquire, self.worker):
                return False
            heartbeat = asyncio.create_task(self._heartbeat(asyncio.current_task()))
            self.progress = await drained_thread(self.workspace.progress)
            deadline = asyncio.timeout(self._budget.remaining())
            try:
                async with deadline:
                    return await self._run_reconcile(
                        force_external=force_external, external_mode=external_mode
                    )
            except TimeoutError as error:
                if not deadline.expired():
                    raise
                raise CollectionBudgetExceeded("max_seconds", self.limits.max_seconds) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            try:
                # Cancellation may have drained a committed SQLite write before
                # its result reached this task. Preserve the durable checkpoint.
                self.progress = await drained_thread(
                    self.workspace.record_error,
                    self.worker,
                    type(error).__name__ + ": " + str(error),
                    defer_folder_proof=isinstance(error, CollectionBudgetExceeded)
                    and getattr(error, "name", None) in {"max_pages", "max_bytes", "max_seconds"},
                )
            except RevisionConflict:
                pass
            raise
        finally:
            # Shutdown and exact credential retirement may both cancel this
            # owner. Neither can interrupt heartbeat/thread/DB lease cleanup.
            await drained_task(asyncio.create_task(self._release(heartbeat)))

    async def _run_reconcile(self, *, force_external: bool, external_mode: str) -> bool:
        if self.workspace.restart_policy_pending:
            await drained_thread(self.workspace.require_restart_policy)
        status = await self.collect("status")
        await self._phase("checking", editor=status, error=None)
        if any(status.get(key) for key in ("compiling", "importing", "compilation_failed")):
            await self._phase("waiting_for_editor")
            return False
        ignore_snapshot = await self._ignore_rules(status)
        policy_fence = (await drained_thread(self.workspace.delivery_fence))[3:]
        self.progress["editor"] = status
        await drained_thread(self.workspace.ensure_analysis_version, self.worker, ANALYSIS_VERSION)
        compilation_context = status.get("compilation_context")
        if not self.progress.get("initialized") or self.progress.get("census"):
            census = self.progress.get("census") or {}
            await self._census(
                status, force=census.get("force", not self.progress.get("initialized"))
            )
        await self._changes()
        await self._compilation(compilation_context)
        # A complete catalog and journal permit narrowly scoped, current Unity
        # dependency pages while the remaining assets are being collected.
        # A folder publication that exhausted its RPC, byte or time budget
        # reserves retries for durable collection and the mandatory final checks.
        if not (await drained_thread(self.workspace.delivery_fence))[2] and not (
            await drained_thread(self.workspace.state, "folder_scan", {})
        ).get("defer_partial_proof"):
            if ignore_snapshot is not None:
                validated = False
                try:
                    verified = await self.collect(
                        "ignore_rules",
                        snapshot=ignore_snapshot,
                        offset=0,
                        page_size=1,
                        validate=True,
                    )
                    if (
                        verified.get("ignore_state") != "available"
                        or verified.get("snapshot") != ignore_snapshot
                        or (verified.get("epoch"), verified.get("ignore_revision"))
                        != (self.progress["ignore_epoch"], self.progress["ignore_revision"])
                    ):
                        raise RevisionConflict(
                            "Ignore rules changed before partial graph publication"
                        )
                    validated = True
                finally:
                    if not validated:
                        await drained_thread(self.workspace.invalidate_ignore)
            partial_status = await self.collect("status")
            partial_fence = await drained_thread(self.workspace.delivery_fence)
            if (
                partial_fence[3:] == policy_fence
                and not partial_fence[2]
                and partial_status.get("epoch") == self.progress.get("epoch")
                and partial_status.get("revision") == self.progress.get("after")
                and partial_status.get("compilation_context") == compilation_context
                and not any(
                    partial_status.get(key)
                    for key in (
                        "compiling",
                        "importing",
                        "compilation_failed",
                        "addressables_event_error",
                    )
                )
                and (
                    ignore_snapshot is None
                    or (
                        partial_status.get("ignore_state") == "available"
                        and (partial_status.get("epoch"), partial_status.get("ignore_revision"))
                        == (self.progress["ignore_epoch"], self.progress["ignore_revision"])
                    )
                )
            ):
                self.partial_proof = (partial_fence[1:], partial_status)
        await self._assets(ignore_snapshot)
        await self._analysis()
        await self._external(force=force_external, external_mode=external_mode)
        if ignore_snapshot is not None:
            validated = False
            try:
                verified = await self.collect(
                    "ignore_rules", snapshot=ignore_snapshot, offset=0, page_size=1, validate=True
                )
                if (
                    verified.get("ignore_state") != "available"
                    or verified.get("snapshot") != ignore_snapshot
                    or (verified.get("epoch"), verified.get("ignore_revision"))
                    != (self.progress["ignore_epoch"], self.progress["ignore_revision"])
                ):
                    raise RevisionConflict("Ignore rules changed before reconciliation publication")
                validated = True
            finally:
                if not validated:
                    await drained_thread(self.workspace.invalidate_ignore)
        status = await self.collect("status")
        if ignore_snapshot is not None and (
            status.get("ignore_state") != "available"
            or (status.get("epoch"), status.get("ignore_revision"))
            != (self.progress["ignore_epoch"], self.progress["ignore_revision"])
        ):
            await drained_thread(self.workspace.block_admission)
            raise RevisionConflict("Ignore rules changed before graph publication")
        await drained_thread(self.workspace.finish_admission, self.worker)
        providers = await drained_thread(self.workspace.provider_status)
        caught_up = (
            status["epoch"] == self.progress.get("epoch")
            and status.get("compilation_context") == compilation_context
            and status["revision"] == self.progress.get("after")
            and not self.progress.get("external_errors")
            and not providers["addressables"]["errors"]
            and (await drained_thread(self.store.status))["queue_idle"]
            and not any(
                status.get(key)
                for key in (
                    "compiling",
                    "importing",
                    "compilation_failed",
                    "addressables_event_error",
                )
            )
        )
        self.progress.pop("external_pass", None)
        await self._phase(
            "idle" if caught_up else "changes_pending", editor=status, completed_at=time.time()
        )
        return caught_up

    async def _ignore_rules(self, status: dict) -> str | None:
        """Read only collector-admitted rule files and verify the complete byte stream."""
        if not (await drained_thread(self.workspace.policy))["respect_gitignore"]:
            return None
        if status.get("ignore_state") not in {"available", "unavailable"}:
            await drained_thread(self.workspace.block_admission)
            raise RevisionConflict(
                "Project ignore rules are unavailable; graph admission is incomplete"
            )
        known = await drained_thread(self.workspace.ignore_status)
        if status.get("ignore_state") == "unavailable":
            await drained_thread(self.workspace.block_admission)
            known = {}
        identity = (
            text(status.get("epoch"), "epoch"),
            integer(status.get("ignore_revision"), "ignore_revision"),
        )
        if known.get("snapshot") and identity == (known.get("epoch"), known.get("ignore_revision")):
            self.progress.update(ignore_epoch=identity[0], ignore_revision=identity[1])
            # Validation must still recheck missing/present candidates at the end.
            return known.get("snapshot")
        await drained_thread(self.workspace.block_admission)
        files, offset, snapshot, last = [], 0, None, b""
        while True:
            page = await self.collect(
                "ignore_rules",
                offset=offset,
                snapshot=snapshot,
                page_size=256,
                validate=offset == 0,
            )
            if page.get("ignore_state") != "available":
                raise RevisionConflict("Project ignore rule snapshot is unavailable")
            token = text(page.get("snapshot"), "snapshot")
            observed = (
                text(page.get("epoch"), "epoch"),
                integer(page.get("ignore_revision"), "ignore_revision"),
            )
            if snapshot is not None and (token != snapshot or observed != identity):
                raise RevisionConflict("Ignore rule snapshot changed between pages")
            identity, snapshot = observed, token
            entries = rows(page, "files", 256)
            following = continuation(page, offset, len(entries))
            self._budget.admit_items(len(entries))
            for entry in entries:
                path = text(entry.get("path"), "ignore path")
                rule_directory(path)
                ordinal = path.encode("utf-16-be")
                if ordinal <= last:
                    raise RevisionConflict("Ignore rule files must have unique ordered paths")
                last = ordinal
                fingerprint = text(entry.get("fingerprint"), "fingerprint")
                expected_size = integer(entry.get("size_bytes"), "size_bytes")
                content, position = bytearray(), 0
                while True:
                    chunk = await self.collect(
                        "ignore_source",
                        snapshot=snapshot,
                        path=path,
                        offset=position,
                        fingerprint=fingerprint,
                    )
                    if (
                        chunk.get("snapshot") != snapshot
                        or chunk.get("path") != path
                        or chunk.get("fingerprint") != fingerprint
                        or (chunk.get("epoch"), chunk.get("ignore_revision")) != identity
                    ):
                        raise RevisionConflict("Ignore rule bytes changed during collection")
                    size = decoded_size(chunk.get("bytes_base64"))
                    next_position = continuation(chunk, position, size)
                    self._budget.source(size, len(content))
                    content.extend(base64.b64decode(chunk["bytes_base64"], validate=True))
                    if next_position is None:
                        break
                    position = next_position
                if (
                    len(content) != expected_size
                    or hashlib.sha256(content).hexdigest() != fingerprint
                ):
                    raise RevisionConflict("Ignore rule content fingerprint or size changed")
                files.append((path, content.decode("utf-8-sig")))
            if following is None:
                break
            offset = following
        changed = await drained_thread(
            self.workspace.set_ignore_rules,
            self.worker,
            files,
            {"epoch": identity[0], "ignore_revision": identity[1], "snapshot": snapshot},
        )
        if changed:
            self.clear_caches()
        self.progress = await drained_thread(self.workspace.progress)
        self.progress.update(ignore_epoch=identity[0], ignore_revision=identity[1])
        return snapshot

    async def _release(self, heartbeat: asyncio.Task | None) -> None:
        if heartbeat is not None:
            if not heartbeat.cancelling():
                heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await drained_thread(self.workspace.release, self.worker)

    async def _census(self, status: dict, *, force: bool) -> None:
        census = self.progress.get("census")
        if census and not {"epoch", "revision", "last_guid"} <= census.keys():
            # Older checkpoints lack the identity fence; replay a complete census.
            census = None
        census = census or {
            "seen": uuid.uuid4().hex,
            "offset": 0,
            "force": force,
            "through": status["revision"],
        }
        await self._phase("census", census=census)
        while True:
            await self.check()
            try:
                page = await self.collect(
                    "inventory",
                    offset=census["offset"],
                    snapshot=census.get("snapshot"),
                    page_size=256,
                )
                assets = rows(page, "assets", 256)
                following = continuation(page, census["offset"], len(assets), scanned=256)
                snapshot = text(page.get("snapshot"), "snapshot")
                epoch = text(page.get("epoch"), "epoch")
                revision = integer(page.get("revision"), "revision")
                if census.get("snapshot") is not None and (
                    snapshot != census["snapshot"]
                    or epoch != census.get("epoch", epoch)
                    or revision != census.get("revision", revision)
                ):
                    raise RevisionConflict("Inventory snapshot changed during collection")
                if any(not isinstance(row, dict) for row in assets):
                    raise RevisionConflict("Collector inventory assets must be objects")
                last_guid = ordered_guids(
                    (row.get("guid") for row in assets), census.get("last_guid", "")
                )
                self._budget.admit_items(len(assets))
            except RevisionConflict:
                # Only an invalid snapshot or inconsistent page invalidates the cursor.
                await self._phase("census", census=None, initialized=False)
                raise
            progress = {
                **self.progress,
                "epoch": epoch,
                "after": revision if census["force"] else min(self.progress["after"], revision),
                "catalog_through": census["through"],
            }
            if following is not None:
                census = {
                    **census,
                    "snapshot": snapshot,
                    "offset": following,
                    "epoch": epoch,
                    "revision": revision,
                    "last_guid": last_guid,
                }
                progress["census"] = census
            # On the last page retain its starting cursor until the sweep commits.
            await drained_thread(
                self.workspace.record_page,
                self.worker,
                assets,
                progress,
                census["seen"],
                force=census["force"],
            )
            self.progress = progress
            if following is None:
                finished = {**progress, "census": None, "initialized": True, "phase": "changes"}
                await drained_thread(
                    self.workspace.finish_census, self.worker, census["seen"], finished
                )
                self.progress = finished
                return

    async def _changes(self) -> None:
        context = await drained_thread(self.workspace.state, "change_page", {})
        while True:
            await self.check()
            after = self.progress["after"]
            page_size = context["through"] - after if context else 256
            page = await self.collect(
                "changes", epoch=self.progress["epoch"], after=after, page_size=page_size
            )
            if boolean(page.get("requires_census"), "requires_census"):
                await self._census(await self.collect("status"), force=True)
                context = {}
                continue
            changes, following, more = change_page(
                page, after, maximum=256, epoch=self.progress["epoch"]
            )
            self._budget.admit_items(len(changes))
            through = context.get("through", following)
            if page["revision"] < through:
                raise RevisionConflict("Collector journal moved behind its checkpoint")
            if following > through:
                changes = changes[: through - after]
                more = True
            admitted = await drained_thread(self.workspace.admission)
            if any(
                change.get("kind") != "addressables_catalog" and not admitted(change["path"])
                for change in changes
            ):
                await drained_thread(self.workspace.block_admission)
            catalog = context.get("catalog", False)
            moved_roots = set(context.get("moved_roots", []))
            version = self.progress.get("editor", {}).get("asset_batch_version")
            per_rpc = 32 if type(version) is int and version == 1 else 1
            # Keep imports batched. A deletion owns its cursor and destination
            # evidence until its journal position commits, even on worker restart.
            offset = 0
            while offset < len(changes):
                change = changes[offset]
                if change.get("deleted"):
                    destination, needs_census = await self._remove_changed_path(
                        change["path"], change["revision"], through
                    )
                    if destination:
                        moved_roots.add(destination)
                    catalog |= needs_census
                    offset += 1
                else:
                    end = next(
                        (i for i in range(offset + 1, len(changes)) if changes[i].get("deleted")),
                        len(changes),
                    )
                    # Leave room for legacy single-asset RPCs on constrained attempts.
                    capacity = max(1, self._budget.limits.max_pages - self._budget.pages) * per_rpc
                    end = min(end, offset + capacity)
                    catalog |= await self._import_changes(
                        changes[offset:end], admitted, moved_roots
                    )
                    offset = end
                after = changes[offset - 1]["revision"]
                context = (
                    {"through": through, "catalog": catalog, "moved_roots": sorted(moved_roots)}
                    if after < through
                    else {}
                )
                progress = {**self.progress, "phase": "changes", "after": after}
                if catalog and not context:
                    # Keep the required census durable before requesting its first page.
                    progress["census"] = {"requested": True}
                await drained_thread(self.workspace.finish_changes, self.worker, progress, context)
                self.progress = progress
            if catalog and not context:
                await self._census(await self.collect("status"), force=False)
                continue
            if not more:
                return

    async def _import_changes(self, changes: list[dict], admitted, moved_roots: set[str]) -> bool:
        described = await self._describe_changes(changes)
        descriptions = iter(described) if described is not None else None
        catalog = False
        pending_assets = []

        async def flush_assets():
            if pending_assets:
                await drained_thread(
                    self.workspace.record_page, self.worker, pending_assets, self.progress, "event"
                )
                pending_assets.clear()

        for change in changes:
            if change.get("kind") == "addressables_catalog":
                catalog |= change["revision"] > self.progress.get("catalog_through", -1)
                continue
            path = change["path"]
            moved = path in moved_roots
            moved_roots.discard(path)
            asset = (
                next(descriptions)
                if descriptions is not None
                else await self.collect("asset", path=path)
            )
            if asset.get("present"):
                self._budget.admit_items(1)
                pending_assets.append(asset)
            else:
                await flush_assets()
                if not moved:
                    if admitted(path):
                        await drained_thread(
                            self.workspace.remove_path, self.worker, path, exact=True
                        )
                    if change["revision"] > self.progress.get("catalog_through", -1):
                        await drained_thread(self.workspace.block_admission)
                        catalog = True
        await flush_assets()
        return catalog

    async def _describe_changes(self, changes: list[dict]) -> list[dict] | None:
        version = self.progress.get("editor", {}).get("asset_batch_version")
        if type(version) is not int or version != 1:
            return None
        paths = [
            change["path"]
            for change in changes
            if change.get("kind") != "addressables_catalog" and not change["deleted"]
        ]
        descriptions = []
        for start in range(0, len(paths), 32):
            batch = paths[start : start + 32]
            page = await self.collect("assets", paths=batch)
            result = rows(page, "assets", len(batch))
            if len(result) != len(batch):
                raise RevisionConflict("Collector asset batch length changed")
            for path, row in zip(batch, result):
                if (
                    not isinstance(row, dict)
                    or row.get("path") != path
                    or not isinstance(row.get("asset"), dict)
                ):
                    raise RevisionConflict("Collector asset batch does not match requested paths")
                asset = row["asset"]
                if not isinstance(asset.get("present"), bool) or (
                    asset["present"] and asset.get("path") != path
                ):
                    raise RevisionConflict("Collector asset batch contains an invalid description")
                descriptions.append(asset)
        return descriptions

    async def _remove_changed_path(
        self, path: str, revision: int, through: int
    ) -> tuple[str | None, bool]:
        scan = await drained_thread(self.workspace.state, "folder_scan", {})
        identity = [self.progress["epoch"], revision]
        if scan.get("event") != identity:
            scan = {"event": identity, "after": ["", ""], "destination": None, "ambiguous": False}
        while not scan.get("census"):
            candidates = await drained_thread(self.workspace.descendant_assets, path, scan["after"])
            if not candidates:
                break
            if scan["after"] == ["", ""]:
                await drained_thread(self.workspace.begin_folder_scan, self.worker, scan, through)
            for candidate in candidates:
                guid, old_path = candidate["guid"], candidate["path"]
                asset = await self.collect("asset", guid=guid)
                scan["after"] = [old_path, guid]
                if not boolean(asset.get("present"), "present"):
                    await drained_thread(
                        self.workspace.remove_missing_guids, self.worker, [guid], scan
                    )
                    continue
                self._budget.admit_items(1)
                if asset.get("guid") != guid:
                    raise RevisionConflict("Collector folder descendant identity changed")
                current = text(asset.get("path"), "path")
                if current != old_path and (current == path or current.startswith(path + "/")):
                    # Relocation within the scan could displace an unvisited GUID.
                    # Keep admission blocked and let inventory enumerate both identities.
                    scan["census"] = True
                    await drained_thread(
                        self.workspace.begin_folder_scan, self.worker, scan, through
                    )
                    break
                suffix = old_path[len(path) :]
                if current != old_path and current.endswith(suffix):
                    root = current[: -len(suffix)]
                    if scan["destination"] is None:
                        scan["destination"] = root
                    elif scan["destination"] != root:
                        scan["ambiguous"] = True
                else:
                    scan["ambiguous"] = True
                await drained_thread(
                    self.workspace.record_page,
                    self.worker,
                    [asset],
                    self.progress,
                    "event",
                    folder_scan=scan,
                )
        await drained_thread(self.workspace.remove_path, self.worker, path, exact=True)
        return (None if scan["ambiguous"] else scan["destination"]), scan.get("census", False)

    async def _assets(self, ignore_snapshot: str | None) -> None:
        await self._phase("collecting")
        after = ""
        donor = (
            await drained_thread(self.workspace.state, "restart_from")
            if not self.progress.get("completed_at")
            else None
        )
        ignore_digest = (
            hashlib.sha256(ignore_snapshot.encode()).hexdigest() if ignore_snapshot else None
        )
        promote = self.supports_asset_guard(self.progress["editor"])
        while batch := await drained_thread(self.workspace.dirty_assets, after=after):
            reused = await self._reuse_batch(donor, batch) if donor else {}
            batch_guids = {asset["guid"] for asset in batch}
            for asset in batch:
                after = asset["guid"]
                if after not in batch_guids:
                    continue
                # At most one promotion per regular job. Do not advance the
                # ordinary GUID cursor for an out-of-order asset.
                if promote and self._requested_guids:
                    try:
                        promoted = await self._promote_requested(after, ignore_digest)
                    except RequestedAssetChanged:
                        # Keep the regular job. Refresh the catalog/rules on the
                        # next pass before attempting any further promotions.
                        promote = False
                    else:
                        if promoted:
                            batch_guids.discard(promoted)
                await self._collect_dirty_asset(asset, reused)
        self._requested_guids.clear()

    async def _promote_requested(self, regular: str, ignore_digest: str | None) -> str | None:
        candidates = tuple(self._requested_guids)
        requested = await drained_thread(
            self.workspace.requested_asset,
            self.worker,
            candidates,
            self.partial_proof[0][0] if self.partial_proof else None,
        )
        for guid in candidates:
            self._requested_guids.pop(guid, None)
            if requested and guid == requested["guid"]:
                break
        if not requested or requested["guid"] == regular:
            return None
        guard = {
            "path": requested["path"],
            "epoch": self.progress["epoch"],
            "revision": self.progress["after"],
            "ignore_digest": ignore_digest,
        }
        await self._collect_dirty_asset(requested, {}, guard)
        return requested["guid"]

    async def _collect_dirty_asset(
        self, asset: dict, reused: dict, guard: dict | None = None
    ) -> None:
        guid = asset["guid"]
        await self.check()
        try:
            if guid not in reused:
                await self._asset(asset, guard)
            elif version := reused[guid]:
                await self._source(asset)
                await drained_thread(self.workspace.clean_asset, self.worker, guid, version=version)
        except RevisionConflict:
            raise
        except (OSError, ValueError, RecursionError) as error:
            await drained_thread(self.workspace.fail_asset, self.worker, asset, error)

    async def _reuse_batch(self, donor: str, batch: list[dict]) -> dict[str, dict | None]:
        from services.reference_graph.restart import reuse_assets

        reused = {}
        for offset in range(0, len(batch), 32):
            assets = batch[offset : offset + 32]
            try:
                page = await self.collect(
                    "probe", paths=[a["path"] for a in assets], static_proof=True
                )
            except OSError:
                # An unprobeable path must still use normal per-asset error handling.
                continue
            if (page.get("epoch"), page.get("revision")) != (
                self.progress["epoch"],
                self.progress["after"],
            ):
                continue
            targets = rows(page, "targets", len(assets))
            self._budget.admit_items(len(targets))
            proofs = []
            for asset, row in zip(assets, targets):
                if (
                    isinstance(row, dict)
                    and row.get("path") == asset["path"]
                    and row.get("guid") == asset["guid"]
                    and row.get("present") is True
                    and row.get("folder") is False
                    and isinstance(row.get("stamp"), str)
                    and row["stamp"]
                    and isinstance(row.get("dependency"), str)
                    and row["dependency"].strip("0")
                    and type(row.get("dependency_count")) is int
                    and row["dependency_count"] >= 0
                ):
                    proofs.append(row)
            if proofs:
                await self.check()
                reused.update(
                    await drained_thread(
                        reuse_assets, self.workspace, self.worker, donor, proofs, self._budget
                    )
                )
        return reused

    async def _compilation(self, context: dict | None) -> None:
        if context == await drained_thread(self.workspace.state, "compilation_context"):
            return
        checked = bool(context and context.get("state") == "available")
        if checked:
            await self._phase("checking_compilation")
            after = ""
            while candidates := await drained_thread(self.workspace.compilation_candidates, after):
                await self.check()
                paths = [item["path"] for item in candidates]
                page = await self.collect("compilation", paths=paths, revision=context["revision"])
                bindings = rows(page, "bindings", len(paths))
                self._budget.admit_items(len(bindings))
                if (
                    page.get("revision") != context["revision"]
                    or [row.get("path") for row in bindings] != paths
                    or any(
                        not isinstance(row.get("signature"), str)
                        and not isinstance(row.get("error"), str)
                        for row in bindings
                    )
                ):
                    raise RevisionConflict("Compilation metadata changed during verification")
                dirty = [
                    item["guid"]
                    for item, binding in zip(candidates, bindings)
                    if binding.get("error") or item["signature"] != binding.get("signature")
                ]
                if dirty:
                    await drained_thread(self.workspace.dirty_compilation, self.worker, dirty)
                after = candidates[-1]["guid"]
        await drained_thread(
            self.workspace.ensure_compilation_context, self.worker, context, checked=checked
        )

    async def _collect_asset_page(self, action: str, guard: dict | None, **params) -> dict:
        if guard is None:
            return await self.collect(action, **params)
        page = await self.collect(action, **params, asset_guard=guard)
        if page.get("asset_guard_current") is not True:
            raise RequestedAssetChanged
        return page

    async def _asset(self, asset: dict, guard: dict | None = None) -> None:
        guid, owner = asset["guid"], "asset:" + asset["guid"]
        targets, offset, snapshot, fingerprint = [], 0, None, None
        while True:
            page = await self._collect_asset_page(
                "dependencies", guard, guid=guid, offset=offset, snapshot=snapshot, page_size=1000
            )
            if not boolean(page.get("present"), "present"):
                await drained_thread(self.workspace.remove_path, self.worker, asset["path"])
                return

            if fingerprint is not None and fingerprint != page["fingerprint"]:
                raise RevisionConflict("Dependencies changed while reading")
            next_snapshot = text(page.get("snapshot"), "snapshot")
            if snapshot is not None and next_snapshot != snapshot:
                raise RevisionConflict("Dependency snapshot changed during collection")
            page_targets = rows(page, "targets", 1000)
            following = continuation(page, offset, len(page_targets))
            ordered_guids(page_targets, targets[-1] if targets else "")
            self._budget.dependencies(len(targets) + len(page_targets))
            self._budget.admit_items(len(page_targets))
            fingerprint, snapshot = text(page.get("fingerprint"), "fingerprint"), next_snapshot
            targets.extend(page_targets)
            offset = following
            if offset is None:
                generation = await drained_thread(
                    self.workspace.observe,
                    self.worker,
                    Observation(
                        owner, "unity", fingerprint, path=asset["path"], kind=asset["kind"]
                    ),
                )
                result = Analysis(
                    [Edge("asset:" + target, "unity_dependency") for target in targets],
                    coverage="direct_asset_database_dependencies",
                )
                await drained_thread(
                    self.workspace.publish, self.worker, owner, "unity", generation, result
                )
                await self._source(asset, guard)
                await drained_thread(
                    self.workspace.clean_asset,
                    self.worker,
                    guid,
                    version={"stamp": page.get("file_stamp"), "dependency": fingerprint},
                )
                return

    async def _source(self, asset: dict, guard: dict | None = None) -> None:
        if asset["source_format"] == "none":
            await drained_thread(
                self.workspace.drop_document, self.worker, "asset:" + asset["guid"]
            )
            return
        content, offset, fingerprint, context = bytearray(), 0, None, None
        while True:
            page = await self._collect_asset_page(
                "source", guard, guid=asset["guid"], offset=offset, fingerprint=fingerprint
            )
            source_format = page.get("source_format")
            if source_format not in {asset["source_format"], "binary"}:
                raise RevisionConflict("Collector source format changed during collection")
            if source_format == "binary":
                if offset:
                    raise RevisionConflict("Source became binary during collection")
                payload = {
                    "coverage": page["coverage"],
                    "edges": [],
                    "unresolved": [{"reason": page["coverage"]}],
                }
                fingerprint = _hash(payload)
                format_ = "summary"
                break
            if fingerprint is not None and fingerprint != page["fingerprint"]:
                raise RevisionConflict("Source changed while reading")
            fingerprint = text(page.get("fingerprint"), "fingerprint")
            if offset == 0:
                context = page.get("compilation_context")
                expected = self.progress.get("editor", {}).get("compilation_context")
                if (
                    expected
                    and (context is None or context.get("revision") != expected.get("revision"))
                    and asset["source_format"] == "csharp"
                ):
                    raise RevisionConflict("Compilation context changed while collecting")
            size = decoded_size(page.get("bytes_base64"))
            following = continuation(page, offset, size)
            self._budget.source(size, len(content))
            content.extend(base64.b64decode(page["bytes_base64"], validate=True))
            offset = following
            if offset is None:
                try:
                    format_, payload, fingerprint = await drained_thread(
                        self._parse,
                        asset,
                        content,
                        context,
                        check=self._check_thread,
                        _cancel_event=self.stopped,
                    )
                except (SerializedScanError, ConditionalScanError, SourceEncodingError) as error:
                    reason = (
                        "serialized_scan_incomplete"
                        if isinstance(error, SerializedScanError)
                        else "conditional_scan_incomplete"
                        if isinstance(error, ConditionalScanError)
                        else "source_encoding_incomplete"
                    )
                    format_ = "summary"
                    payload = {
                        "path": asset["path"],
                        "kind": asset["kind"],
                        "coverage": reason,
                        "edges": [],
                        "unresolved": [{"reason": reason}],
                    }
                    fingerprint = _hash([hashlib.sha256(content).hexdigest(), context, reason])
                break
        await drained_thread(
            self.workspace.save_document,
            self.worker,
            "asset:" + asset["guid"],
            format_,
            fingerprint,
            payload,
        )

    @staticmethod
    def _parse(
        asset: dict,
        content: bytes,
        context: dict | None = None,
        *,
        check: Callable[[], None] = lambda: None,
    ) -> tuple:
        check()
        try:
            text = content.decode(
                "utf-16" if content[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
            )
        except UnicodeDecodeError as error:
            raise SourceEncodingError("Optional source encoding is unsupported") from error
        fingerprint = hashlib.sha256(content).hexdigest()
        if asset["source_format"] == "csharp":
            facts = parse_csharp(
                "asset:" + asset["guid"], asset["path"], text, context=context, check=check
            )
            check()
            return (
                "csharp",
                asdict(facts),
                _hash([fingerprint, facts.build, (context or {}).get("defines")]),
            )
        result = serialized_references(text, Catalog([]), check=check)
        edges = []
        for edge in result.edges:
            check()
            edges.append(asdict(edge))
        check()
        return (
            "summary",
            {
                "edges": edges,
                "coverage": result.coverage,
                "unresolved": result.unresolved,
            },
            fingerprint,
        )

    async def _analysis(self) -> None:
        if time.monotonic() - self._analysis_used > 300:
            self._analysis_cache = None
        rules = await drained_thread(self.store.rules_state)
        rules_changed = rules["revision"] != await drained_thread(
            self.workspace.state, "analyzed_rules_revision", -1
        )
        if not rules_changed and not await drained_thread(
            self.workspace.state, "analysis_dirty", False
        ):
            return
        await self._phase("analyzing")

        try:
            candidate, affected = await drained_thread(
                analysis_cache.refresh,
                self.workspace,
                self._analysis_cache,
                rules,
                self._check_thread,
                _cancel_event=self.stopped,
            )
            await drained_thread(
                self.workspace.queue_analysis,
                self.worker,
                affected,
                candidate.rules_revision,
                candidate.catalog_revision,
                candidate.document_generation,
                all_documents=rules_changed,
                _cancel_event=self.stopped,
            )
        except BaseException:
            # The old binding index may already be mutated. Until every affected
            # owner is durably queued only a fresh index can recover its work.
            self._analysis_cache = None
            raise
        self._analysis_cache = candidate
        after = ""
        while owners := await drained_thread(self.workspace.pending_analysis, after):
            await self.check()
            after = owners[-1]
            selected = [owner for owner in owners if owner not in candidate.blocked]
            if not selected:
                continue
            documents = await drained_thread(self.workspace.documents, selected)
            await drained_thread(
                self._analyze_batch,
                documents,
                candidate.index,
                candidate.rules_revision,
                candidate.catalog_revision,
                document_generation=candidate.document_generation,
                _cancel_event=self.stopped,
            )
        await drained_thread(
            self.workspace.finish_analysis,
            self.worker,
            candidate.rules_revision,
            candidate.catalog_revision,
            document_generation=candidate.document_generation,
            blocked=candidate.blocked,
        )
        self._analysis_used = time.monotonic()

    @staticmethod
    def _build_index(documents: list[dict], assets: list[dict], rules: dict) -> AnalysisIndex:
        return AnalysisIndex(
            [Facts.from_dict(row["data"]) for row in documents if row["format"] == "csharp"],
            Catalog(assets),
            [rule["config"] for rule in rules["rules"] if rule["config"].get("enabled", True)],
        )

    def _analyze_batch(
        self,
        documents: list[dict],
        index: AnalysisIndex,
        rules_revision: int,
        catalog_revision: int | None = None,
        *,
        document_generation: int | None = None,
    ) -> None:
        observations = [
            Observation(
                row["owner"],
                "code",
                row["fingerprint"],
                path=row["data"].get("path", ""),
                kind=row["data"].get("kind", "script" if row["format"] == "csharp" else "asset"),
                force=True,
                rules_revision=rules_revision,
                provenance={
                    "analysis_fingerprint": row["fingerprint"],
                    "build": {
                        key: value
                        for key, value in row["data"].get("build", {}).items()
                        if key != "references"
                    },
                },
            )
            for row in documents
        ]
        generations = self.workspace.observe_many(
            self.worker, observations, document_generation=document_generation
        )
        publications = []
        for row, generation in zip(documents, generations):
            self._check_thread()
            lookups = CatalogLookups()
            if row["format"] == "csharp":
                events = index.events(row["owner"], lookups)
                preview = list(islice(events, 257))
                if len(preview) > 256:
                    self.workspace.publish_events(
                        self.worker,
                        row["owner"],
                        generation,
                        chain(preview, events),
                        lambda owner=row["owner"]: index.transitive_dependencies(owner),
                        catalog_lookups=lookups,
                        catalog_revision=catalog_revision,
                        document_generation=document_generation,
                        check=self._check_thread,
                    )
                    continue
                result = Analysis(
                    [event for event in preview if isinstance(event, Edge)],
                    [event for event in preview if isinstance(event, dict)],
                    dependencies=index.transitive_dependencies(row["owner"]),
                    catalog_lookups=lookups,
                )
            else:
                result = Analysis(
                    [Edge(**edge) for edge in row["data"]["edges"]],
                    row["data"]["unresolved"],
                    coverage=row["data"]["coverage"],
                    catalog_lookups=lookups,
                )
                # A serialized GUID is lexical, but an excluded target is
                # suppressed at publication and can later become admitted.
                for edge in result.edges:
                    lookups.add(
                        lookups.key("guid", edge.target[6:])
                        if edge.target.startswith("asset:")
                        else lookups.ANY
                    )
            publications.append((row["owner"], "code", generation, result))
        self._check_thread()
        if not all(
            self.workspace.publish_many(
                self.worker,
                publications,
                catalog_revision=catalog_revision,
                document_generation=document_generation,
            )
        ):
            raise RevisionConflict("Analysis inputs changed before publication")

    async def _external(self, *, force: bool, external_mode: str = "full") -> None:
        self.progress["external_errors"] = {}
        sources = [
            source
            for source in await drained_thread(self.store.sources)
            if source["provider"].startswith("external:") and source["enabled"]
        ]
        active = {source["provider"] for source in sources} if external_mode == "tracked" else set()
        for provider in self.external_changes.registrations.keys() - active:
            await drained_thread(self.external_changes.close, provider)
        if not sources:
            self._catalog_cache = None
            return
        scan_pass = self.progress.get("external_pass")
        if not scan_pass or (force and not scan_pass["force"]):
            scan_pass = {"id": uuid.uuid4().hex, "force": force}
        await self._phase("external", external_errors={}, external_pass=scan_pass)
        self._catalog_cache = await drained_thread(
            self.workspace.refresh_catalog, self._catalog_cache
        )
        catalog_revision = self._catalog_cache[0]
        catalog = self._catalog_cache[1]
        rules = (await drained_thread(self.store.rules_state))["revision"]
        previous = await drained_thread(self.workspace.provider_status)
        for source in sources:
            await self.check()
            scan = {
                "generation": uuid.uuid4().hex,
                "state": "scanning",
                "started_at": time.time(),
                "last_verified_at": previous.get(source["provider"], {}).get("last_verified_at"),
                "catalog_revision": catalog_revision,
                "rules_revision": rules,
            }
            try:
                await drained_thread(self.workspace.external_scan, self.worker, source, scan)
                failures, repository, full = await drained_thread(
                    self._external_source,
                    source,
                    catalog,
                    rules,
                    catalog_revision,
                    scan_pass,
                    external_mode == "tracked",
                    previous.get(source["provider"], {}).get("state") == "verified_at_scan",
                    _cancel_event=self.stopped,
                )
                scan.update(
                    state="failed" if failures else "verified_at_scan",
                    failed_files=failures,
                    completed_at=time.time(),
                    repository=repository,
                )
                if not failures and full:
                    scan["last_verified_at"] = scan["completed_at"]
                await drained_thread(self.workspace.external_scan, self.worker, source, scan)
                if failures:
                    self.progress["external_errors"][source["provider"]] = {
                        "failed_files": failures
                    }
                    await self._phase("external")
            except RevisionConflict:
                raise
            except CollectionBudgetExceeded as error:
                scan.update(
                    state="failed",
                    error=type(error).__name__ + ": " + str(error),
                    completed_at=time.time(),
                )
                await drained_thread(
                    self.workspace.external_scan,
                    self.worker,
                    source,
                    scan,
                    incomplete=str(error),
                )
                raise
            except (OSError, ValueError, csv.Error, RecursionError) as error:
                scan.update(
                    state="failed",
                    error=type(error).__name__ + ": " + str(error),
                    completed_at=time.time(),
                )
                await drained_thread(self.workspace.external_scan, self.worker, source, scan)
                self.progress["external_errors"][source["provider"]] = (
                    type(error).__name__ + ": " + str(error)
                )
                await self._phase("external")

    def _external_source(
        self,
        source: dict,
        catalog: Catalog,
        rules: int,
        catalog_revision: int,
        scan_pass: dict,
        tracked: bool,
        previously_verified: bool,
    ) -> tuple[int, dict, bool]:
        try:
            with self.workspace.source_guard(
                source["provider"], source["version"]
            ) as require_source:
                return self._scan_external_source(
                    source,
                    catalog,
                    rules,
                    catalog_revision,
                    scan_pass,
                    require_source,
                    tracked,
                    previously_verified,
                )
        except BaseException:
            self.external_changes.close(source["provider"])
            raise

    def _scan_external_source(
        self,
        source: dict,
        catalog: Catalog,
        rules: int,
        catalog_revision: int,
        scan_pass: dict,
        require_source: Callable[[], None],
        tracked: bool,
        previously_verified: bool,
    ) -> tuple[int, dict, bool]:
        self._check_source(source, require_source)
        policy = self.workspace.policy()
        make_adapter = partial(
            ExternalSource,
            source["config"],
            respect_gitignore=policy["respect_gitignore"],
            exclude=policy["exclude"],
            budget=self._budget,
        )
        adapter = make_adapter()
        repository = adapter.repository_revision()
        provider = source["provider"]
        pass_prefix = scan_pass["id"] + ":"
        # Each enumeration needs its own marker to retire files removed since
        # the previous attempt, including files already verified in this pass.
        seen = pass_prefix + uuid.uuid4().hex
        analysis_key = _hash(
            (ANALYSIS_VERSION, source["version"], policy["version"], rules, catalog_revision)
        )
        check = partial(self._check_source, source, require_source)
        if tracked:
            signature = _hash(
                (
                    analysis_key,
                    str(adapter.root),
                    adapter.directory_identity(adapter.root),
                    repository,
                )
            )
            plan = self.external_changes.plan(
                provider,
                signature,
                adapter,
                require_full=not previously_verified or scan_pass["force"],
                check=check,
            )
        else:
            plan = ScanPlan(batch=Batch.capture(self.workspace, provider))
        if plan.full:
            # A targeted spelling/identity check may have observed a directory
            # while it was being replaced. A recovery sweep reads fresh rule files.
            if tracked:
                check()
                adapter = make_adapter()
            if plan.batch and plan.batch.paths:
                adapter.guard_event_reads(plan.scope.directories if plan.scope else {}, check)
            paths = (
                adapter.files(check=check, on_directory=plan.scope.visit)
                if plan.scope
                else adapter.files(check=check)
            )
            previous = self.workspace.external_inputs(provider, admission=True)
        else:
            paths = plan.files
            previous = plan.previous
        hinted = adapter.hint_matcher(plan.batch.paths) if plan.batch and plan.batch.paths else None
        rehash_all = plan.batch is not None and plan.batch.rehash_all
        failures = 0
        pending = []
        resumed = []
        provenance_updates = []

        def visit_external_item() -> None:
            self._budget.admit_items(1)

        def verify_document(path: str, stamp: str) -> None:
            current = adapter.stamp(path)
            if _json(current) != stamp:
                raise RevisionConflict("External input changed during analysis")
            adapter.confirm_event_entry(path, current[:2])
            self._check_source(source, require_source)

        for path in paths:
            self._check_source(source, require_source)
            old = previous.pop(path, None)
            owner = provider + ":" + _hash(path)
            fingerprint = (old or {}).get("fingerprint") or "unavailable"
            stamp = ""
            recorded = False
            try:
                current_stamp = adapter.stamp(path)
                stamp = _json(current_stamp)
                needs_analysis = (
                    old is None
                    or (scan_pass["force"] and not (old["seen"] or "").startswith(pass_prefix))
                    or (old["state"] != "ready" and old["checkpoint"] is None)
                    or old["stamp"] != stamp
                    or old["analysis_key"] != analysis_key
                )
                document = (
                    adapter.read(path)
                    if needs_analysis or (hinted and hinted(path, current_stamp))
                    else None
                )
                if (
                    document is not None
                    and rehash_all
                    and not needs_analysis
                    and document.fingerprint == fingerprint
                ):
                    # The lost notification requires content proof. Reuse the verified
                    # analysis when its inputs and freshly read bytes still match.
                    verify_document(path, stamp)
                    document = None
                if document is not None:
                    fingerprint = document.fingerprint
                    generation = self.workspace.observe_external(
                        self.worker,
                        Observation(
                            owner,
                            provider,
                            fingerprint,
                            path=path,
                            kind="external",
                            force=True,
                            source_version=source["version"],
                            rules_revision=rules,
                            provenance=adapter.provenance(path, fingerprint, repository),
                        ),
                        stamp=stamp,
                        analysis_key=analysis_key,
                        seen=seen,
                    )
                    recorded = True
                    events = document_events(
                        path,
                        document.text,
                        catalog,
                        adapter.mappings,
                        check=self._check_thread,
                        visit=visit_external_item,
                    )

                    self.workspace.publish_events(
                        self.worker,
                        owner,
                        generation,
                        events,
                        provider=provider,
                        coverage=document_coverage(adapter.mappings),
                        catalog_revision=catalog_revision,
                        check=self._check_thread,
                        before_publish=partial(verify_document, path, stamp),
                    )
            except RevisionConflict:
                raise
            except (OSError, ValueError, csv.Error, RecursionError) as error:
                self._check_source(source, require_source)
                adapter.confirm_event_entry(path, None)
                generation = self.workspace.observe_external(
                    self.worker,
                    Observation(
                        owner,
                        provider,
                        fingerprint,
                        path=path,
                        kind="external",
                        force=True,
                        source_version=source["version"],
                        rules_revision=rules,
                    ),
                    stamp=stamp,
                    analysis_key=analysis_key,
                    seen=seen,
                )
                recorded = True
                self.workspace.fail_external(self.worker, owner, provider, generation, error)
                failures += 1
            if not recorded:
                pending.append((provider, owner, path, fingerprint, stamp, analysis_key, seen))
                if old["checkpoint"] is not None:
                    resumed.append((provider, owner, old["checkpoint"]))
                provenance = adapter.provenance(path, fingerprint, repository)
                if old.get("provenance") != _json(provenance):
                    provenance_updates.append((owner, fingerprint, provenance))
            if len(pending) == 256:
                self._check_source(source, require_source)
                self.workspace.record_external_many(self.worker, pending, resume=resumed)
                pending.clear()
                resumed.clear()
                if provenance_updates:
                    self.workspace.refresh_external_provenance(
                        self.worker, source, provenance_updates
                    )
                    provenance_updates.clear()
        self._check_source(source, require_source)
        if adapter.repository_revision() != repository:
            raise RevisionConflict("Repository identity changed during source scan")
        if pending:
            self.workspace.record_external_many(self.worker, pending, resume=resumed)
        if provenance_updates:
            self.workspace.refresh_external_provenance(self.worker, source, provenance_updates)
        hidden = [
            (row["owner"], path) for path, row in previous.items() if not adapter.admitted(path)
        ]
        if hidden:
            self.workspace.hide_external(self.worker, hidden)
        if plan.full:
            self.workspace.finish_external(self.worker, provider, seen)
        elif previous:
            self.workspace.remove_external(
                self.worker, provider, [row["owner"] for row in previous.values()]
            )
        if failures:
            self.external_changes.close(provider)
        elif tracked:
            self.external_changes.finish(plan, self.worker, provider, adapter, make_adapter, check)
        elif plan.batch:
            plan.batch.acknowledge(self.workspace, self.worker)
        return failures, repository, plan.full

    def _check_source(self, source: dict, require_source: Callable[[], None]) -> None:
        self._check_thread()
        require_source()
        self.authorize_source(source["config"])

    def _check_thread(self) -> None:
        self._budget.remaining()
        if self.stopped.is_set():
            raise RuntimeError("Reference reconciliation stopped")
        self.check_now()
