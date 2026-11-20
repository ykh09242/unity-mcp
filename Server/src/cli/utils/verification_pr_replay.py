"""Paired chronological replay of time-budgeted NUnit selections from retained PR evidence."""

import re
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from services.verification.git_draft_artifacts import ChunkedDraftSide, InlineDraftSide
from services.verification.input_chunks import decode_object, load_chunks

from .play_scenario_analysis_io import AnalysisError, read_bytes, read_json, safe_local_path
from .verification_selection import read_cohort
from .verification_test_budget import BudgetInputs, assess_budget, parse_ranking, plan_budget
from .verification_test_ranking import OID, items, label, propose_ranking


def instant(value):
    if not isinstance(value, str):
        raise AnalysisError("Expected an archive timestamp with timezone")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise AnalysisError("Archive timestamps require a timezone")
    return stamp


class Archives:
    def __init__(self, manifest):
        self.root = safe_local_path(manifest).parent
        self.remaining = 256 * 1024**2
        self.bindings = {}

    def path(self, receipt, xml):
        relative = label(receipt.get("path"), "relative archive path")
        parts = relative.split("/")
        if any(
            not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", p) or p.endswith(".") for p in parts
        ) or not relative.endswith(".xml" if xml else ".json"):
            raise AnalysisError("Expected a relative archive JSON or XML filename")
        return safe_local_path(self.root.joinpath(*parts))

    def reserve(self, size):
        self.remaining -= size
        if self.remaining < 0:
            raise AnalysisError("Replay exceeds 256 MiB of selected archive input")

    def load(self, receipt, cutoff, commit, *, result=False, xml=False, historical=False):
        if not isinstance(receipt, dict):
            raise AnalysisError("Missing dated artifact receipt")
        available = instant(receipt.get("available_at"))
        if (available > cutoff) != result:
            raise AnalysisError("Artifact availability is on the wrong side of the plan cutoff")
        candidate = receipt.get("commit")
        wrong_candidate = candidate == commit if historical else candidate != commit
        if not OID.fullmatch(str(candidate)) or wrong_candidate:
            raise AnalysisError("Artifact belongs to the wrong candidate")
        path = self.path(receipt, xml)
        size = path.stat().st_size
        self.reserve(size)
        if xml:
            data = read_cohort(path)
            digest = data.digest
            binding = (candidate, digest, available)
            for key in (("sha256", digest), ("execution", data.fixture.execution_id)):
                if self.bindings.get(key) != {binding}:
                    raise AnalysisError(
                        "Conflicting execution identity or retained outcome receipt"
                    )
        else:
            raw = read_bytes(path, min(size, 4 * 1024**2))
            digest = sha256(raw).hexdigest()
            data = decode_object(raw)
        if digest != receipt.get("sha256"):
            raise AnalysisError("Artifact SHA-256 differs from its dated receipt")
        return data

    def index_executions(self, rows):
        # Index identities only. Partial PRs still contribute every available
        # receipt, so an earlier ranking cannot consume a later re-export.
        cache = {}
        claims = []
        invalid = set()
        for row in rows:
            keys = set()
            claims.append(keys)
            if not isinstance(row, dict):
                raise AnalysisError("Expected an archived PR entry")
            prior = row.get("history")
            receipts = (
                [(r, False) for r in prior if isinstance(r, dict)]
                if isinstance(prior, list) and len(prior) <= 16
                else []
            )
            for field in ("automatic", "manual", "full"):
                if isinstance(row.get(field), dict):
                    receipts.append((row[field], True))
            cutoff = instant(row["plan_at"]) if row.get("plan_at") else None
            for receipt, outcome in receipts:
                digest = receipt.get("sha256")
                if not isinstance(digest, str):
                    continue
                try:
                    stamp = instant(receipt.get("available_at"))
                except ValueError:
                    stamp = None
                commit = receipt.get("commit")
                wrong_role = (commit == row.get("commit")) != outcome
                wrong_time = stamp is None or (cutoff is not None and (stamp > cutoff) != outcome)
                binding = (str(commit), digest, stamp)
                receipt_keys = {("sha256", digest)}
                try:
                    path = self.path(receipt, True)
                    if (path, digest) not in cache:
                        self.reserve(path.stat().st_size)
                        run = read_cohort(path)
                        if run.digest == digest:
                            cache[path, digest] = run.fixture.execution_id
                    if (path, digest) in cache:
                        receipt_keys.add(("execution", cache[path, digest]))
                except (OSError, ValueError):
                    pass
                keys.update(receipt_keys)
                for key in receipt_keys:
                    self.bindings.setdefault(key, set()).add(binding)
                if wrong_time or wrong_role:
                    invalid.update(receipt_keys)
        if self.remaining < 0:
            raise AnalysisError("Replay exceeds 256 MiB of selected archive input")

        ambiguous = invalid | {key for key, receipts in self.bindings.items() if len(receipts) > 1}
        return {index for index, keys in enumerate(claims) if not keys.isdisjoint(ambiguous)}

    def draft_inputs(self, draft, receipt):
        side = draft.get("candidate")
        if not isinstance(side, dict):
            raise AnalysisError("Missing candidate input inventory")
        if "input_chunks" in side:
            if draft.get("schema_version") != 2:
                raise AnalysisError("Chunked candidate requires a version 2 draft")
            descriptor = ChunkedDraftSide.model_validate(side)

            def read_chunk(path, limit):
                self.reserve(safe_local_path(path).stat().st_size)
                return read_bytes(path, limit)

            inputs, rows = load_chunks(
                self.path(receipt, False).parent, descriptor.input_chunks, read=read_chunk
            )
            if any(row.state != "verified" for row in rows):
                raise AnalysisError("Chunked candidate input inventory incomplete")
        else:
            descriptor = InlineDraftSide.model_validate(side)
            inputs = descriptor.inputs
        descriptor.validate_inputs(inputs)
        return inputs


def prepare_plans(archives, row, cutoff, commit, budget):
    """Compute both plans from the information available at the cutoff."""

    def capture(field, **options):
        return archives.load(row[field], cutoff, commit, **options)

    draft = capture("draft")
    if draft.get("candidate_commit") != commit:
        raise AnalysisError("Draft differs from historical PR head")
    inputs = archives.draft_inputs(draft, row["draft"])
    history = [
        archives.load(r, cutoff, commit, historical=True, xml=True)
        for r in items(row, "history", 16)
    ]
    closures = [archives.load(r, cutoff, commit) for r in items(row, "closures", 32)]
    automatic = propose_ranking(draft, capture("mappings"), closures, history)
    plans = {
        name: plan_budget(BudgetInputs(parse_ranking(ranking), tuple(history), budget))
        for name, ranking in (("automatic", automatic), ("manual", capture("manual_ranking")))
    }
    return inputs, automatic, plans


def _trial(archives, row, cutoff, commit, budget):
    inputs, automatic, plans = prepare_plans(archives, row, cutoff, commit, budget)
    full = archives.load(row["full"], cutoff, commit, result=True, xml=True)
    for path, expected in inputs.items():
        observed = full.fixture.inputs.get(path)
        if observed is not None and observed[1] != expected:
            raise AnalysisError("Full run observed different bytes from the candidate draft")
    if full.fixture.expected_cases != full.cases.keys():
        raise AnalysisError("Independent full run is missing inventory cases")
    controls = {}
    ids = {full.fixture.execution_id}
    for name, plan in plans.items():
        selected = None
        if row.get(name) is not None:
            selected = archives.load(row[name], cutoff, commit, result=True, xml=True)
            if selected.fixture.execution_id in ids:
                raise AnalysisError("Automatic, manual and full controls must be independent")
            ids.add(selected.fixture.execution_id)
        elif plan["test_names"]:
            raise AnalysisError(f"Missing independent {name} result for its planned tests")
        controls[name] = assess_budget(plan, full, selected)
        if controls[name]["missed_failures"] is None:
            raise AnalysisError(f"Incomparable {name} and full results")
    return {
        "state": "paired",
        "context": full.fixture.context,
        "ranking": automatic,
        "full": controls["manual"]["full"],
        **controls,
    }


def replay(manifest: Path):
    data = read_json(manifest)
    rows = items(data, "prs", 128)
    budget = data.get("budget_ms")
    if (
        data.get("schema") != "verification_pr_replay@1"
        or data.get("cohort_kind") not in ("historical", "synthetic_fixture")
        or not rows
        or type(budget) is not int
        or budget < 1
    ):
        raise AnalysisError("Expected a labeled PR replay inventory and positive common budget")
    archives = Archives(manifest)
    conflicted = archives.index_executions(rows)
    seen = set()
    commits = set()
    timeline = []
    last = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise AnalysisError("Expected an archived PR entry")
        pr = label(row.get("pr"), "PR identity")
        commit = row.get("commit")
        cutoff = instant(row.get("plan_at") or row.get("archived_at"))
        if (
            not OID.fullmatch(str(commit))
            or commit in commits
            or pr in seen
            or (last and cutoff < last)
        ):
            raise AnalysisError("Expected distinct PRs and commits in plan-time order")
        last = cutoff
        seen.add(pr)
        commits.add(commit)
        trial = {"pr": pr, "commit": commit, "plan_at": row.get("plan_at")}
        required = ("plan_at", "draft", "mappings", "history", "closures", "manual_ranking", "full")
        missing = [k for k in required if row.get(k) is None or (k == "history" and not row[k])]
        if index in conflicted:
            trial.update(
                state="incomparable",
                reason="Conflicting execution identity or retained outcome receipt",
                missing=missing,
            )
        elif missing:
            trial.update(
                state="unavailable",
                missing=missing,
                absent_selection_archives=[
                    k for k in ("automatic", "manual") if row.get(k) is None
                ],
            )
        else:
            try:
                trial.update(_trial(archives, row, cutoff, commit, budget))
            except (OSError, ValueError) as exc:
                trial.update(state="incomparable", reason=str(exc))
        timeline.append(trial)

    pairs = [r for r in timeline if r["state"] == "paired"]
    summary = {}
    for mode in ("automatic", "manual"):
        runs = [p[mode] for p in pairs]
        timed = [r for r in runs if r["measured_seconds_saved"] is not None]
        summary[mode] = {
            "full_failures": sum(len(r["full"]["failed_checks"]) for r in runs) if runs else None,
            "missed_failures": sum(len(r["missed_failures"]) for r in runs) if runs else None,
            "timed_prs": len(timed),
            "seconds_saved": sum(r["measured_seconds_saved"] for r in timed) if timed else None,
            "budget_overruns": sum(r["within_observed_budget"] is False for r in timed)
            if timed
            else None,
        }
    return {
        "schema": "verification_pr_replay_result@1",
        "cohort_kind": data["cohort_kind"],
        "state": "measured" if len(pairs) == len(rows) else "incomplete",
        "budget_ms": budget,
        "total_prs": len(rows),
        "paired_prs": len(pairs),
        "unpaired_prs": len(rows) - len(pairs),
        "summary": summary,
        "prs": timeline,
    }
