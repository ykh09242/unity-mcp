"""Request/command elapsed budgets and bounded dispatch evidence.

PluginHub remains the transport lifecycle owner. This module owns no task, socket,
retry policy or global deadline; callers provide clocks/scopes and retain admission.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from models.models import MCPResponse


def command_outcome_unknown(command: str, detail: str, **context: Any) -> dict[str, Any]:
    """A lost transport reply is not evidence that Unity did not execute."""
    return MCPResponse(
        success=False,
        error=(
            f"Outcome of '{command}' is unknown: {detail}. "
            "Inspect the target Unity state before retrying; the command may have executed."
        ),
        hint="inspect_state_before_retry",
        data={"reason": "outcome_unknown", "command": command, **context},
    ).model_dump()


@dataclass(slots=True)
class _CommandBudget:
    expires_at: float
    phase: str = "session_selection"
    session_id: str | None = None
    command_id: str | None = None
    dispatched: bool = False
    command: str | None = None
    active: _CommandBudget | None = None
    prior_dispatch: _CommandBudget | None = None

    def execution_evidence(self) -> _CommandBudget:
        current = self.active.execution_evidence() if self.active is not None else self
        return current if current.dispatched else self.prior_dispatch or current

    def remaining(self) -> float:
        return max(0.0, self.expires_at - asyncio.get_running_loop().time())

    def timeout_response(self, command: str) -> dict[str, Any]:
        evidence = self.execution_evidence()
        if evidence is not self:
            return evidence.timeout_response(command)
        command = self.command or command
        detail = f"total command deadline exceeded during {self.phase}"
        context = {"phase": self.phase, "session_id": self.session_id}
        if self.dispatched:
            return command_outcome_unknown(
                command, detail, **context, command_id=self.command_id, cause="timeout"
            )
        return MCPResponse(
            success=False,
            error=f"Unity command was not dispatched: {detail}",
            hint="retry",
            data={"reason": "command_deadline_exceeded", "command": command, **context},
        ).model_dump()


@dataclass(slots=True)
class _HttpRequestBudget:
    """One origin for HTTP selection, tool work and transport execution."""

    started_at: float
    budget: _CommandBudget
    scope: asyncio.Timeout
    hard_expires_at: float | None = None

    def extend(self, timeout_s: float) -> None:
        # A tool may transform arguments or discover an existing long-job policy.
        # Account for that policy from request entry, never from this later phase.
        expires_at = self.started_at + timeout_s
        if self.hard_expires_at is not None:
            expires_at = min(expires_at, self.hard_expires_at)
        if expires_at > self.budget.expires_at and not self.scope.expired():
            self.budget.expires_at = expires_at
            self.scope.reschedule(expires_at)

    def command_budget(self, command: str, timeout_s: float) -> _CommandBudget:
        self.extend(timeout_s)
        budget = _CommandBudget(
            min(self.budget.expires_at, asyncio.get_running_loop().time() + timeout_s),
            command=command,
        )
        previous = self.budget.execution_evidence()
        if previous.dispatched:
            # Keep one earlier dispatch, not an unbounded history of polling reads.
            self.budget.prior_dispatch = previous
        self.budget.active = budget
        return budget


http_request_budget: ContextVar[_HttpRequestBudget | None] = ContextVar(
    "http_request_budget", default=None
)


@dataclass(frozen=True, slots=True)
class CommandEvidenceSnapshot:
    """Copy bounded scalar evidence; never retain the originating request budget."""

    phase: str
    session_id: str | None
    command_id: str | None
    dispatched: bool
    command: str | None


@dataclass(frozen=True, slots=True)
class HttpBudgetSnapshot:
    started_at: float
    expires_at: float
    evidence: CommandEvidenceSnapshot


def capture_http_request_budget() -> HttpBudgetSnapshot | None:
    request = http_request_budget.get()
    if request is None:
        return None
    evidence = request.budget.execution_evidence()
    return HttpBudgetSnapshot(
        request.started_at,
        request.budget.expires_at,
        CommandEvidenceSnapshot(
            evidence.phase,
            evidence.session_id,
            evidence.command_id,
            evidence.dispatched,
            evidence.command,
        ),
    )


@contextmanager
def detached_http_request_budget(snapshot: HttpBudgetSnapshot, scope: asyncio.Timeout):
    """Own a new budget whose live evidence changes but original ceiling cannot."""
    evidence = snapshot.evidence
    budget = _CommandBudget(
        snapshot.expires_at,
        phase=evidence.phase,
        session_id=evidence.session_id,
        command_id=evidence.command_id,
        dispatched=evidence.dispatched,
        command=evidence.command,
    )
    owned = _HttpRequestBudget(
        snapshot.started_at, budget, scope, hard_expires_at=snapshot.expires_at
    )
    token = http_request_budget.set(owned)
    try:
        yield owned
    finally:
        http_request_budget.reset(token)


def extend_http_request_deadline(timeout_s: float) -> None:
    """Retain a tool's existing long-job policy without restarting its clock."""
    request = http_request_budget.get()
    if request is not None:
        request.extend(timeout_s)
