"""Run registered change mappings within the candidate's existing ownership boundary."""

import asyncio
from hashlib import sha256
import json
import re
from time import monotonic
from uuid import uuid4

from models.script_quality import ScriptSelection
from models.verification import VerificationProfile
from services.script_quality.pages import encoded_size
from services.verification.profiles import profile_permissions
from services.verification.reports import final_verdict, test_settled
from services.verification.runner import VerificationCanceled, VerificationRunner
from transport.native_arbitration import VerifiedQueueRefusal

SCENARIO_STATES = {
    "succeeded": "passed",
    "failed": "failed",
    "timed_out": "failed",
    "cancelled": "canceled",
    "interrupted": "incomplete",
}


def profile_definition_hash(profile, selection):
    definition = {"profile": profile.model_dump(mode="json"), "selection": selection}
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


class SelectedChecks:
    def __init__(self, runner, selection, broader_profile):
        self.runner = runner
        self.profile = (
            VerificationProfile.model_validate(broader_profile) if broader_profile else None
        )
        self.active_profile = None
        self.rows = [
            {
                "kind": item["kind"],
                "id": item["id"],
                "state": "pending",
                "job_id": None,
                "evidence": {},
            }
            for item in selection.get("checks", [])
        ]
        self.selection_gaps = selection.get("outstanding", [])
        runner.report["selected_check_results"] = self.rows
        self.permissions = set()
        for row in self.rows:
            if row["kind"] == "test":
                self.permissions.add("test")
            elif row["kind"] == "profile" and self.profile:
                self.permissions.update(profile_permissions(self.profile))

    def cancel(self):
        if self.active_profile is not None:
            self.active_profile.cancel_requested = True

    def standalone_result(self, state, job_id, status, definition_hash):
        for row in self.rows:
            if row["kind"] == "scenario" and row["id"] == self.runner.prepared.candidate.scenario:
                row.update(
                    state=state,
                    job_id=job_id,
                    definition_hash=definition_hash,
                    evidence={
                        "target": "standalone_player",
                        "status": status,
                        "definition_hash": definition_hash,
                        "candidate_source_revision": self.runner.prepared.candidate.source_revision,
                    },
                )
                return

    async def run(self):
        # Only Editor checks get this additional bounded budget. The Player keeps its original deadline.
        self.runner.deadline += 600
        for index, row in enumerate(self.rows):
            if row["state"] != "pending":
                continue
            # Reserve space for terminal rows and the final source sample in the 512 KiB report.
            # Timing is optional and the store already drops it when necessary.
            available = 512 * 1024 - 48 * 1024 - 64 * (len(self.rows) - index)
            if (
                encoded_size({k: v for k, v in self.runner.report.items() if k != "timing"})
                > available
            ):
                for pending in self.rows[index:]:
                    if pending["state"] == "pending":
                        pending.update(
                            state="incomplete", evidence={"reason": "report_evidence_limit"}
                        )
                break
            row["state"] = "running"
            self.runner.publish()
            try:
                await self.runner.check()
                if any(not child["settled"] for child in self.runner.report["children"]):
                    raise ValueError("previous_native_child_unsettled")
                if not self.runner.report["comparison"]["live_matches_candidate"]:
                    raise ValueError("historical_candidate_unavailable_in_editor")
                if not await self.runner.current():
                    row.update(
                        state=self.runner.source_check_state,
                        evidence={"reason": "candidate_inputs_changed_or_incomplete"},
                    )
                    continue
                await self.runner.check()
                if row["kind"] == "scenario":
                    await self.run_scenario(row)
                else:
                    await self.run_profile(row)
                if not await self.runner.current():
                    row["state"] = self.runner.source_check_state
                    row["evidence"]["reason"] = "candidate_inputs_changed_or_incomplete"
            except VerificationCanceled:
                row.update(
                    state="canceled", evidence={**row["evidence"], "reason": "cancel_requested"}
                )
                raise
            except (OSError, ValueError, RuntimeError) as error:
                row.update(
                    state="failed" if row["state"] == "failed" else "incomplete",
                    evidence={**row["evidence"], "reason": str(error)[:256] or "check_deadline"},
                )
            finally:
                self.runner.publish()

    async def run_profile(self, row):
        if row["kind"] == "test":
            profile = VerificationProfile(
                refresh_policy="never",
                analysis_required=False,
                impact="off",
                test_mode=self.runner.selection["selected_test_mode"],
                test_names=[row["id"]],
            )
            selection = {"test_names": [row["id"]]}
        else:
            profile = self.profile
            if profile is None:
                raise ValueError("mapped_profile_not_found")
            selection = {}
            if profile.analysis_required:
                scripts = sorted(
                    path
                    for path in self.runner.prepared.candidate.inputs
                    if path.lower().endswith(".cs")
                )
                if not scripts:
                    raise ValueError("mapped_profile_requires_script_inputs")
                selection = ScriptSelection(paths=scripts).model_dump(exclude_none=True)
        first_child = len(self.runner.report["children"])
        digest = profile_definition_hash(profile, selection)
        row["definition_hash"] = digest
        row["evidence"].update(
            target="editor",
            definition_hash=digest,
            candidate_source_revision=self.runner.prepared.candidate.source_revision,
        )

        def publish(report):
            children = report["children"]
            for native in children:
                native.update(check_kind=row["kind"], check_id=row["id"], definition_hash=digest)
            self.runner.report["children"][first_child:] = children
            row["job_id"] = children[0].get("job_id") if len(children) == 1 else None
            self.runner.publish()

        child = VerificationRunner(self.runner.io, profile, selection, emit=publish)
        child.deadline = min(self.runner.deadline, child.deadline)
        self.active_profile = child

        def check_test_dispatch():
            if self.runner.cancel_requested:
                raise ValueError("cancel_requested")

        previous_dispatch_check = self.runner.io.before_test_send
        self.runner.io.before_test_send = check_test_dispatch
        try:
            result = await child.run()
        finally:
            self.active_profile = None
            self.runner.io.before_test_send = previous_dispatch_check
        stages = result["stages"]
        evidence = {
            **row["evidence"],
            "source_token": result.get("source_token"),
            "stages": [{"name": s["name"], "state": s["state"]} for s in stages],
            "child_job_ids": [c["job_id"] for c in result["children"] if c.get("job_id")],
        }
        if problem := next(
            (s for s in stages if s.get("required") and s["state"] != "passed"), None
        ):
            evidence["reason"] = str(problem["evidence"].get("reason") or problem["name"])[:256]
        test = next((s["evidence"] for s in stages if s["name"] == "tests"), {})
        if test:
            summary = test.get("summary") or {}
            evidence.update(
                mode=profile.test_mode,
                status=test.get("status"),
                summary={
                    key: summary[key]
                    for key in ("total", "passed", "failed", "skipped", "inconclusive")
                    if type(summary.get(key)) is int
                },
            )
            if row["kind"] == "test":
                row["job_id"] = test.get("job_id")
                case = await self.observed_case(row, evidence)
                if case is None:
                    evidence["test_result_gap"] = "exact_test_case_unavailable"
                else:
                    evidence["test_result"] = case
        row.update(state=result["state"], evidence=evidence)
        if any(c.get("dispatch") == "acknowledged" for c in result["children"]):
            self.record_execution(row, evidence.get("status") or result["state"])

    async def observed_case(self, row, evidence):
        if not row["job_id"] or evidence["summary"].get("total") != 1:
            return None
        try:
            response = await self.runner.io.native(
                "get_test_job",
                {"job_id": row["job_id"], "includeDetails": True, "page_size": 1},
                self.runner.until("final"),
            )
        except (OSError, RuntimeError, ValueError):
            return None
        data = response.get("data")
        if (
            response.get("success") is not True
            or not isinstance(data, dict)
            or data.get("job_id") != row["job_id"]
            or data.get("status") != evidence.get("status")
            or not test_settled(data)
        ):
            return None
        page = data.get("result_page")
        if (
            not isinstance(page, dict)
            or type(page.get("total_count")) is not int
            or page["total_count"] != 1
            or page.get("has_more") is not False
            or page.get("next_cursor")
        ):
            return None
        cases = page.get("items")
        if not isinstance(cases, list) or len(cases) != 1 or not isinstance(cases[0], dict):
            return None
        case = cases[0]
        state = case.get("state")
        if (
            case.get("fullName") != row["id"]
            or state not in ("Passed", "Failed", "Skipped", "Inconclusive")
            or evidence["summary"].get(state.lower()) != 1
        ):
            return None
        return {"job_id": row["job_id"], "full_name": case["fullName"], "state": state}

    def record_execution(self, row, status):
        result = {
            "kind": row["kind"],
            "id": row["id"],
            "job_id": row["job_id"],
            "status": status,
            "definition_hash": row["evidence"]["definition_hash"],
        }
        if row["kind"] == "profile":
            result["child_job_ids"] = row["evidence"]["child_job_ids"]
        self.runner.report["executed_checks"].append(result)

    async def source_token(self, deadline):
        value = await self.runner.io.freshness("never", deadline)
        if value.get("status") != "observed_current" or not value.get("source_token"):
            raise ValueError("editor_source_freshness_unavailable")
        return value["source_token"]

    async def run_scenario(self, row):
        io = self.runner.io
        deadline = min(self.runner.deadline, monotonic() + 120)
        token = await self.source_token(deadline)
        # Resolve the saved project-owned definition before taking a dispatch slot.
        saved = await io.native(
            "manage_play_scenario", {"action": "get", "name": row["id"]}, deadline
        )
        definition = saved.get("data")
        if (
            saved.get("success") is not True
            or not isinstance(definition, dict)
            or definition.get("name") != row["id"]
        ):
            raise ValueError("mapped_scenario_unavailable")
        child = {
            "kind": "editor_scenario",
            "check_kind": "scenario",
            "check_id": row["id"],
            "job_id": uuid4().hex,
            "name": row["id"],
            "settled": False,
            "dispatch": "unknown",
        }
        revision = self.runner.prepared.candidate.source_revision
        params = {
            "action": "run",
            "name": row["id"],
            "job_id": child["job_id"],
            "source_revision": revision,
            "repeat_count": 1,
            "timeout_seconds": max(1, int(deadline - monotonic())),
        }

        def dispatch():
            if self.runner.cancel_requested:
                raise VerificationCanceled()
            self.runner.report["children"].append(child)
            row.update(
                job_id=child["job_id"],
                evidence={
                    "target": "editor",
                    "source_token": token,
                    "candidate_source_revision": revision,
                },
            )
            self.runner.publish()

        start = await io.native("manage_play_scenario", params, deadline, before_send=dispatch)
        if start.get("success") is not True:
            # Other failures may be lost replies. A status read can later prove native settlement.
            refusal = start.get("data")
            if start.get("success") is False and (
                isinstance(start, VerifiedQueueRefusal)
                or isinstance(refusal, dict)
                and refusal.get("scenario_job_created") is False
                and refusal.get("requested_job_id") == child["job_id"]
            ):
                child.update(dispatch="refused", settled=True)
            raise ValueError(str(start.get("error") or "mapped_scenario_start_unconfirmed")[:256])
        self.scenario_identity(start.get("data"), child)
        row["definition_hash"] = row["evidence"]["definition_hash"] = child["definition_hash"]
        child["dispatch"] = "acknowledged"
        while monotonic() < deadline:
            try:
                result = await self.observe_scenario(
                    child, deadline, cancel=self.runner.cancel_requested
                )
            except ConnectionError:
                result = None
            if result is not None:
                status = result["status"]
                row["evidence"].update(
                    status=status,
                    runner_resources_released=True,
                    definition_hash=(result.get("reproduction") or {}).get("definition_hash"),
                )
                self.record_execution(row, status)
                row["state"] = SCENARIO_STATES[status]
                if result.get("report_error") and row["state"] == "passed":
                    row.update(state="incomplete")
                    row["evidence"]["reason"] = "scenario_report_unavailable"
                if await self.source_token(deadline) != token:
                    row.update(state="stale")
                    row["evidence"]["reason"] = "editor_source_changed"
                return
            await asyncio.sleep(min(0.1, max(0, deadline - monotonic())))
        raise TimeoutError("mapped_scenario_deadline")

    def scenario_identity(self, data, child):
        if not isinstance(data, dict):
            raise ValueError("child_job_identity_changed")
        definition = data.get("scenario")
        reproduction = data.get("reproduction")
        if (
            data.get("job_id") != child["job_id"]
            or not isinstance(definition, dict)
            or definition.get("name") != child["name"]
            or data.get("execution_environment") != "editor"
            or not isinstance(reproduction, dict)
            or reproduction.get("source_revision") != self.runner.prepared.candidate.source_revision
        ):
            raise ValueError("child_job_identity_changed")
        digest = reproduction.get("definition_hash")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or digest != child.get("definition_hash", digest)
        ):
            raise ValueError("child_scenario_definition_changed")
        child["definition_hash"] = digest

    async def observe_scenario(self, child, deadline, *, cancel):
        params = {"action": "status", "job_id": child["job_id"]}
        response = await self.runner.io.native("manage_play_scenario", params, deadline)
        if response.get("success") is not True:
            return None
        data = response.get("data")
        self.scenario_identity(data, child)
        if (
            isinstance(data.get("status"), str)
            and data["status"] in SCENARIO_STATES
            and data.get("phase") == "finished"
            and data.get("runner_resources_released") is True
        ):
            child.update(settled=True, status=data["status"])
            self.runner.publish()
            return data
        if cancel and not child.get("cancel_sent"):
            child["cancel_sent"] = True
            await self.runner.io.native(
                "manage_play_scenario", {**params, "action": "cancel"}, deadline
            )
        return None

    def finish(self):
        runner = self.runner
        for row in self.rows:
            if row["state"] in {"pending", "running"}:
                state = "canceled" if runner.cancel_requested else "incomplete"
                row.update(
                    state=state, evidence={**row["evidence"], "reason": "check_not_completed"}
                )
        runner.report["additional_checks_outstanding"] = [
            {"kind": r["kind"], "id": r["id"]}
            for r in self.rows
            if r["state"] in {"incomplete", "stale", "canceled"}
        ]
        states = [{"state": row["state"]} for row in self.rows]
        if self.selection_gaps:
            states.append({"state": "incomplete"})
        runner.stage(
            "selected_checks",
            final_verdict(states) if states else "passed",
            {"count": len(self.rows), "selection_gaps": self.selection_gaps},
        )
