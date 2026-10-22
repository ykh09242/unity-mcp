"""Resumable reconciliation; the transport and authenticated lifetime are injected.

Only the Editor collector reads project files. Blocking analysis/SQLite work is
drained before releasing the cross-process lease, including on cancellation.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import asdict
import hashlib
import time
import threading
import uuid

from services.reference_graph.analysis import (
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


async def drained_thread(function, *args, _cancel_event: threading.Event | None = None, **kwargs):
    """Cancellation must not abandon a thread that can still publish or read."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
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
        await self.check()
        self.stopped.clear()
        if not await drained_thread(self.workspace.acquire, self.worker):
            return False
        heartbeat = asyncio.create_task(self._heartbeat(asyncio.current_task()))
        try:
            self.progress = await drained_thread(self.workspace.progress)
            status = await self.collect("status")
            await self._phase("checking", editor=status, error=None)
            if any(status.get(key) for key in ("compiling", "importing", "compilation_failed")):
                await self._phase("waiting_for_editor")
                return False
            if not self.progress.get("initialized") or self.progress.get("census"):
                await self._census(status, force=True)
            await self._changes()
            await self._assets()
            await self._analysis()
            await self._external(force=force_external)
            status = await self.collect("status")
            caught_up = (
                status["epoch"] == self.progress.get("epoch")
                and status["revision"] == self.progress.get("after")
                and not self.progress.get("external_errors")
                and not any(
                    status.get(key) for key in ("compiling", "importing", "compilation_failed")
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
        while batch := await drained_thread(self.workspace.dirty_assets):
            for asset in batch:
                await self.check()
                guid, owner = asset["guid"], "asset:" + asset["guid"]
                targets, offset, snapshot, fingerprint = [], 0, None, None
                while True:
                    page = await self.collect(
                        "dependencies", guid=guid, offset=offset, snapshot=snapshot, page_size=1000
                    )
                    if not page.get("present"):
                        await drained_thread(self.workspace.remove_path, self.worker, asset["path"])
                        break
                    if fingerprint is not None and fingerprint != page["fingerprint"]:
                        raise RevisionConflict("Dependencies changed while reading")
                    fingerprint, snapshot = page["fingerprint"], page["snapshot"]
                    targets.extend(page["targets"])
                    offset = page["next_offset"]
                    if offset is None:
                        generation = await drained_thread(
                            self.store.observe,
                            owner,
                            "unity",
                            fingerprint,
                            path=asset["path"],
                            kind=asset["kind"],
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
                        break

    async def _source(self, asset: dict) -> None:
        if asset["source_format"] == "none":
            await drained_thread(
                self.workspace.drop_document, self.worker, "asset:" + asset["guid"]
            )
            return
        content, offset, fingerprint = bytearray(), 0, None
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
            content.extend(base64.b64decode(page["bytes_base64"], validate=True))
            offset = page["next_offset"]
            if offset is None:
                format_, payload, fingerprint = await drained_thread(self._parse, asset, content)
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
    def _parse(asset: dict, content: bytes) -> tuple:
        text = content.decode(
            "utf-16" if content[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        )
        fingerprint = hashlib.sha256(content).hexdigest()
        if asset["source_format"] == "csharp":
            return (
                "csharp",
                asdict(parse_csharp("asset:" + asset["guid"], asset["path"], text)),
                fingerprint,
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
        rules = await drained_thread(self.store.rules_state)
        rules_changed = rules["revision"] != await drained_thread(
            self.workspace.state, "analyzed_rules_revision", -1
        )
        if not rules_changed and not await drained_thread(
            self.workspace.state, "analysis_dirty", False
        ):
            return
        await self._phase("analyzing")
        documents = await drained_thread(self.workspace.documents)
        assets = await drained_thread(self.workspace.catalog)
        index = await drained_thread(self._build_index, documents, assets, rules)
        affected = await drained_thread(
            index.affected, await drained_thread(self.workspace.changed_inputs)
        )
        selected = [
            row
            for row in documents
            if rules_changed or row["state"] != "ready" or row["owner"] in affected
        ]
        for offset in range(0, len(selected), 100):
            await self.check()
            await drained_thread(
                self._analyze_batch,
                selected[offset : offset + 100],
                index,
                rules["revision"],
                _cancel_event=self.stopped,
            )
        await drained_thread(self.workspace.finish_analysis, self.worker, rules["revision"])

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
        generations = self.store.observe_batch(observations)
        publications = []
        for row, generation in zip(documents, generations):
            self._check_thread()
            result = (
                index.analyze(row["owner"])
                if row["format"] == "csharp"
                else Analysis(
                    [Edge(**edge) for edge in row["data"]["edges"]],
                    row["data"]["unresolved"],
                    coverage=row["data"]["coverage"],
                )
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
            return
        await self._phase("external", external_errors={})
        catalog = await drained_thread(Catalog, await drained_thread(self.workspace.catalog))
        rules = (await drained_thread(self.store.rules_state))["revision"]
        catalog_revision = await drained_thread(self.workspace.state, "catalog_revision", 0)
        for source in sources:
            await self.check()
            try:
                await drained_thread(
                    self._external_source,
                    source,
                    catalog,
                    rules,
                    catalog_revision,
                    force,
                    _cancel_event=self.stopped,
                )
            except RevisionConflict:
                raise
            except (OSError, ValueError) as error:
                self.progress["external_errors"][source["provider"]] = (
                    type(error).__name__ + ": " + str(error)
                )
                await self._phase("external")

    def _external_source(
        self, source: dict, catalog: Catalog, rules: int, catalog_revision: int, force: bool
    ) -> None:
        self._check_source(source)
        adapter = ExternalSource(source["config"])
        provider, seen = source["provider"], uuid.uuid4().hex
        previous = self.workspace.external_inputs(provider)
        analysis_key = _hash((source["version"], rules, catalog_revision))
        for path in adapter.files():
            self._check_source(source)
            stamp = _json(adapter.stamp(path))
            old = previous.get(path)
            owner = provider + ":" + _hash(path)
            fingerprint = old["fingerprint"] if old else ""
            if force or old is None or old["stamp"] != stamp or old["analysis_key"] != analysis_key:
                document = adapter.read(path)
                generation = self.store.observe(
                    owner,
                    provider,
                    document.fingerprint,
                    path=path,
                    kind="external",
                    force=True,
                    source_version=source["version"],
                    rules_revision=rules,
                )
                result = analyze_document(path, document.text, catalog, adapter.mappings)
                if _json(adapter.stamp(path)) != stamp:
                    raise RevisionConflict("External input changed during analysis")
                self._check_source(source)
                if not self.workspace.publish(self.worker, owner, provider, generation, result):
                    raise RevisionConflict("External source grant changed during analysis")
                fingerprint = document.fingerprint
            self.workspace.record_external(
                self.worker, provider, owner, path, fingerprint, stamp, analysis_key, seen
            )
        self._check_source(source)
        self.workspace.finish_external(self.worker, provider, seen)

    def _check_source(self, source: dict) -> None:
        self._check_thread()
        self.workspace.require_source(source["provider"], source["version"])
        self.authorize_source(source["config"])

    def _check_thread(self) -> None:
        if self.stopped.is_set():
            raise RuntimeError("Reference reconciliation stopped")
        self.check_now()
