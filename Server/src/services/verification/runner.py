"""Sequential composition; each native child owns its own admission and deadline."""

import asyncio
from time import monotonic, time

from services.script_quality.pages import encoded_size
from services.verification.reports import console_delta, final_verdict, test_settled, test_verdict


class VerificationCanceled(Exception):
    pass


def source_summary(value):
    keys = {
        "schema",
        "status",
        "source_token",
        "source_digest",
        "context_digest",
        "project_id",
        "session_id",
        "compile_id",
        "compile_generation",
        "process_id",
        "observed_at_unix_ms",
        "reason",
        "basis",
        "coverage",
    }
    return {key: item for key, item in value.items() if key in keys}


class VerificationRunner:
    def __init__(self, io, profile, selection, *, baseline_id=None, emit=None):
        self.io, self.profile, self.selection, self.baseline_id = (
            io,
            profile,
            selection,
            baseline_id,
        )
        self.emit = emit or (lambda _: None)
        self.cancel_requested = False
        self.report = {
            "schema": "verification_report@1",
            "state": "running",
            "profile": profile.model_dump(),
            "selection": selection,
            "baseline_id": baseline_id,
            "stages": [],
            "children": [],
            "started_at_unix_ms": int(time() * 1000),
            "native_settled": True,
            "limitations": [
                "Source/compile/load evidence is sampled, not atomic compiler input provenance.",
                "A console without exact loss evidence cannot establish a clean run.",
            ],
        }
        self.deadline = monotonic() + sum(profile.deadlines.model_dump().values())
        self.io.on_refresh = self.refresh_dispatched
        self.io.impact_requested = bool(baseline_id)

    def refresh_dispatched(self, parameters):
        if self.cancel_requested:
            raise VerificationCanceled()
        child = {
            "kind": "compile" if parameters.get("compile_id") else "import",
            "job_id": parameters.get("compile_id"),
            "settled": False,
            "dispatch": "unknown",
        }
        self.report["children"].append(child)
        self.publish()
        return child

    async def check(self):
        await asyncio.wait_for(self.io.check(), max(0, self.deadline - monotonic()))
        if self.cancel_requested:
            raise VerificationCanceled()
        if monotonic() >= self.deadline:
            raise TimeoutError()

    def stage(self, name, state, evidence=None, *, required=True, started=None):
        evidence = evidence or {}
        if encoded_size(evidence) > 48 * 1024:
            evidence = {"reason": "stage_evidence_limit", "detail_omitted": True}
            state = "incomplete"
        self.report["stages"].append(
            {
                "name": name,
                "state": state,
                "required": required,
                "evidence": evidence,
                "started_at_unix_ms": started or int(time() * 1000),
                "finished_at_unix_ms": int(time() * 1000),
            }
        )
        self.publish()

    def publish(self):
        self.report["native_settled"] = all(
            child.get("settled") is True for child in self.report["children"]
        )
        self.emit(self.report)

    def until(self, stage):
        return min(self.deadline, monotonic() + getattr(self.profile.deadlines, stage))

    async def observe(self, name, policy, deadline, expected=None):
        await self.check()
        started = int(time() * 1000)
        value = await self.io.freshness(policy, deadline)
        for item in value.get("refresh_dispatches", []):
            self.report["children"].append(
                {
                    "kind": "compile" if item.get("compile_id") else "import",
                    "job_id": item.get("compile_id"),
                    "settled": item.get("settled") is True,
                    "dispatch": "unknown",
                }
            )
        current = value.get("status") == "observed_current"
        state = (
            "passed"
            if current
            else "failed"
            if value.get("status") == "compile_failed"
            else "incomplete"
        )
        if expected and value.get("source_token") and value.get("source_token") != expected:
            state = "stale"
        self.stage(name, state, source_summary(value), started=started)
        return value, state

    async def analysis(self, expected):
        await self.check()
        deadline = self.until("analysis")
        started = int(time() * 1000)
        child = {"kind": "analysis", "job_id": None, "settled": False, "dispatch": "unknown"}
        self.report["children"].append(child)
        self.publish()
        options = {**self.selection, "timeout_seconds": self.profile.deadlines.analysis}
        if self.baseline_id:
            options["baseline_id"] = self.baseline_id
        response = await self.io.quality("analyze", options, deadline)
        child["job_id"] = (response.get("data") or {}).get("job_id") or response.get("job_id")
        if response.get("success") is not True:
            child["settled"] = response.get("settled") is True
            self.stage(
                "analysis",
                "incomplete",
                {
                    "reason": response.get("code") or response.get("error"),
                    "job_id": child["job_id"],
                },
                started=started,
            )
            return None
        if not child["job_id"]:
            raise ValueError("analysis_job_identity_missing")
        child["dispatch"] = "acknowledged"
        self.publish()
        response = await self.poll(child, deadline)
        if response.get("state") != "completed" or not isinstance(response.get("result"), dict):
            self.stage(
                "analysis",
                "incomplete",
                {
                    "job_id": child["job_id"],
                    "reason": response.get("reason", "analysis_incomplete"),
                },
                started=started,
            )
            return None
        result = response["result"]
        state = "passed" if result.get("complete") is True else "incomplete"
        if result.get("stale") or expected and result.get("source_token") != expected:
            state = "stale"
        if self.profile.diagnostic_scope == "introduced" and not result.get(
            "classification_complete"
        ):
            state = "incomplete" if state != "stale" else state
        evidence = {
            key: result[key]
            for key in (
                "source_token",
                "context_digest",
                "environment_signature",
                "providers",
                "coverage_gaps",
                "classification_reasons",
                "counts",
                "classification_complete",
            )
            if key in result
        }
        evidence["analysis_id"] = child["job_id"]
        severity_counts = {"error": 0, "warning": 0, "info": 0}
        seen = set()
        for _ in range(128):
            for row in result.get("rows", []):
                diagnostic = row.get("diagnostic", {})
                selected = (
                    row.get("scope") == "selected"
                    and row.get("classification") != "resolved"
                    and row.get("observation") != "baseline"
                    and (
                        self.profile.diagnostic_scope == "all"
                        or row.get("classification") == "introduced"
                    )
                )
                # Compiler/context failures matter even when only introduced selected hints were requested.
                selected |= row.get("scope") == "context" and diagnostic.get("severity") == "error"
                if (
                    selected
                    and not diagnostic.get("suppressed")
                    and diagnostic.get("severity") in severity_counts
                ):
                    severity_counts[diagnostic["severity"]] += 1
            cursor = result.get("next_cursor")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("analysis_cursor_repeated")
            seen.add(cursor)
            await self.check()
            page = await self.io.quality(
                "status", {"job_id": child["job_id"], "cursor": cursor, "page_size": 500}, deadline
            )
            if page.get("success") is not True:
                raise ValueError("analysis_page_unavailable")
            result = page["data"]["result"]
        else:
            state = "incomplete"
            evidence["reason"] = "analysis_page_limit"
        if any(severity_counts[severity] for severity in self.profile.fail_on) and state != "stale":
            state = "failed"
        evidence["active_severity_counts"] = severity_counts
        self.stage("analysis", state, evidence, started=started)
        return child["job_id"]

    async def poll(self, child, deadline):
        while monotonic() < deadline:
            await asyncio.wait_for(self.io.check(), max(0, deadline - monotonic()))
            if self.cancel_requested and not child.get("cancel_sent"):
                child["cancel_sent"] = True
                if child["kind"] == "analysis":
                    await self.io.quality("cancel", {"job_id": child["job_id"]}, deadline)
                else:
                    await self.io.native("cancel_test_job", {"job_id": child["job_id"]}, deadline)
            try:
                if child["kind"] == "analysis":
                    response = await self.io.quality(
                        "status", {"job_id": child["job_id"], "page_size": 500}, deadline
                    )
                else:
                    response = await self.io.native(
                        "get_test_job", {"job_id": child["job_id"]}, deadline
                    )
            except ConnectionError:
                await asyncio.sleep(min(0.1, max(0, deadline - monotonic())))
                continue
            data = response.get("data") or {}
            if response.get("success") is True:
                if data.get("job_id") != child["job_id"]:
                    raise ValueError("child_job_identity_changed")
                settled = (
                    (
                        data.get("terminal") is True
                        and data.get("state") in {"completed", "failed", "canceled"}
                    )
                    if child["kind"] == "analysis"
                    else test_settled(data)
                )
                if settled:
                    child["settled"] = True
                    self.publish()
                    return data
            await asyncio.sleep(min(0.1, max(0, deadline - monotonic())))
        raise TimeoutError()

    async def impact(self, analysis_id):
        if (
            self.profile.impact == "off"
            or self.profile.impact == "if_baseline"
            and not self.baseline_id
        ):
            self.stage(
                "impact", "passed", {"reason": "not_requested_without_baseline"}, required=False
            )
            return
        if not analysis_id:
            self.stage("impact", "incomplete", {"reason": "analysis_unavailable"})
            return
        deadline, started = self.until("impact"), int(time() * 1000)
        options = {"analysis_id": analysis_id, "page_size": 100}
        if self.baseline_id:
            options["baseline_id"] = self.baseline_id
        gaps, counts, revision, seen = (
            set(),
            {"confirmed_reference": 0, "potential_reference": 0, "unknown": 0},
            None,
            set(),
        )
        for _ in range(128):
            await self.check()
            response = await self.io.quality("impact", options, deadline)
            if response.get("success") is not True:
                gaps.add("impact_unavailable")
                break
            page = response["data"]
            gaps.update(page.get("coverage_gaps", []))
            if not page.get("comparison_complete"):
                gaps.add("change_comparison_unavailable")
            revision = page.get("graph_revision")
            for row in page.get("findings", []):
                counts[row.get("certainty", "unknown")] += 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if cursor in seen:
                gaps.add("impact_cursor_repeated")
                break
            seen.add(cursor)
            options["cursor"] = cursor
        else:
            gaps.add("impact_page_limit")
        self.stage(
            "impact",
            "incomplete" if gaps else "passed",
            {
                "analysis_id": analysis_id,
                "baseline_id": self.baseline_id,
                "reference_counts": counts,
                "graph_revision": revision,
                "coverage_gaps": sorted(gaps),
                "advisory": True,
            },
            started=started,
        )

    async def tests(self, source_token):
        _, admitted = await self.observe(
            "test_admission", "never", self.until("final"), source_token
        )
        if admitted != "passed":
            self.stage(
                "tests", admitted, {"reason": "source_admission_refused", "dispatched": False}
            )
            return
        await self.check()
        console_parameters = {
            "tail_cursor": "begin",
            "types": ["error"],
            "format": "json",
            "pageSize": 200,
            "includeStacktrace": False,
            "includeMcpLogs": False,
        }
        try:
            response = await self.io.native(
                "tail_console", console_parameters, self.until("console")
            )
            before = response.get("data") if response.get("success") is True else None
        except (ConnectionError, TimeoutError):
            before = None
        await self.check()
        deadline, started = self.until("tests"), int(time() * 1000)
        parameters = {
            "mode": self.profile.test_mode,
            "source_token": source_token,
            "require_source_freshness": True,
            "runTimeoutMs": max(1, int((deadline - monotonic()) * 1000)),
        }
        for field, native in (
            ("test_names", "testNames"),
            ("assembly_names", "assemblyNames"),
            ("category_names", "categoryNames"),
        ):
            if values := getattr(self.profile, field):
                parameters[native] = values
        child = {"kind": "tests", "job_id": None, "settled": False, "dispatch": "unknown"}
        self.report["children"].append(child)
        self.publish()
        response = await self.io.native("run_tests", parameters, deadline)
        data = response.get("data") or {}
        child["job_id"] = data.get("job_id")
        if response.get("success") is not True or not child["job_id"]:
            child["settled"] = response.get("settled") is True
            self.stage(
                "tests",
                "incomplete",
                {"reason": "test_start_not_confirmed", "job_id": child["job_id"]},
                started=started,
            )
            return
        child["dispatch"] = "acknowledged"
        self.publish()
        data = await self.poll(child, deadline)
        self.stage(
            "tests",
            test_verdict(data),
            {
                "job_id": child["job_id"],
                "summary": (data.get("result") or {}).get("summary"),
                "native_cleanup_pending": data.get("native_cleanup_pending"),
                "mode": self.profile.test_mode,
                "selectors": {
                    key: value
                    for key, value in parameters.items()
                    if key in {"testNames", "assemblyNames", "categoryNames"}
                },
                "selection_basis": "explicit_selectors"
                if any(key in parameters for key in ("testNames", "assemblyNames", "categoryNames"))
                else "complete_requested_mode",
            },
            started=started,
        )
        await self.check()
        after = None
        if before and before.get("next_cursor"):
            console_parameters["tail_cursor"] = before["next_cursor"]
            try:
                response = await self.io.native(
                    "tail_console", console_parameters, self.until("console")
                )
                after = response.get("data") if response.get("success") is True else None
            except (ConnectionError, TimeoutError):
                pass
        console = console_delta(before, after)
        self.stage("console", console["state"], console)

    async def run(self):
        try:
            await asyncio.wait_for(
                self.io.authorize_profile(self.profile), max(0, self.deadline - monotonic())
            )
            initial, admission = await self.observe(
                "freshness", self.profile.refresh_policy, self.until("freshness")
            )
            self.report["source_token"] = initial.get("source_token")
            analysis_id = (
                await self.analysis(initial.get("source_token"))
                if self.profile.analysis_required
                else None
            )
            await self.impact(analysis_id)
            if self.profile.test_mode:
                if admission == "passed":
                    await self.tests(initial["source_token"])
                else:
                    self.stage(
                        "tests",
                        admission,
                        {"reason": "source_admission_refused", "dispatched": False},
                    )
            await self.observe(
                "final_freshness", "never", self.until("final"), initial.get("source_token")
            )
        except VerificationCanceled:
            self.stage("cancellation", "canceled", {"reason": "cancel_requested"})
        except (TimeoutError, ConnectionError, PermissionError, RuntimeError, ValueError) as error:
            self.stage(
                "interruption",
                "incomplete",
                {
                    "reason": "deadline_or_observation_unavailable"
                    if isinstance(error, (TimeoutError, ConnectionError))
                    else str(error)[:256],
                    "automatic_replay": False,
                    "next_action": "inspect_existing_child_jobs",
                },
            )
        self.report["state"] = (
            "canceled" if self.cancel_requested else final_verdict(self.report["stages"])
        )
        self.report["finished_at_unix_ms"] = int(time() * 1000)
        self.publish()
        return self.report
