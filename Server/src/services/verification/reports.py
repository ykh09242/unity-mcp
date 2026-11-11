"""Bounded report lifetime and evidence-based stage verdicts."""

import copy
from collections import OrderedDict
from time import monotonic
from uuid import uuid4

from services.script_quality.pages import encoded_size


def final_verdict(stages):
    states = {stage["state"] for stage in stages if stage.get("required", True)}
    for state in ("stale", "failed", "canceled", "incomplete"):
        if state in states:
            return state
    return "passed" if states == {"passed"} else "incomplete"


def test_settled(data):
    control = data.get("native_control")
    if (
        data.get("status") not in {"succeeded", "failed", "cancelled"}
        or not isinstance(control, dict)
        or type(control.get("version")) is not int
        or control["version"] != 1
        or not data.get("job_id")
        or control.get("job_id") != data["job_id"]
        or control.get("released") is not True
        or control.get("observation_lost") is True
        or control.get("ambiguous") is True
    ):
        return False
    if control.get("termination") == "not_started":
        return (
            data["status"] != "succeeded"
            and control.get("native_invoked") is False
            and control.get("final_witness") is False
            and not control.get("native_guid")
        )
    return (
        control.get("termination") == "confirmed"
        and control.get("final_witness") is True
        and control.get("native_invoked") is True
        and isinstance(control.get("native_guid"), str)
        and bool(control["native_guid"])
    )


def test_verdict(data):
    summary = (data.get("result") or {}).get("summary") or {}
    if data.get("status") == "failed" or summary.get("failed", 0) > 0:
        return "failed"
    if data.get("status") == "cancelled":
        return "canceled"
    if not test_settled(data) or type(summary.get("total")) is not int or summary["total"] < 1:
        return "incomplete"
    if data.get("status") != "succeeded" or data.get("completion_kind") != "completed":
        return "incomplete"
    if summary.get("passed") != summary["total"] or summary.get("skipped", 0) != 0:
        return "incomplete"
    return "passed"


def console_delta(before, after):
    if not before or not after:
        return {
            "state": "incomplete",
            "coverage": "unknown",
            "reason": "console_observation_unavailable",
        }
    errors = [
        row
        for row in after.get("items", [])
        if row.get("entry", {}).get("type") in {"Error", "Assert", "Exception"}
    ]
    known = bool(
        before.get("generation")
        and before.get("generation") == after.get("generation")
        and after.get("loss_unknown") is False
        and not after.get("has_more")
    )
    return {
        "state": "failed" if errors else "passed" if known else "incomplete",
        "coverage": "complete" if known else "unknown",
        "reason": None if known else "console_loss_or_reset_not_excluded",
        "observed_error_count": len(errors),
        "generation": after.get("generation"),
        "cursor": after.get("next_cursor"),
    }


class ReportStore:
    def __init__(self, *, clock=monotonic, max_completed=16, ttl=1800, max_active_per_scope=2):
        self.clock, self.max_completed, self.ttl = clock, max_completed, ttl
        self.max_active_per_scope = max_active_per_scope
        self.records = OrderedDict()

    def _expire(self):
        for identity, record in list(self.records.items()):
            if record["settled"] and record["expires"] <= self.clock():
                del self.records[identity]

    def create(self, owner, report):
        self._expire()
        active = [row for row in self.records.values() if not row["settled"]]
        if (
            len(active) >= 16
            or sum(row["owner"] == owner for row in active) >= self.max_active_per_scope
        ):
            raise ValueError("verification_capacity")
        while len(self.records) >= 64:
            expired = next((key for key, row in self.records.items() if row["settled"]), None)
            if expired is None:
                raise ValueError("verification_capacity")
            del self.records[expired]
        identity = uuid4().hex
        self.records[identity] = {
            "owner": owner,
            "settled": False,
            "expires": float("inf"),
            "report": {},
        }
        self.update(identity, owner, report)
        return identity

    def update(self, identity, owner, report):
        row = self.records.get(identity)
        if row is None or row["owner"] != owner:
            raise ValueError("verification_not_found")
        if encoded_size(report) > 512 * 1024:
            raise ValueError("verification_report_limit")
        row["report"] = copy.deepcopy(report)

    def finish(self, identity, owner, report, *, settled=True):
        self.update(identity, owner, report)
        row = self.records[identity]
        row.update(settled=settled, expires=self.clock() + self.ttl if settled else float("inf"))
        completed = [
            key for key, item in self.records.items() if item["owner"] == owner and item["settled"]
        ]
        for key in completed[: -self.max_completed]:
            del self.records[key]

    def read(self, identity, owner):
        self._expire()
        row = self.records.get(identity)
        return copy.deepcopy(row["report"]) if row is not None and row["owner"] == owner else None
