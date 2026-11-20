"""Run a registered standalone candidate through the normal verification handle lifecycle."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from cli.utils.play_scenario_player import (
    PROCESS_LOG_LIMIT,
    PlayerDispatch,
    PlayerRunOptions,
    run_player,
)
from services.reference_graph.change_evidence import explain_asset_references
from services.verification.loader_observations import import_player_loader_capture
from services.verification.player_candidate import prepare_candidate
from services.verification.reports import final_verdict
from services.verification.runner import VerificationCanceled, VerificationRunner
from services.verification.selected_checks import SelectedChecks


def _resource_key(path):
    if "/Resources/" in path:
        return path.rsplit("/Resources/", 1)[1].rsplit(".", 1)[0]
    return None


def _reference_evidence(prepared):
    manifest = prepared.candidate
    scripts, serialized = {}, {}
    coverage = []
    total = 0
    for path, digest in manifest.inputs.items():
        suffix = Path(path).suffix
        if suffix not in {".cs", ".prefab", ".unity", ".asset"}:
            continue
        location = prepared.root / prepared.candidate_id / "sources" / path
        if not safe_local_path(location).is_file():
            location = prepared.project / path
        try:
            data = read_bytes(location, 512 * 1024)
        except (OSError, ValueError):
            coverage.append({"path": path, "reason": "source_unavailable_or_over_limit"})
            continue
        total += len(data)
        if total > 512 * 1024:
            coverage.append({"reason": "source_analysis_limit"})
            break
        if sha256(data).hexdigest() != digest:
            coverage.append({"path": path, "reason": "source_digest_mismatch"})
            continue
        try:
            (scripts if suffix == ".cs" else serialized)[path] = data.decode("utf-8-sig")
        except UnicodeError:
            coverage.append({"path": path, "reason": "source_not_text"})
    assets = [
        {
            "guid": guid,
            "path": path,
            "keys": [{"scheme": "resources", "value": key}] if (key := _resource_key(path)) else [],
        }
        for path, guid in manifest.assets.items()
    ]
    previous = (
        {guid: path for path, guid in prepared.baseline.assets.items()} if prepared.baseline else {}
    )
    evidence = {}
    for asset in assets[:4]:
        guid = asset["guid"]
        key = _resource_key(previous.get(guid, ""))
        evidence[guid] = explain_asset_references(
            asset_guid=guid,
            assets=assets,
            scripts=scripts,
            serialized=serialized,
            previous_keys=[key] if key else [],
        )
    if len(assets) > 4:
        coverage.append({"reason": "asset_analysis_limit"})
    return {"assets": evidence, "coverage": coverage}


def _state_result(directory, check):
    content = read_bytes(directory / "player.log", PROCESS_LOG_LIMIT)
    if len(content) == PROCESS_LOG_LIMIT:
        return "incomplete", {"expected": check.expected, "reason": "state_log_retention_limit"}
    log = content.decode("utf-8", errors="replace")
    values = []
    for line in log.split("\n"):
        if not line.startswith(check.log_marker):
            continue
        raw = line[len(check.log_marker) :]
        if len(raw) > 8192:
            raise ValueError("state_observation_over_limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_state_observation")
        selected = {key: value[key] for key in check.expected if key in value}
        values.append(selected)
    if not values or any(
        json.dumps(row, sort_keys=True) != json.dumps(values[0], sort_keys=True)
        for row in values[1:]
    ):
        return "incomplete", {
            "expected": check.expected,
            "reason": "state_observation_missing_or_ambiguous",
        }
    matched = json.dumps(values[0], sort_keys=True) == json.dumps(check.expected, sort_keys=True)
    return "passed" if matched else "failed", {"expected": check.expected, "observed": values[0]}


class PlayerVerificationRunner(VerificationRunner):
    def __init__(self, io, profile, selection, prepared, broader_profile=None):
        super().__init__(io, profile, selection)
        self.prepared = prepared
        self.player_job_id = None
        self.dispatch = PlayerDispatch()
        self.report.pop("profile")
        self.report.update(
            comparison=prepared.comparison(),
            executed_checks=[],
            source_token=prepared.candidate.source_revision,
            source_mode=selection["source_mode"],
            limitations=["Live source identity is sampled before and after execution."],
        )
        selected = self.report["comparison"]["selected_checks"]
        self.report["additional_checks_outstanding"] = [
            {"kind": check["kind"], "id": check["id"]}
            for check in selected.get("checks", [])
            if (check["kind"], check["id"]) != ("scenario", prepared.candidate.scenario)
        ]
        self.deadline = monotonic() + 180
        self.selected = (
            SelectedChecks(self, selected, broader_profile)
            if selection.get("execute_selected_checks")
            else None
        )

    async def current(self):
        fresh = await asyncio.to_thread(
            prepare_candidate, {"projectRoot": str(self.prepared.project)}, self.selection
        )
        inventories = fresh.inventories()
        if inventories:
            self.report["latest_input_inventory"] = inventories
        self.source_check_state = "stale"
        if not self.prepared.complete or not fresh.complete:
            self.source_check_state = "incomplete"
            return False
        return fresh == self.prepared and (
            self.selection["source_mode"] == "frozen" or fresh.live == fresh.candidate.inputs
        )

    async def run(self):
        try:
            await self.check()
            if not await self.current():
                self.stage(
                    "candidate_inputs",
                    self.source_check_state,
                    {"reason": "candidate_inputs_changed_or_incomplete"},
                )
            else:
                await self.execute_candidate()
                if self.selected is not None:
                    await self.selected.run()
                await self.check()
                self.stage(
                    "final_source", "passed" if await self.current() else self.source_check_state
                )
        except VerificationCanceled:
            self.stage("cancellation", "canceled")
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            self.stage("interruption", "incomplete", {"reason": str(error)[:256]})
        if self.selected is not None:
            self.selected.finish()
        self.report.update(
            state="canceled" if self.cancel_requested else final_verdict(self.report["stages"]),
            finished_at_unix_ms=int(time() * 1000),
        )
        self.publish()
        return self.report

    async def execute_candidate(self):
        prepared, manifest = self.prepared, self.prepared.candidate
        if prepared.baseline_candidate_id and prepared.baseline is None:
            self.stage("baseline", "incomplete", {"reason": "baseline_missing"})
        references = await asyncio.to_thread(_reference_evidence, prepared)
        self.stage(
            "reference_impact",
            "incomplete" if references["coverage"] else "passed",
            references,
            required=False,
        )
        await self.io.authorize("manage_play_scenario", {"action": "run"}, self.deadline)
        await self.check()
        job_id = uuid4().hex
        self.player_job_id = job_id
        directory = safe_local_path(prepared.root / prepared.candidate_id)
        output = safe_local_path(directory / "verification-runs")
        options = PlayerRunOptions(
            build_directory=safe_local_path(directory / "build"),
            output_directory=output,
            timeout_seconds=120,
            cleanup_wait_seconds=10,
            deadline_monotonic=self.deadline,
            player_job_id=job_id,
            expected_build_id=manifest.build_id,
            expected_build_revision=manifest.source_revision,
            source_revision=manifest.source_revision,
            require_verified_payload=True,
            expected_payload_hash=manifest.payload_hash,
            expected_scenario=manifest.scenario,
            cancel_requested=lambda: self.cancel_requested,
            dispatch=self.dispatch,
        )
        child = {"kind": "standalone_player", "job_id": job_id, "settled": False}
        self.report["children"].append(child)
        self.publish()
        try:
            outcome = await asyncio.to_thread(run_player, options)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            child["dispatch"] = self.dispatch.state
            child["settled"] = self.player_settled(job_id)
            raise
        child["settled"] = True
        raw = outcome.report
        execution = {
            "kind": "scenario",
            "id": manifest.scenario,
            "job_id": job_id,
            "status": raw["status"],
        }
        if self.selected is not None:
            definition_hash = raw["reproduction"]["definition_hash"]
            child.update(
                check_kind="scenario", check_id=manifest.scenario, definition_hash=definition_hash
            )
            execution["definition_hash"] = definition_hash
        self.report["executed_checks"].append(execution)
        expected = {
            "job_id": job_id,
            "build_id": manifest.build_id,
            "build_source_revision": manifest.source_revision,
            "payload_hash": manifest.payload_hash,
        }
        keys = {("resources", key) for path in manifest.assets if (key := _resource_key(path))}
        if prepared.baseline:
            keys.update(
                ("resources", key)
                for path in prepared.baseline.assets
                if (key := _resource_key(path))
            )
        runtime = import_player_loader_capture(raw, expected=expected, expected_keys=keys)
        coverage = runtime["coverage"]
        complete = (
            coverage["state"] == "instrumented_hooks_only"
            and coverage["dropped"] == 0
            and not coverage["unobserved_keys"]
        )
        self.stage(
            "loader_observations", "passed" if complete else "incomplete", runtime, required=False
        )
        player_state = (
            "failed"
            if raw["status"] in {"failed", "timed_out"}
            else "passed"
            if raw["status"] == "succeeded" and outcome.exit_code == 0
            else "incomplete"
        )
        self.stage(
            "standalone_player",
            player_state,
            {
                **expected,
                "scenario": manifest.scenario,
                "status": raw["status"],
                "exit_code": outcome.diagnostics.get("process_exit_code"),
                "client_error": outcome.client_error,
                "finalization_state": raw["finalization_state"],
                "artifacts": str(outcome.directory),
            },
        )
        if self.selected is not None:
            self.selected.standalone_result(
                player_state, job_id, raw["status"], execution["definition_hash"]
            )
        if manifest.state_check:
            try:
                state, evidence = await asyncio.to_thread(
                    _state_result, outcome.directory, manifest.state_check
                )
            except (OSError, ValueError) as error:
                state, evidence = (
                    "incomplete",
                    {"expected": manifest.state_check.expected, "reason": str(error)[:256]},
                )
            self.stage("observed_state", state, evidence)

    def player_settled(self, job_id):
        if job_id != self.player_job_id:
            return False
        if self.dispatch.confirmed_not_started:
            return True
        directory = self.prepared.root / self.prepared.candidate_id / "verification-runs" / job_id
        try:
            process = json.loads(read_bytes(directory / "process.json", 64 * 1024))
            return (
                isinstance(process, dict)
                and process.get("job_id") == job_id
                and process.get("process_ended") is True
            )
        except (OSError, ValueError):
            return False
