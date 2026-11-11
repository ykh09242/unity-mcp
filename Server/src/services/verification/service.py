"""Authenticated preparations and detached, bounded verification ownership."""

import asyncio
import contextvars
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from core.config import config
from models.verification import parse_verification_options
from services.credential_lease import bind_credential_lease, require_credential_lease
from services.operation_permissions import authorize_remote_operation
from services.registry.operation_target import capture_target
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
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


@dataclass
class BoundContext:
    fastmcp: object
    instance: str
    user_id: str | None

    async def get_state(self, key, default=None):
        return {"unity_instance": self.instance, "user_id": self.user_id}.get(key, default)


class VerificationIO:
    def __init__(self, ctx, target):
        self.ctx, self.owner = ctx, target.scope.key
        self.resume = target.resume_generation
        self.generation = target.scope.generation
        self.on_refresh = None
        self.impact_requested = False

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
        if target.public_available is not None and not await target.public_available(
            names, self.ctx.fastmcp
        ):
            raise ValueError("verification_child_tool_unavailable")
        return target

    async def authorize_profile(self, profile):
        for name, params in profile_operations(profile, include_impact=self.impact_requested):
            await self.authorize(name, params)

    async def native(self, command, params, deadline):
        if monotonic() >= deadline:
            raise TimeoutError()
        target = await asyncio.wait_for(
            self.authorize(command, params, deadline), max(0, deadline - monotonic())
        )
        response = await asyncio.wait_for(
            send_with_unity_instance(
                async_send_command_with_retry,
                self.ctx.instance,
                command,
                params,
                retry_on_reload=False,
                _deadline=deadline,
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
            if name == "refresh_unity" and callable(self.on_refresh):
                children.append(self.on_refresh(params))

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

    async def quality(self, action, options, deadline):
        await asyncio.wait_for(
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
            service.execute(self.ctx, action, options), max(0, deadline - monotonic())
        )
        if response.get("success") is False:
            job = service.jobs.get(response.get("job_id"))
            if job is not None and job.owner == self.owner and job.terminal:
                response = {**response, "settled": True}
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
            if preparation["expires"] <= monotonic() and (
                preparation["ready"] or preparation.get("failed")
            ):
                del self.preparations[identity]
        for identity, runner in list(self.runners.items()):
            if identity not in self.store.records and (
                identity not in self.tasks or self.tasks[identity].done()
            ):
                self.runners.pop(identity, None)
                self.tasks.pop(identity, None)

    async def execute(self, ctx, action, options):
        try:
            parsed = parse_verification_options(action, options).model_dump(exclude_none=True)
        except ValueError as error:
            return self.error("invalid_verification_request", str(error)[:2048])
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
            profile = resolve_profile(parsed["profile_id"], document)
            if (
                len(self.preparations) >= 64
                or sum(p["owner"] == owner for p in self.preparations.values()) >= 16
            ):
                return self.error("verification_preparation_capacity")
            selection = {key: parsed[key] for key in ("paths", "change_set_id") if key in parsed}
            baseline_id = parsed.get("baseline_id")
            if parsed["capture_baseline"]:
                await io.authorize("manage_script_quality", {"action": "capture_baseline"})
                capture = await io.quality(
                    "capture_baseline",
                    {**selection, "timeout_seconds": profile.deadlines.analysis},
                    deadline,
                )
                baseline_id = (capture.get("data") or {}).get("job_id") or capture.get("job_id")
                if not baseline_id:
                    return capture
            identity = uuid4().hex
            self.preparations[identity] = {
                "owner": owner,
                "instance": instance,
                "resume": target.resume_generation,
                "generation": target.scope.generation,
                "preparation_id": identity,
                "selection": selection,
                "profile": profile,
                "profile_id": parsed["profile_id"],
                "profile_hash": data.get("byte_hash"),
                "baseline_id": baseline_id,
                "ready": not parsed["capture_baseline"],
                "state": "pending",
                "run_id": None,
                "expires": monotonic() + 1800,
            }
            return {"success": True, "data": self.preparation_view(self.preparations[identity])}
        if "preparation_id" in parsed:
            preparation = self.preparations.get(parsed["preparation_id"])
            if preparation is None or preparation["owner"] != owner:
                return self.error("verification_not_found")
            if preparation["resume"] != target.resume_generation or (
                not preparation["resume"] and preparation["generation"] != target.scope.generation
            ):
                return self.error("verification_editor_process_changed")
            if not preparation["ready"] and not preparation.get("failed"):
                baseline = await io.quality(
                    "status", {"job_id": preparation["baseline_id"]}, deadline
                )
                data = baseline.get("data") or {}
                if data.get("baseline_id") == preparation["baseline_id"]:
                    preparation["ready"] = True
                elif data.get("terminal") and data.get("state") in {
                    "completed",
                    "failed",
                    "canceled",
                }:
                    preparation.update(failed=True, state="incomplete")
            if action == "status":
                return {"success": True, "data": self.preparation_view(preparation)}
            if not preparation["ready"]:
                return self.error("verification_preparation_not_ready")
            async with self.lock:
                if preparation["run_id"]:
                    return self.read(preparation["run_id"], owner)
                io.impact_requested = bool(preparation["baseline_id"])
                await io.authorize_profile(preparation["profile"])
                runner = VerificationRunner(
                    io,
                    preparation["profile"],
                    preparation["selection"],
                    baseline_id=preparation["baseline_id"],
                )
                run_id = self.store.create(owner, runner.report)
                preparation["run_id"] = run_id
                runner.report.update(
                    run_id=run_id,
                    preparation_id=preparation["preparation_id"],
                    profile_id=preparation["profile_id"],
                    profile_hash=preparation["profile_hash"],
                    project_scope=owner[1],
                    required_permissions=sorted(profile_permissions(preparation["profile"])),
                )
                runner.emit = lambda report: self.store.update(run_id, owner, report)
                runner.publish()
                self.runners[run_id] = runner
                self.tasks[run_id] = asyncio.create_task(
                    self._run(run_id, owner, runner, lease), context=contextvars.Context()
                )
                return self.read(run_id, owner)
        identity = parsed["run_id"]
        if self.store.read(identity, owner) is None:
            return self.error("verification_not_found")
        runner = self.runners.get(identity)
        if action == "cancel" and runner is not None:
            runner.cancel_requested = True
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

    async def _run(self, identity, owner, runner, lease):
        with bind_credential_lease(lease):
            try:
                result = await runner.run()
                self.store.finish(identity, owner, result, settled=result["native_settled"])
            except BaseException:
                # Task cancellation does not release native ownership or authorize replay.
                runner.report.update(state="incomplete", interruption="orchestrator_interrupted")
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
            except (TimeoutError, ConnectionError, ValueError):
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
        return {
            key: preparation[key]
            for key in (
                "preparation_id",
                "selection",
                "profile_id",
                "profile_hash",
                "baseline_id",
                "ready",
                "state",
                "run_id",
            )
        } | {"profile": preparation["profile"].model_dump()}
