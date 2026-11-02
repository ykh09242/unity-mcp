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

from services.reference_graph.analysis import (
    ANALYSIS_VERSION,
    Analysis,
    AnalysisIndex,
    Catalog,
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
from services.reference_graph.external import ExternalSource, analyze_document
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

    async def validate_ignore(self, status: dict | None = None) -> bool:
        """Validate a warm native snapshot without rereading assets or external documents."""
        if not (await drained_thread(self.workspace.policy))["respect_gitignore"]:
            return True
        known = await drained_thread(self.workspace.ignore_status)
        if not known.get("snapshot"):
            return False
        try:
            text(known.get("snapshot"), "snapshot")
            known_epoch = text(known.get("epoch"), "epoch")
            known_revision = integer(known.get("ignore_revision"), "ignore_revision")
            if status is not None:
                text(status.get("epoch"), "epoch")
                integer(status.get("revision"), "revision")
                integer(status.get("ignore_revision"), "ignore_revision")
            if status is not None and "ignore_current_verified" in status:
                digest = hashlib.sha256(known["snapshot"].encode("utf-8")).hexdigest()
                if (
                    type(status.get("ignore_current_validation")) is int
                    and status.get("ignore_current_validation") == 1
                    and status.get("ignore_current_verified") is True
                    and status.get("ignore_snapshot_digest") == digest
                    and status.get("epoch") == known_epoch
                    and status.get("ignore_revision") == known_revision
                ):
                    return True
                await drained_thread(self.workspace.invalidate_ignore)
                return False
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
        await drained_thread(self.workspace.invalidate_ignore)
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

    async def reconcile(self, *, force_external: bool = False, wait: bool = True) -> bool | None:
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
            return await self._reconcile(force_external=force_external)
        finally:
            self.partial_proof = None
            self._gate.release()

    async def _reconcile(self, *, force_external: bool) -> bool:
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
                    return await self._run_reconcile(force_external=force_external)
            except TimeoutError as error:
                if not deadline.expired():
                    raise
                raise CollectionBudgetExceeded("max_seconds", self.limits.max_seconds) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            try:
                await self._phase("error", error=type(error).__name__ + ": " + str(error))
            except RevisionConflict:
                pass
            raise
        finally:
            # Shutdown and exact credential retirement may both cancel this
            # owner. Neither can interrupt heartbeat/thread/DB lease cleanup.
            await drained_task(asyncio.create_task(self._release(heartbeat)))

    async def _run_reconcile(self, *, force_external: bool) -> bool:
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
            await self._census(status, force=True)
        await self._changes()
        await self._compilation(compilation_context)
        # A complete catalog and journal permit narrowly scoped, current Unity
        # dependency pages while the remaining assets are being collected.
        if not (await drained_thread(self.workspace.delivery_fence))[2]:
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
        await self._external(force=force_external)
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
                "after": revision,
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
        while True:
            await self.check()
            page = await self.collect(
                "changes", epoch=self.progress["epoch"], after=self.progress["after"], page_size=256
            )
            if boolean(page.get("requires_census"), "requires_census"):
                await self._census(await self.collect("status"), force=True)
                continue
            changes, following, more = change_page(
                page, self.progress["after"], maximum=256, epoch=self.progress["epoch"]
            )
            self._budget.admit_items(len(changes))
            catalog = False
            admitted = await drained_thread(self.workspace.admission)
            moved_roots = set()
            pending_assets = []
            blocked = False

            async def flush_assets():
                if pending_assets:
                    await drained_thread(
                        self.workspace.record_page,
                        self.worker,
                        pending_assets,
                        self.progress,
                        "event",
                    )
                    pending_assets.clear()

            for change in changes:
                if change.get("kind") == "addressables_catalog":
                    catalog |= change["revision"] > self.progress.get("catalog_through", -1)
                    continue
                path = change["path"]
                if not admitted(path) and not blocked:
                    await drained_thread(self.workspace.block_admission)
                    blocked = True
                if change["deleted"]:
                    await flush_assets()
                    moved_roots.update(await self._remove_changed_path(path))
                else:
                    asset = await self.collect("asset", path=path)
                    if asset.get("present"):
                        self._budget.admit_items(1)
                        pending_assets.append(asset)
                    else:
                        await flush_assets()
                        # Folder moves/imports can affect descendants not listed individually.
                        if path not in moved_roots:
                            if admitted(path):
                                await drained_thread(self.workspace.remove_path, self.worker, path)
                            if change["revision"] > self.progress.get("catalog_through", -1):
                                await drained_thread(self.workspace.block_admission)
                                catalog = True
            await flush_assets()
            await self._phase("changes", after=following)
            if catalog:
                await self._census(await self.collect("status"), force=False)
                continue
            if not more:
                return

    async def _remove_changed_path(self, path: str) -> set[str]:
        after = ("", "")
        destination, ambiguous = None, False
        while candidates := await drained_thread(self.workspace.descendant_assets, path, after):
            # A folder-only move can omit child events. Resolve known GUIDs before
            # deleting the old prefix, without a global inventory or content read.
            await drained_thread(self.workspace.block_admission)
            assets = []
            for candidate in candidates:
                asset = await self.collect("asset", guid=candidate["guid"])
                if asset.get("present"):
                    self._budget.admit_items(1)
                    assets.append(asset)
                    suffix = "/" + candidate["path"][len(path) + 1 :]
                    if asset["path"].endswith(suffix):
                        root = asset["path"][: -len(suffix)]
                        if destination is None:
                            destination = root
                        elif destination != root:
                            ambiguous = True
                    else:
                        ambiguous = True
            if assets:
                await drained_thread(
                    self.workspace.record_page, self.worker, assets, self.progress, "event"
                )
            after = (candidates[-1]["path"], candidates[-1]["guid"])
        await drained_thread(self.workspace.remove_path, self.worker, path)
        return {destination} if destination and not ambiguous else set()

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
        result = serialized_references(text, Catalog([]))
        return (
            "summary",
            {
                "edges": [asdict(edge) for edge in result.edges],
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
        manifest = await drained_thread(self.workspace.document_manifest)
        blocked = await drained_thread(self.workspace.blocked_analysis)
        manifest = {owner: row for owner, row in manifest.items() if owner not in blocked}
        changed = await drained_thread(self.workspace.changed_inputs)
        index, affected = await drained_thread(
            self._refresh_analysis, manifest, rules, changed, _cancel_event=self.stopped
        )
        selected = [
            owner
            for owner, row in manifest.items()
            if rules_changed or row["state"] != "ready" or owner in affected
        ]
        for offset in range(0, len(selected), 100):
            await self.check()
            documents = await drained_thread(
                self.workspace.documents, selected[offset : offset + 100]
            )
            await drained_thread(
                self._analyze_batch,
                documents,
                index,
                rules["revision"],
                _cancel_event=self.stopped,
            )
        await drained_thread(self.workspace.finish_analysis, self.worker, rules["revision"])
        self._analysis_used = time.monotonic()

    def _refresh_analysis(self, manifest: dict, rules: dict, changed_inputs: set[str]) -> tuple:
        cache = self._analysis_cache
        if cache is not None and cache[0] != rules["revision"]:
            cache = None
        fingerprints = {
            owner: (row["format"], row["fingerprint"]) for owner, row in manifest.items()
        }
        changed = sorted(
            owner
            for owner in manifest
            if cache is None
            or cache[2].get(owner) != fingerprints[owner]
            or owner in changed_inputs
        )

        def facts():
            owners = [owner for owner in changed if manifest[owner]["format"] == "csharp"]
            for offset in range(0, len(owners), 256):
                self._check_thread()
                for row in self.workspace.documents(owners[offset : offset + 256]):
                    self._check_thread()
                    yield Facts.from_dict(row["data"])

        try:
            revision, assets = self.workspace.catalog_snapshot(cache[1] if cache else None)
            if cache is None:
                index = AnalysisIndex(
                    facts(),
                    Catalog(assets),
                    [
                        rule["config"]
                        for rule in rules["rules"]
                        if rule["config"].get("enabled", True)
                    ],
                    check=self._check_thread,
                )
                affected = index.affected(changed_inputs)
                if changed_inputs:
                    # A fresh index has no former symbol lookups. Changed declarations
                    # can rebind ready callers without a persisted dependency on them.
                    affected.update(
                        owner for owner, row in manifest.items() if row["format"] == "csharp"
                    )
            else:
                index = cache[3]
                if assets is not None:
                    index.catalog = Catalog(assets)
                removed = (cache[2].keys() - manifest.keys()) | {
                    owner for owner in changed if manifest[owner]["format"] != "csharp"
                }
                affected = index.update(facts(), removed=removed)
                affected.update(index.affected(changed_inputs))
            self._analysis_cache = (rules["revision"], revision, fingerprints, index)
            self._analysis_used = time.monotonic()
            return index, affected
        except BaseException:
            # An interrupted in-place update must never become a reusable index.
            self._analysis_cache = None
            raise

    @staticmethod
    def _build_index(documents: list[dict], assets: list[dict], rules: dict) -> AnalysisIndex:
        return AnalysisIndex(
            [Facts.from_dict(row["data"]) for row in documents if row["format"] == "csharp"],
            Catalog(assets),
            [rule["config"] for rule in rules["rules"] if rule["config"].get("enabled", True)],
        )

    def _analyze_batch(
        self, documents: list[dict], index: AnalysisIndex, rules_revision: int
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
        generations = self.workspace.observe_many(self.worker, observations)
        publications = []
        for row, generation in zip(documents, generations):
            self._check_thread()
            if row["format"] == "csharp":
                events = index.events(row["owner"])
                preview = list(islice(events, 257))
                if len(preview) > 256:
                    self.workspace.publish_code_events(
                        self.worker,
                        row["owner"],
                        generation,
                        chain(preview, events),
                        lambda owner=row["owner"]: index.transitive_dependencies(owner),
                        check=self._check_thread,
                    )
                    continue
                result = Analysis(
                    [event for event in preview if isinstance(event, Edge)],
                    [event for event in preview if isinstance(event, dict)],
                    dependencies=index.transitive_dependencies(row["owner"]),
                )
            else:
                result = Analysis(
                    [Edge(**edge) for edge in row["data"]["edges"]],
                    row["data"]["unresolved"],
                    coverage=row["data"]["coverage"],
                )
            publications.append((row["owner"], "code", generation, result))
        self._check_thread()
        if not all(self.workspace.publish_many(self.worker, publications)):
            raise RevisionConflict("Analysis inputs changed before publication")

    async def _external(self, *, force: bool) -> None:
        self.progress["external_errors"] = {}
        sources = [
            source
            for source in await drained_thread(self.store.sources)
            if source["provider"].startswith("external:") and source["enabled"]
        ]
        if not sources:
            self._catalog_cache = None
            return
        await self._phase("external", external_errors={})
        catalog_revision, assets = await drained_thread(
            self.workspace.catalog_snapshot,
            self._catalog_cache[0] if self._catalog_cache else None,
        )
        if assets is not None:
            self._catalog_cache = (catalog_revision, await drained_thread(Catalog, assets))
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
                failures, repository = await drained_thread(
                    self._external_source,
                    source,
                    catalog,
                    rules,
                    catalog_revision,
                    force,
                    _cancel_event=self.stopped,
                )
                scan.update(
                    state="failed" if failures else "verified_at_scan",
                    failed_files=failures,
                    completed_at=time.time(),
                    repository=repository,
                )
                if not failures:
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
        self, source: dict, catalog: Catalog, rules: int, catalog_revision: int, force: bool
    ) -> tuple[int, dict]:
        with self.workspace.source_guard(source["provider"], source["version"]) as require_source:
            return self._scan_external_source(
                source, catalog, rules, catalog_revision, force, require_source
            )

    def _scan_external_source(
        self,
        source: dict,
        catalog: Catalog,
        rules: int,
        catalog_revision: int,
        force: bool,
        require_source: Callable[[], None],
    ) -> tuple[int, dict]:
        self._check_source(source, require_source)
        policy = self.workspace.policy()
        adapter = ExternalSource(
            source["config"],
            respect_gitignore=policy["respect_gitignore"],
            exclude=policy["exclude"],
            budget=self._budget,
        )
        repository = adapter.repository_revision()
        provider, seen = source["provider"], uuid.uuid4().hex
        previous = self.workspace.external_inputs(provider, admission=True)
        analysis_key = _hash((ANALYSIS_VERSION, source["version"], rules, catalog_revision))
        failures = 0
        pending = []
        provenance_updates = []
        for path in adapter.files(check=partial(self._check_source, source, require_source)):
            self._check_source(source, require_source)
            old = previous.pop(path, None)
            owner = provider + ":" + _hash(path)
            fingerprint = (old or {}).get("fingerprint") or "unavailable"
            stamp = ""
            recorded = False
            try:
                stamp = _json(adapter.stamp(path))
                if (
                    force
                    or old is None
                    or old["state"] != "ready"
                    or old["stamp"] != stamp
                    or old["analysis_key"] != analysis_key
                ):
                    document = adapter.read(path)
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
                    result = analyze_document(path, document.text, catalog, adapter.mappings)
                    if _json(adapter.stamp(path)) != stamp:
                        raise RevisionConflict("External input changed during analysis")
                    self._check_source(source, require_source)
                    if not self.workspace.publish(self.worker, owner, provider, generation, result):
                        raise RevisionConflict("External source grant changed during analysis")
            except RevisionConflict:
                raise
            except (OSError, ValueError, csv.Error, RecursionError) as error:
                self._check_source(source, require_source)
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
                provenance = adapter.provenance(path, fingerprint, repository)
                if old.get("provenance") != _json(provenance):
                    provenance_updates.append((owner, fingerprint, provenance))
            if len(pending) == 256:
                self._check_source(source, require_source)
                self.workspace.record_external_many(self.worker, pending)
                pending.clear()
                if provenance_updates:
                    self.workspace.refresh_external_provenance(
                        self.worker, source, provenance_updates
                    )
                    provenance_updates.clear()
        self._check_source(source, require_source)
        if adapter.repository_revision() != repository:
            raise RevisionConflict("Repository identity changed during source scan")
        if pending:
            self.workspace.record_external_many(self.worker, pending)
        if provenance_updates:
            self.workspace.refresh_external_provenance(self.worker, source, provenance_updates)
        hidden = [
            (row["owner"], path) for path, row in previous.items() if not adapter.admitted(path)
        ]
        if hidden:
            self.workspace.hide_external(self.worker, hidden)
        self.workspace.finish_external(self.worker, provider, seen)
        return failures, repository

    def _check_source(self, source: dict, require_source: Callable[[], None]) -> None:
        self._check_thread()
        require_source()
        self.authorize_source(source["config"])

    def _check_thread(self) -> None:
        self._budget.remaining()
        if self.stopped.is_set():
            raise RuntimeError("Reference reconciliation stopped")
        self.check_now()
