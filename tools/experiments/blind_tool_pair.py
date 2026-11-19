"""Prepare both real hosts and check their controls before either receives a task."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from hashlib import sha256
import json
from threading import Condition
from time import monotonic_ns
from uuid import uuid4

from tools.experiments.blind_tool_preflight import pair_controls


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PreparedPair:
    def __init__(self, order, timeout, competitors=None):
        self.order = tuple(order)
        self.timeout = timeout
        self.competitors = sorted(competitors or ())
        self.id = str(uuid4())
        self.condition = Condition()
        self.ready = {}
        self.reasons = []
        self.started = {}
        self.finished = set()

    def _check(self):
        left, right = (self.ready[arm] for arm in ("baseline", "candidate"))
        reasons = pair_controls(left, right)
        if left["thread"] == right["thread"]:
            reasons.append("identical:prepared_threads")
        for arm, case in self.ready.items():
            actor = case["actor_conditions"]
            permissions = actor.get("permissions") or {}
            if not actor.get("tool_schema_digest") or actor["inventory_status"] != "observed":
                reasons.append("unverified:" + arm + "_inventory")
            if case["competition_observed"] is not True:
                reasons.append("unverified:" + arm + "_competition")
            if self.competitors and self.competitors != case["competitors"]:
                reasons.append("different:declared_competitors")
            if (
                permissions.get("sandbox") not in ("readOnly", "read-only")
                or not permissions.get("digest")
                or permissions.get("approval") != case["approval"]
            ):
                reasons.append("unverified:" + arm + "_permissions")
        return reasons

    def arrive(self, arm, actor, surface, metadata):
        with self.condition:
            self.ready[arm] = {
                "thread": actor,
                "at_ns": monotonic_ns(),
                "model": metadata["model"],
                "effort": metadata["effort"],
                "approval": "on-request"
                if metadata["approval_policy"] == "auto-review"
                else metadata["approval_policy"],
                "editor_start_state": metadata.get("editor_start_state"),
                "competition_observed": surface.get("competition_observed"),
                "competitors": sorted(surface.get("competing_servers", {})),
                "actor_conditions": {
                    **{
                        k: surface.get(k)
                        for k in (
                            "model",
                            "effort",
                            "permissions",
                            "project_verified",
                            "namespace",
                            "tool_schema_digest",
                            "competitor_digest",
                        )
                    },
                    "server_commit": (surface.get("source") or {}).get("server_commit"),
                    "host_binary_digest": metadata.get("host_binary_digest"),
                    "inventory_status": surface.get("status"),
                },
            }
            if len(self.ready) == 2:
                self.reasons.extend(self._check())
            self.condition.notify_all()
            can_run = self.condition.wait_for(
                lambda: (
                    self.reasons
                    or (
                        len(self.ready) == 2
                        and (arm == self.order[0] or self.order[0] in self.finished)
                    )
                ),
                timeout=self.timeout * 2,
            )
            if not can_run:
                self.reasons.append("timeout:paired_preparation")
                self.condition.notify_all()
            return not self.reasons

    def start(self, arm, current_state):
        with self.condition:
            if not current_state or current_state != self.ready[arm]["editor_start_state"]:
                self.reasons.append("different:editor_before_delivery")
            self.condition.notify_all()
            if self.reasons:
                return None
            self.started[arm] = monotonic_ns()
            return {
                "id": self.id,
                "order": list(self.order),
                "cohort": "fixed_competitors" if self.competitors else "configured_competitors",
                "declared_competitors": self.competitors,
                "controls_digest": _digest(self.ready),
                "ready_ns": {arm: item["at_ns"] for arm, item in self.ready.items()},
                "prompt_ns": self.started[arm],
            }

    def finish(self, arm):
        with self.condition:
            if arm not in self.started and not self.reasons:
                self.reasons.append("unverified:" + arm + "_preparation")
            self.finished.add(arm)
            self.condition.notify_all()


def prepared_pair(arms, order, cell, root, run_case):
    """Retry transient mismatches only before any prompt. Retain the predeclared execution order."""
    name = "{}-{}-{}-{:02d}".format(*cell)
    attempts = []

    def run(arm, directory, gate):
        selected = copy(arms[arm])
        selected.output = directory / arm
        selected.output.mkdir(parents=True)
        selected.pair_gate = gate
        try:
            run_case(selected, *cell)
            return selected.output / name
        finally:
            gate.finish(arm)

    for number in (1, 2):
        gate = PreparedPair(
            order, arms[order[0]].timeout, getattr(arms[order[0]], "competitor", None)
        )
        directory = root / "preparation" / name / str(number)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {arm: pool.submit(run, arm, directory, gate) for arm in order}
            paths = {arm: future.result() for arm, future in futures.items()}
        attempts.append(
            {
                "id": gate.id,
                "prepared": gate.ready,
                "prompt_ns": gate.started,
                "reasons": gate.reasons,
            }
        )
        if (
            number == 2
            or gate.started
            or not gate.reasons
            or not all(
                reason
                in {
                    "different:actor_competitor_digest",
                    "different:editor_start_state",
                    "different:editor_before_delivery",
                }
                for reason in gate.reasons
            )
        ):
            break
    for arm, path in paths.items():
        destination = arms[arm].output / name
        if not path.resolve().is_relative_to(
            root.resolve()
        ) or not destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("Pair output escaped the selected study directory")
        path.rename(destination)
    (root / "preparation" / name / "pair.json").write_text(
        json.dumps(attempts, indent=2) + "\n", encoding="utf-8"
    )


def preparation_issues(left, right):
    receipts = [case.get("prepared_pair") or {} for case in (left, right)]
    if not all(receipts):
        return ["unverified:before_prompt_preparation"]
    a, b = receipts
    order = a.get("order")
    ready = a.get("ready_ns")
    if (
        any(
            not a.get(k) or a[k] != b.get(k)
            for k in ("id", "order", "cohort", "controls_digest", "ready_ns")
        )
        or a.get("declared_competitors") != b.get("declared_competitors")
        or order not in (["baseline", "candidate"], ["candidate", "baseline"])
        or not isinstance(ready, dict)
        or set(ready) != set(order)
        or not all(
            type(n) is int and n > 0
            for n in [*ready.values(), a.get("prompt_ns"), b.get("prompt_ns")]
        )
    ):
        return ["unverified:before_prompt_preparation"]
    prompts = dict(zip(("baseline", "candidate"), (a["prompt_ns"], b["prompt_ns"]), strict=True))
    if not max(ready.values()) <= prompts[order[0]] < prompts[order[1]]:
        return ["different:prepared_execution_order"]
    return []
