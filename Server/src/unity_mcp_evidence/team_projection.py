"""Privacy-preserving inputs for the existing comparison and selection verdicts."""

import hashlib
import hmac
import json
import math
import re
import secrets

from .verification_change import CapturedRun
from .verification_selection import Cohort, FixtureObservation

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_STATES = {"passed", "failed", "incomplete", "canceled", "stale", "not_run"}
_KINDS = {"scenario", "stage", "observation", "test", "profile"}
_CONDITIONS = {
    "operating_system",
    "platform",
    "architecture",
    "scripting_backend",
    "build_mode",
    "runtime_version",
    "project",
    "unity_version",
    "package_version",
    "definition_hash",
    "repeat_count",
    "timeout_seconds",
    "scenario",
    "replay_seed",
    "start_delay_frames",
}
_IDS = {
    "run_id": "run",
    "job_id": "job",
    "build_id": "build",
    "payload": "payload",
    "source": "source",
    "manifest": "manifest",
    "baseline_manifest": "manifest",
    "baseline_source": "source",
    "scenario": "check",
}


def encoded(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def opaque(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("Expected a SHA-256 value in shared evidence")
    return value


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() <= set(required) | set(
        optional
    ):
        raise ValueError("Unexpected fields in shared evidence")
    return value


def items(value):
    if not isinstance(value, list):
        raise ValueError("Expected a list in shared evidence")
    return value


def choice(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("Unknown value in shared evidence")
    return value


class Aliases:
    """Keep equality across inputs in one bundle without publishing names or a lookup key."""

    def __init__(self):
        self.key = secrets.token_bytes(32)

    def token(self, domain, value):
        return hmac.new(self.key, encoded([domain, value]), hashlib.sha256).hexdigest()

    def nullable(self, domain, value):
        return None if value is None else self.token(domain, value)


def project_run(run: CapturedRun, aliases: Aliases) -> dict:
    checks = []
    for (kind, name), check in sorted(run.checks.items()):
        record = {"kind": kind, "id": aliases.token("check", name), "state": check["state"]}
        for field in ("target", "mode", "definition_hash", "expected_state", "signature", "job_id"):
            if field in check:
                value = check[field]
                if field == "signature" and kind == "scenario" and value == {}:
                    record[field] = {}
                elif (
                    field == "target"
                    and isinstance(value, str)
                    and value in {"standalone_player", "editor", "verification"}
                ):
                    record[field] = value
                else:
                    record[field] = aliases.nullable("job" if field == "job_id" else field, value)
        if "child_job_ids" in check:
            record["child_job_ids"] = [aliases.token("job", job) for job in check["child_job_ids"]]
        checks.append(record)
    return {
        "identity": {k: aliases.nullable(domain, getattr(run, k)) for k, domain in _IDS.items()},
        "conditions": {k: aliases.nullable("condition/" + k, v) for k, v in run.conditions.items()},
        "state": run.state,
        "inputs_confirmed": run.inputs_confirmed,
        "selection_gaps": [aliases.token("gap", gap) for gap in run.selection_gaps],
        "checks": checks,
    }


def restore_run(row) -> CapturedRun:
    fields(row, ("identity", "conditions", "state", "inputs_confirmed", "selection_gaps", "checks"))
    ids = fields(row["identity"], _IDS)
    for key, value in ids.items():
        if key not in {"baseline_manifest", "baseline_source"} or value is not None:
            opaque(value)
    conditions = fields(row["conditions"], _CONDITIONS)
    for value in conditions.values():
        if value is not None:
            opaque(value)
    if type(row["inputs_confirmed"]) is not bool:
        raise ValueError("Expected an explicit source confirmation")
    checks = {}
    optional = {
        "target",
        "mode",
        "definition_hash",
        "expected_state",
        "signature",
        "job_id",
        "child_job_ids",
    }
    for check in items(row["checks"]):
        fields(check, ("kind", "id", "state"), optional)
        kind = choice(check["kind"], _KINDS)
        key = (kind, opaque(check["id"]))
        if key in checks:
            raise ValueError("Repeated shared check identity")
        checked = {"state": choice(check["state"], _STATES)}
        for field in optional & check.keys():
            value = check[field]
            if field == "child_job_ids":
                checked[field] = [opaque(v) for v in items(value)]
            elif value is None or (field == "signature" and kind == "scenario" and value == {}):
                checked[field] = value
            elif (
                field == "target"
                and isinstance(value, str)
                and value in {"standalone_player", "editor", "verification"}
            ):
                checked[field] = value
            else:
                checked[field] = opaque(value)
        checks[key] = checked
    primary = checks.get(("scenario", ids["scenario"]), {})
    if (
        primary.get("job_id") != ids["job_id"]
        or primary.get("target") != "standalone_player"
        or primary.get("state") not in _STATES - {"not_run"}
    ):
        raise ValueError("Shared Player projection lacks its primary scenario and owned job")
    return CapturedRun(
        **ids,
        conditions=conditions,
        checks=checks,
        state=choice(row["state"], _STATES - {"not_run"}),
        selection_gaps=[opaque(v) for v in items(row["selection_gaps"])],
        inputs_confirmed=row["inputs_confirmed"],
    )


def project_cohort(cohort: Cohort, aliases: Aliases) -> dict:
    fixture = cohort.fixture
    return {
        "execution": aliases.token("execution", fixture.execution_id),
        "context": aliases.token("context", fixture.context),
        "expected": sorted(aliases.token("test", name) for name in fixture.expected_cases),
        "inputs": {
            aliases.token("asset", path): [aliases.token("guid", guid), aliases.token("blob", blob)]
            for path, (guid, blob) in fixture.inputs.items()
        },
        "cases": {aliases.token("test", name): state for name, state in cohort.cases.items()},
        "seconds": cohort.seconds,
    }


def restore_cohort(row, raw_digest) -> Cohort:
    fields(row, ("execution", "context", "expected", "inputs", "cases", "seconds"))
    expected = [opaque(v) for v in items(row["expected"])]
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("Incomplete shared fixture inventory")
    if (
        not isinstance(row["inputs"], dict)
        or not row["inputs"]
        or not isinstance(row["cases"], dict)
    ):
        raise ValueError("Invalid shared NUnit observations")
    inputs = {}
    for asset, identity in row["inputs"].items():
        if len(items(identity)) != 2:
            raise ValueError("Invalid shared asset identity")
        inputs[opaque(asset)] = (opaque(identity[0]), opaque(identity[1]))
    cases = {
        opaque(k): choice(v, {"Passed", "Failed", "Skipped", "Inconclusive"})
        for k, v in row["cases"].items()
    }
    seconds = row["seconds"]
    if (
        not cases
        or not cases.keys() <= set(expected)
        or type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        raise ValueError("Invalid shared test coverage or duration")
    fixture = FixtureObservation(
        opaque(row["execution"]), opaque(row["context"]), frozenset(expected), inputs
    )
    return Cohort(opaque(raw_digest), fixture, cases, seconds)
