"""Authenticated preparations and detached, bounded verification ownership."""

import asyncio
import contextvars
from contextlib import nullcontext
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from core.config import config
from core import verification_timing
from core.server_build import RUNNING_SERVER
from models.script_quality import ScriptSelection
from models.verification import VerificationProfile, parse_verification_options
from services.credential_lease import bind_credential_lease, require_credential_lease
from services.operation_permissions import authorize_remote_operation
from services.registry.operation_context import current_operation
from services.registry.operation_target import capture_target
from services.registry.operation_scheduler import OperationScope
from services.script_quality.service import ScriptQualityService
from services.source_freshness import ensure_source_freshness
from services.tools import get_unity_instance_from_context
from services.verification.profiles import (
    builtins,
    profile_operations,
    profile_permissions,
    resolve_profile,
)
from services.verification.reports import ReportStore, test_settled
from services.verification.runner import VerificationRunner
from services.verification.player_candidate import prepare_candidate
from services.verification.player_runner import PlayerVerificationRunner
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


@dataclass
class BoundContext:
    fastmcp: object
    instance: str
    user_id: str | None

    async def get_state(self, key, default=None):
        return {"unity_instance": self.instance, "user_id": self.user_id}.get(key, default)


@dataclass(frozen=True)
class VerificationTarget:
    scope: OperationScope
    resume_generation: str | None


class VerificationIO:
    def __init__(self, ctx, target):
        self.ctx, self.owner = ctx, target.scope.key
        self.resume = target.resume_generation
        self.generation = target.scope.generation
        self.on_refresh = lambda _parameters: None
        self.impact_requested = False
        self.before_test_send = lambda: None

    async def target(self, deadline=None):
        target = await capture_target(
            self.ctx.instance, user_id=self.ctx.user_id, deadline=deadline
        )
        if target is None or not target.is_current():
            raise ConnectionError("verification_target_unavailable")
        if target.scope.key != self.owner or (
            target.resume_generation != self.resume
            if self.resume
            else target.scope.generation != self.generation
        ):
            raise ValueError("verification_editor_process_changed")
        return target

    async def check(self):
        await self.target()
        if config.http_remote_hosted:
            await require_credential_lease(self.ctx.user_id)

    async def authorize(self, name, params, deadline=None):
        target = await self.target(deadline)
        await authorize_remote_operation(name, params, ctx=self.ctx)
        if target.authorize_public is not None:
            check = await target.authorize_public(name, params)
            check()
        names = ("read_console", "tail_console") if name == "tail_console" else (name,)
        # Project information is a resource. Tool inventories omit resource commands.
        if (
            name != "get_project_info"
            and target.public_available is not None
            and not await target.public_available(names, self.ctx.fastmcp)
        ):
            raise ValueError("verification_child_tool_unavailable")
        return target

    async def authorize_profile(self, profile):
        for name, params in profile_operations(profile, include_impact=self.impact_requested):
            await self.authorize(name, params)

    async def native(self, command, params, deadline, *, before_send=None):
        try:
            if monotonic() >= deadline:
                raise TimeoutError()
            target = await asyncio.wait_for(
                self.authorize(command, params, deadline), max(0, deadline - monotonic())
            )
            if not target.is_current():
                raise ConnectionError("verification_target_unavailable")
            if command == "run_tests":
                self.before_test_send()
        except (OSError, ValueError, RuntimeError) as error:
            if command != "run_tests":
                raise
            return {
                "success": False,
                "error": str(error)[:256] or "test_admission_deadline",
                "data": {"test_job_created": False},
            }
        if before_send is not None:
            before_send()
        response = await asyncio.wait_for(
            send_with_unity_instance(
                async_send_command_with_retry,
                self.ctx.instance,
                command,
                params,
                retry_on_reload=False,
                _deadline=deadline,
                **(
                    {"_preserve_queue_refusal": True}
                    if command == "run_tests" or before_send is not None
                    else {}
                ),
            ),
            max(0, deadline - monotonic()),
        )
        # Mutating acknowledgments are never reissued even when their connection retires.
        value = (
            response.model_dump(exclude_none=True) if hasattr(response, "model_dump") else response
        )
        if not isinstance(value, dict):
            raise ValueError("verification_invalid_native_response")
        if command not in {"refresh_unity", "run_tests"} and not target.is_current():
            raise ConnectionError("verification_observation_retired")
        return value

    async def freshness(self, policy, deadline):
        children = []

        async def before(name, params):
            await self.authorize(name, params, deadline)
            if name == "refresh_unity":
                child = self.on_refresh(params)
                if child is not None:
                    children.append(child)
                    return lambda: child.update(settled=True, dispatch="refused")

        evidence = await ensure_source_freshness(
            self.ctx,
            instance=self.ctx.instance,
            deadline=deadline,
            refresh_policy=policy,
            before_dispatch=before,
        )
        await asyncio.wait_for(self.check(), max(0, deadline - monotonic()))
        if evidence.status in {"observed_current", "compile_failed"}:
            for child in children:
                child["settled"] = True
        return evidence.model_dump(by_alias=True)

    async def quality(self, action, options, deadline, *, on_dispatch=None):
        target = await asyncio.wait_for(
            self.authorize(
                "manage_script_quality", {"action": action, "options": options}, deadline
            ),
            max(0, deadline - monotonic()),
        )
        service = getattr(self.ctx.fastmcp, "_script_quality_service", None)
        if service is None:
            service = ScriptQualityService()
            self.ctx.fastmcp._script_quality_service = service
        response = await asyncio.wait_for(
            service.execute(
                self.ctx, action, options, on_dispatch=on_dispatch, expected_target=target
            ),
            max(0, deadline - monotonic()),
        )
        if response.get("success") is False:
            job = service.jobs.get(response.get("job_id"))
            response = {
                **response,
                "settled": job is not None and job.owner == self.owner and job.terminal,
            }
        return response


class VerificationService:
    def __init__(self):
        self.preparations = {}
        self.store = ReportStore()
        self.runners = {}
        self.tasks = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def error(code, message=None):
        return {"success": False, "code": code, "error": message or code}

    def trim(self):
        self.store._expire()
        for identity, preparation in list(self.preparations.items()):
            job = preparation.get("baseline_job")
            if preparation["expires"] <= monotonic() and (
                preparation["ready"]
                or preparation.get("failed")
                or job is not None
                and job.terminal
            ):
                del self.preparations[identity]
        for identity, runner in list(self.runners.items()):
            if identity not in self.store.records and (
                identity not in self.tasks or self.tasks[identity].done()
            ):
                self.runners.pop(identity, None)
                self.tasks.pop(identity, None)

    async def observe_baseline(self, preparation, io, deadline):
        async with preparation["baseline_lock"]:
            if preparation["ready"] or preparation.get("failed"):
                return
            identity = preparation["baseline_id"]
            baseline = await io.quality("status", {"job_id": identity}, deadline)
            data = baseline.get("data") or {}
            if baseline.get("success") is True and data.get("job_id") == identity:
                if data.get("baseline_id") == identity:
                    preparation["ready"] = True
                elif data.get("terminal") is True and data.get("state") in {
                    "completed",
                    "failed",
                    "canceled",
                }:
                    preparation.update(failed=True, state="incomplete")
            elif baseline.get("code") == "analysis_expired_or_evicted":
                preparation.update(failed=True, state="incomplete")

    async def reconcile_expired_baselines(self, io, deadline):
        for preparation in list(self.preparations.values()):
            if (
                preparation["expires"] <= monotonic() < deadline
                and preparation["owner"] == io.owner
                and preparation["resume"] == io.resume
                and (io.resume or preparation["generation"] == io.generation)
                and preparation["baseline_id"]
                and not preparation["ready"]
                and not preparation.get("failed")
                and not preparation["baseline_lock"].locked()
            ):
                try:
                    await self.observe_baseline(preparation, io, deadline)
                except (OSError, RuntimeError, ValueError):
                    # An unavailable observation cannot release an outstanding capture.
                    continue
        self.trim()

    async def execute(self, ctx, action, options):
        if action == "run" and config.phase_diagnostics and verification_timing.current() is None:
            # A direct run includes its inline preparation. A separately prepared
            # run starts here and does not inherit unrelated preparation traffic.
            entry = (
                "prepared"
                if isinstance(options, dict) and "preparation_id" in options
                else "direct"
            )
            with verification_timing.VerificationTiming(entry).bind():
                return await self._execute(ctx, action, options)
        return await self._execute(ctx, action, options)

    async def _execute(self, ctx, action, options):
        try:
            parsed = parse_verification_options(action, options).model_dump(exclude_none=True)
        except ValueError as error:
            return self.error("invalid_verification_request", str(error)[:2048])
        if action == "run" and "preparation_id" not in parsed:
            wait_seconds = parsed.pop("wait_seconds")
            prepared = await self.execute(ctx, "prepare", parsed)
            if prepared.get("success") is not True:
                return prepared
            return await self.execute(
                ctx,
                "run",
                {
                    "preparation_id": prepared["data"]["preparation_id"],
                    "wait_seconds": wait_seconds,
                },
            )
        self.trim()
        instance = await get_unity_instance_from_context(ctx)
        lease = await require_credential_lease() if config.http_remote_hosted else None
        user_id = lease.user_id if lease else None
        target = await capture_target(instance, user_id=user_id)
        if target is None or not target.is_current():
            return self.error("verification_target_unavailable")
        bound = BoundContext(ctx.fastmcp, instance, user_id)
        io = VerificationIO(bound, target)
        await io.authorize("manage_verification", {"action": action, "options": parsed})
        owner, deadline = target.scope.key, monotonic() + 30
        if action in {"observe_assembly", "observe_texture"}:
            return await io.native("manage_verification", {"action": action, **parsed}, deadline)
        if action in {"profiles", "set_profile", "prepare"}:
            native_action = "set_profile" if action == "set_profile" else "profiles"
            response = await io.native(
                "manage_verification",
                {"action": native_action, **(parsed if action == "set_profile" else {})},
                deadline,
            )
            if response.get("success") is not True:
                return response
            data = response.get("data") or {}
            if action == "set_profile":
                return response
            document = data.get("document")
            # Validate every stored profile before making the defaults/overrides executable.
            profiles = {
                key: resolve_profile(key, document).model_dump()
                for key in set(builtins()) | set((document or {}).get("profiles", {}))
            }
            if action == "profiles":
                return {
                    "success": True,
                    "data": {
                        "profiles": profiles,
                        "byte_hash": data.get("byte_hash"),
                        "schema_version": 1,
                    },
                }
            if "candidate_id" in parsed and config.http_remote_hosted:
                return self.error("verification_local_operator_required")
            project = await io.native("get_project_info", {}, deadline)
            info = (project.get("data") or {}) if project.get("success") is True else {}
            installation = {
                "project_scope": owner[1],
                "editor_generation": target.resume_generation or target.scope.generation,
                "unity_version": info.get("unityVersion"),
                "package": info.get("mcpPackage"),
                "server": RUNNING_SERVER.command_metadata(),
            }
            await self.reconcile_expired_baselines(io, deadline)
            if self.preparation_capacity_reached(owner):
                return self.error("verification_preparation_capacity")
            candidate = None
            if "candidate_id" in parsed:
                try:
                    candidate = await asyncio.to_thread(prepare_candidate, info, parsed)
                except (OSError, ValueError) as error:
                    return self.error("candidate_preparation_unavailable", str(error)[:256])
                if self.preparation_capacity_reached(owner):
                    return self.error("verification_preparation_capacity")
                profile_id = "standalone-player"
                profile = VerificationProfile(
                    refresh_policy="never", analysis_required=False, impact="off"
                )
                selection = parsed.copy()
            else:
                profile_id = parsed["profile_id"]
                profile = resolve_profile(profile_id, document)
                selection = {
                    key: parsed[key] for key in ("paths", "change_set_id") if key in parsed
                }
            identity = uuid4().hex
            preparation = self.preparations[identity] = {
                "owner": owner,
                "instance": instance,
                "resume": target.resume_generation,
                "generation": target.scope.generation,
                "preparation_id": identity,
                "selection": selection,
                "installation": installation,
                "player_candidate": candidate,
                "broader_profile": (
                    profiles.get(candidate.candidate.broader_check)
                    if candidate and parsed["execute_selected_checks"]
                    else None
                ),
                "profile": profile,
                "profile_id": profile_id,
                "profile_hash": data.get("byte_hash")
                if not candidate
                or parsed.get("execute_selected_checks")
                and candidate.candidate.broader_check in profiles
                else None,
                "baseline_id": parsed.get("baseline_id"),
                "baseline_lock": asyncio.Lock(),
                "ready": not parsed.get("capture_baseline"),
                "state": "pending",
                "run_id": None,
                "expires": monotonic() + 1800,
            }
            if parsed.get("capture_baseline"):

                def baseline_dispatched(job_id):
                    preparation["baseline_id"] = job_id
                    # Keep the local terminal witness even after the job map evicts it.
                    preparation["baseline_job"] = ctx.fastmcp._script_quality_service.jobs[job_id]

                try:
                    await io.authorize("manage_script_quality", {"action": "capture_baseline"})
                    capture = await io.quality(
                        "capture_baseline",
                        {**selection, "timeout_seconds": profile.deadlines.analysis},
                        deadline,
                        on_dispatch=baseline_dispatched,
                    )
                    if capture.get("success") is False and capture.get("settled"):
                        self.preparations.pop(identity, None)
                        return capture
                    if not preparation["baseline_id"]:
                        return capture
                except (TimeoutError, ConnectionError):
                    if not preparation["baseline_id"]:
                        raise
                finally:
                    if not preparation["baseline_id"]:
                        self.preparations.pop(identity, None)
            return {"success": True, "data": self.preparation_view(preparation)}
        if "preparation_id" in parsed:
            preparation = self.preparations.get(parsed["preparation_id"])
            if preparation is None or preparation["owner"] != owner:
                return self.error("verification_not_found")
            if preparation["resume"] != target.resume_generation or (
                not preparation["resume"] and preparation["generation"] != target.scope.generation
            ):
                return self.error("verification_editor_process_changed")
            if not preparation["ready"] and not preparation.get("failed"):
                await self.observe_baseline(preparation, io, deadline)
            if action == "status":
                return {"success": True, "data": self.preparation_view(preparation)}
            if not preparation["ready"]:
                return self.error("verification_preparation_not_ready")
            async with self.lock:
                run_id = preparation["run_id"]
                if run_id is None:
                    io.impact_requested = bool(preparation["baseline_id"])
                    run_id = await self.start_runner(
                        io,
                        preparation["profile"],
                        preparation["selection"],
                        lease,
                        baseline_id=preparation["baseline_id"],
                        player_candidate=preparation["player_candidate"],
                        broader_profile=preparation["broader_profile"],
                        metadata={
                            "preparation_id": preparation["preparation_id"],
                            "profile_id": preparation["profile_id"],
                            "profile_hash": preparation["profile_hash"],
                            "installation": preparation["installation"],
                        },
                    )
                    preparation["run_id"] = run_id
            if parsed.get("wait_seconds", 0) > 0:
                await asyncio.wait({self.tasks[run_id]}, timeout=parsed["wait_seconds"])
                await io.check()
            return self.read(run_id, owner)
        identity = parsed["run_id"]
        if self.store.read(identity, owner) is None:
            return self.error("verification_not_found")
        runner = self.runners.get(identity)
        if action == "cancel" and runner is not None:
            runner.cancel_requested = True
            if isinstance(runner, PlayerVerificationRunner) and runner.selected is not None:
                runner.selected.cancel()
            report = self.store.read(identity, owner)
            report["cancel_requested"] = True
            self.store.update(identity, owner, report)
        # Completed orchestration never resumes stages. Status may observe unsettled children only.
        if (
            runner is not None
            and self.tasks[identity].done()
            and not runner.report["native_settled"]
        ):
            await self.observe_settlement(runner, deadline, cancel=action == "cancel")
            self.store.finish(
                identity, owner, runner.report, settled=runner.report["native_settled"]
            )
        return self.read(identity, owner)

    def preparation_capacity_reached(self, owner):
        return (
            len(self.preparations) >= 64
            or sum(p["owner"] == owner for p in self.preparations.values()) >= 16
        )

    async def start_runner(
        self,
        io,
        profile,
        selection,
        lease,
        *,
        baseline_id=None,
        expected_sources=None,
        metadata=None,
        player_candidate=None,
        broader_profile=None,
    ):
        if player_candidate is None:
            await io.authorize_profile(profile)
            runner = VerificationRunner(
                io, profile, selection, baseline_id=baseline_id, expected_sources=expected_sources
            )
            permissions = sorted(profile_permissions(profile))
        else:
            if config.http_remote_hosted:
                raise ValueError("verification_local_operator_required")
            await io.authorize("manage_play_scenario", {"action": "run"})
            runner = PlayerVerificationRunner(
                io, profile, selection, player_candidate, broader_profile
            )
            permissions = sorted(
                {"read", "run"} | (runner.selected.permissions if runner.selected else set())
            )
        owner = io.owner
        run_id = self.store.create(owner, runner.report)
        runner.report.update(
            run_id=run_id,
            project_scope=owner[1],
            required_permissions=permissions,
            **(metadata or {}),
        )
        runner.emit = lambda report: self.store.update(run_id, owner, report)
        runner.publish()
        self.runners[run_id] = runner
        timing = verification_timing.current()
        if timing is None and config.phase_diagnostics:
            timing = verification_timing.VerificationTiming()
        scheduled = None
        if timing is not None:
            operation = current_operation.get()
            if operation is not None and operation.queue_ms is not None:
                timing.record("scheduler_queue", operation.queue_ms)
            scheduled = monotonic()
        self.tasks[run_id] = asyncio.create_task(
            self._run(run_id, owner, runner, lease, timing, scheduled),
            context=contextvars.Context(),
        )
        return run_id

    async def start_edit(self, ctx, instance, *, paths, expected_sources, original_target=None):
        """Start the fixed source-check profile after a confirmed write, using existing job owners."""
        # Validation also bounds deletion-only selections, which must never reach an analyzer.
        ScriptSelection(paths=list(expected_sources))
        if paths:
            ScriptSelection(paths=paths)
        lease = await require_credential_lease() if config.http_remote_hosted else None
        user_id = lease.user_id if lease else None
        target = await capture_target(instance, user_id=user_id)
        if target is None or not target.is_current():
            return self.error("verification_target_unavailable")
        bound = BoundContext(ctx.fastmcp, instance, user_id)
        io = VerificationIO(bound, original_target or target)
        try:
            await io.target()
        except (ConnectionError, ValueError) as error:
            return self.error(str(error))
        # Tool defaults never inherit project overrides that could add tests or Play Mode.
        profile = VerificationProfile(analysis_required=bool(paths), impact="off")
        self.trim()
        async with self.lock:
            identity = await self.start_runner(
                io,
                profile,
                {"paths": paths},
                lease,
                expected_sources=expected_sources,
                metadata={"profile_id": "post-edit", "profile_source": "tool_default"},
            )
        return self.read(identity, target.scope.key)

    async def _run(self, identity, owner, runner, lease, timing=None, scheduled=None):
        with bind_credential_lease(lease), timing.bind() if timing else nullcontext():
            if timing is not None:
                timing.record("task_dispatch", (monotonic() - scheduled) * 1000)
            try:
                with verification_timing.interval("orchestration"):
                    result = await runner.run()
                if timing is not None:
                    result["timing"] = timing.finish()
                self.store.finish(identity, owner, result, settled=result["native_settled"])
            except BaseException:
                # Task cancellation does not release native ownership or authorize replay.
                runner.report.update(state="incomplete", interruption="orchestrator_interrupted")
                if timing is not None:
                    runner.report["timing"] = timing.finish()
                runner.publish()
                self.store.finish(
                    identity, owner, runner.report, settled=runner.report["native_settled"]
                )

    async def observe_settlement(self, runner, deadline, *, cancel):
        for child in runner.report["children"]:
            if child.get("settled") or not child.get("job_id"):
                continue
            try:
                if child["kind"] == "analysis":
                    if cancel:
                        await runner.io.quality("cancel", {"job_id": child["job_id"]}, deadline)
                    response = await runner.io.quality(
                        "status", {"job_id": child["job_id"]}, deadline
                    )
                    data = response.get("data") or {}
                    child["settled"] = (
                        response.get("success") is True
                        and data.get("job_id") == child["job_id"]
                        and data.get("terminal") is True
                        and data.get("state") in {"completed", "failed", "canceled"}
                    )
                elif child["kind"] == "tests":
                    if cancel:
                        await runner.io.native(
                            "cancel_test_job", {"job_id": child["job_id"]}, deadline
                        )
                    response = await runner.io.native(
                        "get_test_job", {"job_id": child["job_id"]}, deadline
                    )
                    data = response.get("data") or {}
                    child["settled"] = (
                        response.get("success") is True
                        and data.get("job_id") == child["job_id"]
                        and test_settled(data)
                    )
                elif child["kind"] == "standalone_player":
                    child["settled"] = await asyncio.to_thread(
                        runner.player_settled, child["job_id"]
                    )
                elif child["kind"] == "editor_scenario":
                    await runner.selected.observe_scenario(child, deadline, cancel=cancel)
                elif child["kind"] == "compile":
                    from services.tools.get_compile_job import CompileCoordination

                    response = await runner.io.native(
                        "get_compile_job", {"compile_id": child["job_id"]}, deadline
                    )
                    data = response.get("data") or {}
                    witness = CompileCoordination.model_validate(
                        response.get("native_coordination")
                    )
                    child["settled"] = (
                        response.get("success") is True
                        and data.get("compile_id") == child["job_id"]
                        and data.get("state") == "finished"
                        and witness.phase == "finished"
                    )
            except (TimeoutError, ConnectionError, PermissionError, ValueError):
                pass
        runner.publish()

    def read(self, identity, owner):
        report = self.store.read(identity, owner)
        return (
            {"success": True, "data": report}
            if report is not None
            else self.error("verification_not_found")
        )

    @staticmethod
    def preparation_view(preparation):
        view = {
            key: preparation[key]
            for key in (
                "preparation_id",
                "selection",
                "installation",
                "profile_id",
                "profile_hash",
                "baseline_id",
                "ready",
                "state",
                "run_id",
            )
        }
        if preparation["player_candidate"] is None:
            view["profile"] = preparation["profile"].model_dump()
        else:
            view["comparison"] = preparation["player_candidate"].comparison()
            if preparation.get("broader_profile") is not None:
                view["broader_profile"] = preparation["broader_profile"]
        return view
