"""Resumable reconciliation; the transport and authenticated lifetime are injected.

Only the Editor collector reads project files. Blocking analysis/SQLite work is
drained before releasing the cross-process lease, including on cancellation.
"""

from __future__ import annotations

import asyncio
import base64
import csv
from collections.abc import Awaitable, Callable
from dataclasses import asdict
import hashlib
from itertools import chain, islice
import time
import threading
import uuid

from services.reference_graph.analysis import (
    ANALYSIS_VERSION,
    Analysis,
    AnalysisIndex,
    Catalog,
    Facts,
    parse_csharp,
    serialized_references,
)
from services.reference_graph.external import ExternalSource, analyze_document
from services.reference_graph.store import Edge, Observation, RevisionConflict, _hash, _json
from services.reference_graph.workspace import GraphWorkspace


async def drained_task(task: asyncio.Task, *, _cancel_event: threading.Event | None = None):
    """Repeated caller cancellation cannot abandon an owned cleanup or thread."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if _cancel_event is not None:
                _cancel_event.set()
    if cancelled:
        # Retrieve any exception before propagating the owning cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def drained_thread(function, *args, _cancel_event: threading.Event | None = None, **kwargs):
    """Cancellation must not abandon a thread that can still publish or read."""
    return await drained_task(
        asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)),
        _cancel_event=_cancel_event,
    )


class Reconciler:
    def __init__(
        self,
        workspace: GraphWorkspace,
        collect: Callable[..., Awaitable[dict]],
        check: Callable[[], Awaitable[None]],
        check_now: Callable[[], None] = lambda: None,
        authorize_source: Callable[[dict], None] = lambda _: None,
    ):
        self.workspace = workspace
        self.store = workspace.store
        self.collect = collect
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

    def clear_caches(self) -> None:
        """Call only while this reconciler is idle; durable facts remain intact."""
        self._catalog_cache = None
        self._analysis_cache = None

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

    async def reconcile(self, *, force_external: bool = False) -> bool:
        # A checked query joins this scope's watcher without running a second
        # reconciliation using the same durable worker identity.
        async with self._gate:
            return await self._reconcile(force_external=force_external)

    async def _reconcile(self, *, force_external: bool) -> bool:
        await self.check()
        self.stopped.clear()
        heartbeat = None
        try:
            if not await drained_thread(self.workspace.acquire, self.worker):
                return False
            heartbeat = asyncio.create_task(self._heartbeat(asyncio.current_task()))
            self.progress = await drained_thread(self.workspace.progress)
            status = await self.collect("status")
            await self._phase("checking", editor=status, error=None)
            if any(status.get(key) for key in ("compiling", "importing", "compilation_failed")):
                await self._phase("waiting_for_editor")
                return False
            await drained_thread(
                self.workspace.ensure_analysis_version, self.worker, ANALYSIS_VERSION
            )
            compilation_context = status.get("compilation_context")
            if not self.progress.get("initialized") or self.progress.get("census"):
                await self._census(status, force=True)
            await self._changes()
            await self._compilation(compilation_context)
            await self._assets()
            await self._analysis()
            await self._external(force=force_external)
            status = await self.collect("status")
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

    async def _release(self, heartbeat: asyncio.Task | None) -> None:
        if heartbeat is not None:
            if not heartbeat.cancelling():
                heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await drained_thread(self.workspace.release, self.worker)

    async def _census(self, status: dict, *, force: bool) -> None:
        census = self.progress.get("census") or {
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
            except Exception:
                # An expired native snapshot must restart, never skip a page.
                await self._phase("census", census=None, initialized=False)
                raise
            census.update(snapshot=page["snapshot"], offset=page["next_offset"] or 0)
            self.progress.update(
                census=census,
                epoch=page["epoch"],
                after=page["revision"],
                catalog_through=census["through"],
            )
            await drained_thread(
                self.workspace.record_page,
                self.worker,
                page["assets"],
                self.progress,
                census["seen"],
                force=census["force"],
            )
            if page["next_offset"] is None:
                self.progress.update(census=None, initialized=True, phase="changes")
                await drained_thread(
                    self.workspace.finish_census, self.worker, census["seen"], self.progress
                )
                return

    async def _changes(self) -> None:
        while True:
            await self.check()
            page = await self.collect(
                "changes", epoch=self.progress["epoch"], after=self.progress["after"], page_size=256
            )
            if page["requires_census"]:
                await self._census(await self.collect("status"), force=True)
                continue
            catalog = False
            for change in page["changes"]:
                if change.get("kind") == "addressables_catalog":
                    catalog |= change["revision"] > self.progress.get("catalog_through", -1)
                    continue
                path = change["path"]
                if not await drained_thread(self.workspace.admitted, path):
                    continue
                if change["deleted"]:
                    await drained_thread(self.workspace.remove_path, self.worker, path)
                else:
                    asset = await self.collect("asset", path=path)
                    if asset.get("present"):
                        await drained_thread(
                            self.workspace.record_page, self.worker, [asset], self.progress, "event"
                        )
                    else:
                        # Folder moves/imports can affect descendants not listed individually.
                        await drained_thread(self.workspace.remove_path, self.worker, path)
                        catalog |= change["revision"] > self.progress.get("catalog_through", -1)
            await self._phase("changes", after=page["next_after"])
            if catalog:
                await self._census(await self.collect("status"), force=False)
                continue
            if not page["has_more"]:
                return

    async def _assets(self) -> None:
        await self._phase("collecting")
        after = ""
        while batch := await drained_thread(self.workspace.dirty_assets, after=after):
            for asset in batch:
                await self.check()
                try:
                    await self._asset(asset)
                except RevisionConflict:
                    raise
                except (OSError, ValueError, RecursionError) as error:
                    await drained_thread(self.workspace.fail_asset, self.worker, asset, error)
                after = asset["guid"]

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
                bindings = page.get("bindings", [])
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

    async def _asset(self, asset: dict) -> None:
        guid, owner = asset["guid"], "asset:" + asset["guid"]
        targets, offset, snapshot, fingerprint = [], 0, None, None
        while True:
            page = await self.collect(
                "dependencies", guid=guid, offset=offset, snapshot=snapshot, page_size=1000
            )
            if not page.get("present"):
                await drained_thread(self.workspace.remove_path, self.worker, asset["path"])
                return

            if fingerprint is not None and fingerprint != page["fingerprint"]:
                raise RevisionConflict("Dependencies changed while reading")
            fingerprint, snapshot = page["fingerprint"], page["snapshot"]
            targets.extend(page["targets"])
            offset = page["next_offset"]
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
                await self._source(asset)
                await drained_thread(self.workspace.clean_asset, self.worker, guid)
                return

    async def _source(self, asset: dict) -> None:
        if asset["source_format"] == "none":
            await drained_thread(
                self.workspace.drop_document, self.worker, "asset:" + asset["guid"]
            )
            return
        content, offset, fingerprint, context = bytearray(), 0, None, None
        while True:
            page = await self.collect(
                "source", guid=asset["guid"], offset=offset, fingerprint=fingerprint
            )
            if page["source_format"] == "binary":
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
            fingerprint = page["fingerprint"]
            if offset == 0:
                context = page.get("compilation_context")
                expected = self.progress.get("editor", {}).get("compilation_context")
                if (
                    expected
                    and (context is None or context.get("revision") != expected.get("revision"))
                    and asset["source_format"] == "csharp"
                ):
                    raise RevisionConflict("Compilation context changed while collecting")
            content.extend(base64.b64decode(page["bytes_base64"], validate=True))
            offset = page["next_offset"]
            if offset is None:
                format_, payload, fingerprint = await drained_thread(
                    self._parse, asset, content, context
                )
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
    def _parse(asset: dict, content: bytes, context: dict | None = None) -> tuple:
        text = content.decode(
            "utf-16" if content[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        )
        fingerprint = hashlib.sha256(content).hexdigest()
        if asset["source_format"] == "csharp":
            facts = parse_csharp("asset:" + asset["guid"], asset["path"], text, context=context)
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
                kind="script" if row["format"] == "csharp" else "asset",
                force=True,
                rules_revision=rules_revision,
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
                failures = await drained_thread(
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
    ) -> int:
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
    ) -> int:
        self._check_source(source, require_source)
        adapter = ExternalSource(source["config"])
        provider, seen = source["provider"], uuid.uuid4().hex
        previous = self.workspace.external_inputs(provider)
        analysis_key = _hash((ANALYSIS_VERSION, source["version"], rules, catalog_revision))
        failures = 0
        pending = []
        for path in adapter.files():
            self._check_source(source, require_source)
            old = previous.get(path)
            owner = provider + ":" + _hash(path)
            fingerprint = old["fingerprint"] if old else "unavailable"
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
            if len(pending) == 256:
                self._check_source(source, require_source)
                self.workspace.record_external_many(self.worker, pending)
                pending.clear()
        self._check_source(source, require_source)
        if pending:
            self.workspace.record_external_many(self.worker, pending)
        self.workspace.finish_external(self.worker, provider, seen)
        return failures

    def _check_source(self, source: dict, require_source: Callable[[], None]) -> None:
        self._check_thread()
        require_source()
        self.authorize_source(source["config"])

    def _check_thread(self) -> None:
        if self.stopped.is_set():
            raise RuntimeError("Reference reconciliation stopped")
        self.check_now()
