"""Source-bound verification observations; registry publication belongs to the host.

All helpers are offline. Recorded assignments and outcomes complement existing task
transitions; neither requests nor observations grant acceptance or execute commands.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Final, Protocol, TypeAlias

if TYPE_CHECKING:
    from feature_program import PreparedChange, ReadBudget

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
MAX_RECORDS: Final = 2000
MAX_ROWS: Final = 50
LIMIT: Final = 8 * 1024 * 1024
OUTPUT_LIMIT: Final = LIMIT
KINDS: Final = frozenset(
    {"request", "started", "blocked", "finished", "canceled", "finding", "resolution"}
)
RESULTS: Final = frozenset({"passed", "failed", "pending", "not_run", "unknown", "not_applicable"})


class ProgramAccess(Protocol):
    """Only bounded read and detached event capabilities; no publication or executor."""

    repo: Path
    directory: Path

    def path(self, relative: str) -> Path: ...
    def require_current_location(self) -> None: ...
    def validate(self, state: Record) -> None: ...
    def task(self, state: Record, identifier: str) -> Record: ...
    def current_attempt(self, task: Record) -> Record: ...
    def source_snapshot(self, scopes: list[str], budget: ReadBudget | None = None) -> Record: ...
    def verify_refs(self, refs: list[Json], budget: ReadBudget | None = None) -> None: ...
    def changed_paths(self, baseline: Record, snapshot: Record) -> list[str]: ...
    def event(self, state: Record, action: str, detail: Record) -> None: ...


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _inputs() -> ModuleType:
    if __package__:
        from tools import feature_program_inputs
    else:
        import feature_program_inputs
    return feature_program_inputs


def _execution() -> ModuleType:
    if __package__:
        from tools import feature_program_execution
    else:
        import feature_program_execution
    return feature_program_execution


def _provider() -> ModuleType:
    if __package__:
        from tools import feature_program_delivery
    else:
        import feature_program_delivery
    return feature_program_delivery


def _require(condition: bool, reason: str) -> None:
    _core().require(condition, reason)


def _record(value: Json, label: str) -> Record:
    return _core().record(value, label)


def _items(value: Json, label: str) -> list[Json]:
    return _core().sequence(value, label)


def _text(value: Json, label: str) -> str:
    result = _core().string(value, label)
    _require(len(result) <= 65536, f"Oversized {label}")
    return result


def _id(value: Json, label: str) -> str:
    result = _text(value, label)
    _require(_core().portable_task_id(result), f"Invalid {label}")
    return result


def _actor_id(value: Json, label: str) -> str:
    """Reuse established actor identity semantics; actors are not portable filenames."""
    return _core().coordination._text(value, label)


def _texts(value: Json, label: str) -> list[str]:
    return _core().strings(value, label)


def _ref(value: Json) -> Record:
    ref = _record(value, "verification reference")
    _require(set(ref) == {"path", "sha256"}, "Verification reference requires exact path/hash")
    name = _core().safe_relative(_text(ref.get("path"), "reference path"))
    _require(name.startswith("evidence/"), "Verification reference must be program evidence")
    _require(
        bool(re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256")))), "Invalid verification hash"
    )
    return ref


def _records(state: Record) -> list[Record]:
    if "verification" not in state:
        return []
    extension = _record(state["verification"], "verification")
    _require(
        set(extension) == {"schema_version", "records"}, "Invalid verification extension fields"
    )
    _require(
        type(extension.get("schema_version")) is int and extension["schema_version"] == 1,
        "Invalid verification version",
    )
    values = _items(extension.get("records"), "verification records")
    _require(len(values) <= MAX_RECORDS, "Verification history exceeds 2000 records")
    return [_record(value, "verification record") for value in values]


def _index(state: Record) -> dict[str, Record]:  # noqa: DICT_OK
    return {
        str(_record(value["input"], "verification input")["id"]): value for value in _records(state)
    }


def _actor(state: Record, identifier: str) -> None:
    for value in _items(state.get("actors", []), "actors"):
        actor = _record(value, "actor")
        if actor.get("id") == identifier:
            _require(actor.get("staffing_status") != "retired", "Retired verification participant")


def _task_authority(state: Record, actor: str, owner: Json = None) -> None:
    core = _core()
    try:
        core.coordination.require_task_authority(state, actor, owner)
    except core.coordination.GovernanceError as exc:
        raise core.ProgramError(str(exc)) from exc


def _authority(state: Record, task: Record, value: Record, request: Record | None) -> None:
    core = _core()
    actor = _actor_id(value.get("actor_id"), "acting actor")
    _actor(state, actor)
    director = core.coordination.current_director_id(state) or "leader"
    if request is None:
        _task_authority(state, actor, task.get("owner"))
        _actor(state, _actor_id(value.get("verifier_id"), "assigned verifier"))
        return
    request_input = _record(request["input"], "request input")
    verifier = _actor_id(request_input.get("verifier_id"), "assigned verifier")
    _actor(state, verifier)
    participants = {verifier}
    if value.get("kind") in {"finding", "resolution"} and task.get("owner") is not None:
        participants.add(str(task["owner"]))
    if actor in participants:
        return
    _require(
        actor == director, "Only assigned participant or current director may record observation"
    )
    _task_authority(state, actor)
    reporter = _actor_id(value.get("reporter_id"), "director recording requires actual reporter_id")
    _actor(state, reporter)
    _require(reporter in participants, "Reported origin must be assigned participant")


def _time(value: Json, label: str) -> datetime | None:
    if value is None:
        return None
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _core().ProgramError(f"Invalid {label}") from exc
    _require(parsed.tzinfo is not None, f"{label} requires explicit timezone")
    return parsed


def _criteria(state: Record, values: Json) -> list[Json]:
    entries = _items(values, "criterion identities")
    if entries:
        binding = _core().coverage.objective_binding(state)
        seen: set[int] = set()
        for value in entries:
            ordinal = _core().coverage.criterion(value, binding)
            _require(ordinal not in seen, "Duplicate verification criterion")
            seen.add(ordinal)
    return deepcopy(entries)


def _input_shape(value: Record) -> None:
    core = _core()
    _require(
        type(value.get("schema_version")) is int
        and value["schema_version"] == 1
        and value.get("operation") == "verification-record",
        "Invalid verification input schema/operation",
    )
    _id(value.get("id"), "verification ID")
    _id(value.get("task_id"), "task ID")
    _actor_id(value.get("actor_id"), "actor ID")
    _text(value.get("expected_revision"), "expected revision")
    _text(value.get("reason"), "verification reason")
    kind = _text(value.get("kind"), "event kind")
    _require(kind in KINDS, "Invalid verification event kind")
    allowed = {
        "schema_version",
        "operation",
        "id",
        "kind",
        "task_id",
        "actor_id",
        "expected_revision",
        "reason",
        "observed_at",
        "clock_id",
        "reporter_id",
        "artifacts",
        "provider_ref",
    }
    match kind:
        case "request":
            allowed |= {
                "lane",
                "verifier_id",
                "check_ids",
                "criteria",
                "input_scope",
                "input_complete",
                "environment",
                "resource",
                "evidence_destination",
                "next_trigger",
                "context_path",
                "provider_context_path",
                "provider_source_mapping_path",
                "supersedes",
                "input_manifest",
                "manifest_path",
                "input_capture_path",
                "environment_identity",
                "method_identity",
                "provider_input_mapping_path",
            }
            _id(value.get("lane"), "lane")
            _actor_id(value.get("verifier_id"), "verifier_id")
            _require(
                bool(_texts(value.get("check_ids"), "check IDs")), "Request needs check identities"
            )
            _require(
                bool(_texts(value.get("input_scope"), "input scope")),
                "Request needs explicit input scope",
            )
            _require(
                type(value.get("input_complete")) is bool,
                "Input completeness must be explicit boolean",
            )
            _require(
                not ("input_manifest" in value and "manifest_path" in value),
                "Choose inline input_manifest or manifest_path",
            )
            if "input_manifest" in value:
                _inputs().normalize_manifest(value["input_manifest"])
            for name in ("manifest_path", "input_capture_path", "provider_input_mapping_path"):
                if name in value:
                    core.safe_relative(_text(value[name], name))
            core.safe_relative(
                _text(value.get("evidence_destination"), "evidence destination").rstrip("/")
            )
            _text(value.get("next_trigger"), "next trigger")
            if value.get("resource") is not None:
                resource = _record(value["resource"], "resource boundary")
                _require(set(resource) == {"name", "boundary"}, "Resource requires name/boundary")
                _text(resource.get("name"), "resource name")
                _text(resource.get("boundary"), "resource boundary")
            if "provider_context_path" in value:
                _core().safe_relative(
                    _text(value["provider_context_path"], "provider context path")
                )
            if "provider_source_mapping_path" in value:
                _core().safe_relative(
                    _text(value["provider_source_mapping_path"], "provider source mapping path")
                )
                _require(
                    "provider_context_path" in value,
                    "Provider source mapping requires expected context",
                )
        case "started" | "blocked" | "finished" | "canceled" | "finding" | "resolution":
            allowed |= {"request_id", "request_version"}
            _id(value.get("request_id"), "request ID")
            _require(
                type(value.get("request_version")) is int and value["request_version"] > 0,
                "Invalid request version",
            )
            match kind:
                case "started":
                    allowed |= {"method", "environment", "input_before"}
                case "blocked":
                    allowed |= {"blocker", "as_of"}
                    blocker = _record(value.get("blocker"), "blocker")
                    _require(
                        set(blocker) == {"reason", "resolver_id", "next_trigger"},
                        "Blocker requires reason/resolver/trigger",
                    )
                    _text(blocker.get("reason"), "blocker reason")
                    _text(blocker.get("next_trigger"), "blocker trigger")
                    if blocker["resolver_id"] is not None:
                        _actor_id(blocker["resolver_id"], "resolver")
                case "finished":
                    allowed |= {
                        "result",
                        "method",
                        "environment",
                        "input_before",
                        "input_after",
                        "exit_code",
                        "classification",
                        "policy_evidence",
                        "reuse",
                        "input_capture_before",
                        "input_capture_after",
                        "run_capture_ref",
                    }
                    _require(value.get("result") in RESULTS, "Invalid observed result")
                    method = _record(value.get("method"), "observation method")
                    _require(
                        set(method)
                        <= {"command", "argv", "cwd", "procedure", "execution_kind", "version"},
                        "Invalid observation method fields",
                    )
                    _require(
                        method.get("execution_kind")
                        in {"native", "test", "static", "manual", "compile"},
                        "Invalid observation execution kind",
                    )
                    _require(
                        bool(
                            method.get("command") or method.get("argv") or method.get("procedure")
                        ),
                        "Actual observation method required",
                    )
                    if "argv" in method:
                        _require(
                            bool(_items(method["argv"], "recorded argv"))
                            and type(method["argv"][0]) is str
                            and bool(method["argv"][0].strip())
                            and all(type(argument) is str for argument in method["argv"]),
                            "Invalid recorded argv",
                        )
                    for key in ("command", "cwd", "procedure"):
                        if key in method:
                            _text(method[key], key)
                    if "exit_code" in value:
                        _require(type(value["exit_code"]) is int, "Exit code must be integer")
                        _require(
                            value["result"] != "passed" or value["exit_code"] == 0,
                            "Nonzero exit cannot be passed",
                        )
                    _require(
                        bool(_texts(value.get("artifacts", []), "observation artifacts")),
                        "Finished observation requires output artifacts",
                    )
                    if value["result"] == "not_applicable":
                        _require(
                            bool(_texts(value.get("policy_evidence", []), "policy evidence")),
                            "Not applicable requires reviewed policy evidence",
                        )
                    for key in ("input_before", "input_after"):
                        if value.get(key) is not None:
                            _require(
                                bool(re.fullmatch(r"[a-f0-9]{64}", str(value[key]))),
                                f"Invalid {key}",
                            )
                    if (
                        value.get("input_before") is not None
                        and value.get("input_after") is not None
                    ):
                        _require(
                            value["result"] != "passed"
                            or value["input_before"] == value["input_after"],
                            "Observed inputs changed during verification",
                        )
                    if "reuse" in value:
                        reuse = _record(value["reuse"], "reuse origin")
                        _require(
                            set(reuse) == {"observation_id", "reason"},
                            "Reuse needs exact observation/reason",
                        )
                        _id(reuse["observation_id"], "reuse observation")
                        _text(reuse["reason"], "reuse reason")
                case "canceled":
                    allowed |= {"next_trigger"}
                    _text(value.get("next_trigger"), "cancellation trigger")
                case "finding":
                    allowed |= {
                        "statement",
                        "affected_paths",
                        "check_ids",
                        "criteria",
                        "next_action",
                    }
                    _text(value.get("statement"), "finding statement")
                    _text(value.get("next_action"), "finding next action")
                    _require(
                        bool(_texts(value.get("artifacts", []), "finding evidence")),
                        "Finding needs evidence",
                    )
                    for name in _texts(value.get("affected_paths"), "affected paths"):
                        core.safe_relative(name)
                case "resolution":
                    allowed |= {"finding_id", "observation_id", "next_action"}
                    _id(value.get("finding_id"), "original finding")
                    _id(value.get("observation_id"), "resolution observation")
                    _text(value.get("next_action"), "resolution next action")
                case _:
                    raise core.ProgramError("Invalid verification kind")
        case _:
            raise core.ProgramError("Invalid verification kind")
    _require(set(value) <= allowed, "Unknown verification input fields")
    if value.get("provider_input_mapping_path") is not None:
        _require(
            value.get("provider_context_path") is not None,
            "Executed input mapping requires provider context",
        )
    if "provider_ref" in value:
        _ref(value["provider_ref"])
    for name in ("input_capture_before", "input_capture_after", "run_capture_ref"):
        if name in value:
            _ref(value[name])
    _require(
        ("input_capture_before" in value) == ("input_capture_after" in value),
        "Both actual input capture references are required",
    )
    _time(value.get("observed_at"), "observed_at")
    _time(value.get("as_of"), "as_of")
    if value.get("observed_at") is not None or value.get("as_of") is not None:
        _id(value.get("clock_id"), "observed clock")
    if value.get("environment") is not None:
        _text(value["environment"], "environment identity")


@dataclass(slots=True)
class _Reads:
    """Mutable request-local bounded caches, never persistent freshness authority."""

    artifacts: ReadBudget
    sources: ReadBudget
    snapshots: dict[tuple[str, ...], Record] = field(default_factory=dict)
    contexts: dict[str, Record] = field(default_factory=dict)
    record_index: dict[str, Record] = field(default_factory=dict)
    latest_lane: dict[tuple[str, str], Record] = field(default_factory=dict)
    latest_event: dict[str, Record] = field(default_factory=dict)
    indexed: bool = False

    def index(self, state: Record) -> None:
        if self.indexed:
            return
        for item in _records(state):
            value = item["input"]
            self.record_index[str(value["id"])] = item
            if value["kind"] == "request":
                self.latest_lane[(str(value["task_id"]), str(value["lane"]))] = item
            if value["kind"] in {"request", "started", "blocked", "finished", "canceled"}:
                self.latest_event[str(item["request_id"])] = item
        self.indexed = True

    def snapshot(self, program: ProgramAccess, scope: list[str]) -> Record:
        key = tuple(sorted(scope))
        if key not in self.snapshots:
            self.snapshots[key] = program.source_snapshot(list(key), self.sources)
        return self.snapshots[key]

    def recheck(self, program: ProgramAccess) -> None:
        budget = _core().ReadBudget(128 * 1024 * 1024)
        for scope, snapshot in self.snapshots.items():
            _require(
                program.source_snapshot(list(scope), budget) == snapshot,
                "Verification source changed during observation; refresh",
            )


def _reads(budget: ReadBudget | None = None) -> _Reads:
    core = _core()
    return _Reads(
        budget if budget is not None else core.ReadBudget(64 * 1024 * 1024),
        core.ReadBudget(128 * 1024 * 1024),
    )


def _context(program: ProgramAccess, state: Record, task_id: str, reads: _Reads) -> Record:
    core = _core()
    if task_id in reads.contexts:
        return deepcopy(reads.contexts[task_id])
    task = program.task(state, task_id)
    attempts = _items(task.get("attempts"), "attempts")
    attempt = program.current_attempt(task) if attempts else None
    scope = _texts(
        attempt["scope"] if attempt is not None else task["candidate_paths"], "owned scope"
    )
    source = core.planning.source_for_task(state, task)
    source_bytes = reads.artifacts.read(core.safe_path(program.repo, str(source["path"])), LIMIT)
    _require(core.sha(source_bytes) == source["sha256"], "Frozen verification source drifted")
    section = _record(task["source"], "original section")
    _require(
        core.extract_sections(source_bytes).get(task_id) == section,
        "Original verification section changed",
    )
    submission = attempt.get("submission") if attempt is not None else None
    submitted = _record(submission, "submission") if submission is not None else None
    result = {
        "program_id": state["program_id"],
        "task_id": task_id,
        "definition_digest": core.digest(core.planning.task_definition(task)),
        "objective_binding": core.coverage.objective_binding(state)
        if state.get("objective") is not None
        else None,
        "attempt": attempt["number"] if attempt is not None else None,
        "round_count": len(_items(attempt["rounds"], "rounds")) if attempt is not None else 0,
        "submission": {
            "round": submitted["round"],
            "receipt": deepcopy(submitted["receipt"]),
            "source_digest": submitted["source_digest"],
        }
        if submitted is not None
        else None,
        "owned_write_scope": scope,
        "source_digest": core.digest(reads.snapshot(program, scope)),
        "required_checks": deepcopy(task["required_checks"]),
        "original_sha256": section["sha256"],
    }
    reads.contexts[task_id] = result
    return deepcopy(result)


def _capture(program: ProgramAccess, name: str, reads: _Reads) -> Record:
    core = _core()
    path = program.path(core.safe_relative(name))
    _require(name.startswith("evidence/") and path.is_file(), "Artifact must be program evidence")
    content = reads.artifacts.read(path, LIMIT)
    _require(bool(content), "Verification artifact cannot be empty")
    return {"path": name, "sha256": core.sha(content)}


def validate(state: Record) -> None:
    """Reject malformed supplied history without adding prerequisites to legacy programs."""
    seen: dict[str, Record] = {}
    folded: set[str] = set()
    versions: dict[tuple[str, str], int] = {}
    prior_requests: dict[tuple[str, str], Record] = {}
    audit = {}
    for event_value in _items(state.get("events", []), "events"):
        event = _record(event_value, "event")
        if event.get("action") == "verification-record":
            identifier = _id(event.get("verification_id"), "audit verification ID")
            _require(identifier not in audit, "Duplicate verification audit event")
            audit[identifier] = event
    for item in _records(state):
        _require(
            set(item)
            == {
                "input",
                "receipt",
                "context",
                "request_id",
                "request_version",
                "evidence",
                "recorded_at",
            },
            "Invalid verification record fields",
        )
        value = _record(item["input"], "verification input")
        _input_shape(value)
        identifier = str(value["id"])
        event = audit.get(identifier)
        _require(
            event is not None
            and event.get("receipt") == item["receipt"]
            and event.get("context_digest") == _core().digest(item["context"])
            and event.get("actor") == value["actor_id"]
            and event.get("kind") == value["kind"]
            and event.get("reason") == value["reason"],
            "Verification record differs from immutable audit context",
        )
        _require(identifier.casefold() not in folded, "Duplicate verification ID")
        folded.add(identifier.casefold())
        _ref(item["receipt"])
        refs = _items(item["evidence"], "verification evidence")
        _require(item["receipt"] in refs, "Verification input is not frozen evidence")
        for ref in refs:
            _ref(ref)
        context = _record(item["context"], "resolved verification context")
        _require(
            context.get("task_id") == value["task_id"]
            and context.get("program_id") == state["program_id"],
            "Verification context identity mismatch",
        )
        _require(
            context.get("attempt") is None
            or type(context["attempt"]) is int
            and context["attempt"] > 0,
            "Invalid contextual attempt",
        )
        _require(
            type(item["request_version"]) is int and item["request_version"] > 0,
            "Invalid stored request version",
        )
        _time(item["recorded_at"], "recorded_at")
        match value["kind"]:
            case "request":
                key = (str(value["task_id"]), str(value["lane"]))
                version = versions.get(key, 0) + 1
                _require(
                    item["request_id"] == identifier and item["request_version"] == version,
                    "Invalid request version chain",
                )
                previous = prior_requests.get(key)
                _require(
                    value.get("supersedes") == (previous["request_id"] if previous else None),
                    "Request supersession must identify exact prior lane request",
                )
                _require(
                    context.get("input_scope")
                    == sorted(set(_texts(value["input_scope"], "input scope"))),
                    "Stored input scope differs from authored request",
                )
                _require(
                    set(_texts(value["check_ids"], "request checks"))
                    <= set(_texts(context.get("required_checks"), "bound checks")),
                    "Stored request check obligations mismatch",
                )
                for field_name in (
                    "definition_digest",
                    "source_digest",
                    "input_digest",
                    "original_sha256",
                ):
                    _require(
                        bool(re.fullmatch(r"[a-f0-9]{64}", str(context.get(field_name)))),
                        f"Invalid stored {field_name}",
                    )
                for name in _texts(context["input_scope"], "input scope") + _texts(
                    context.get("owned_write_scope"), "owned scope"
                ):
                    _core().safe_relative(name)
                if "input_capture" in context:
                    captured = _inputs().validate_capture(context["input_capture"])
                    _require(
                        captured["input_digest"] == context["input_digest"],
                        "Stored capture digest mismatch",
                    )
                    _require(
                        captured["manifest"] == context["input_manifest"],
                        "Stored manifest mismatch",
                    )
                    _require(
                        _inputs().manifest_scope(captured["manifest"]) == context["input_scope"],
                        "Stored manifest scope mismatch",
                    )
                versions[key] = version
                prior_requests[key] = item
                if value.get("provider_context_path") is not None:
                    provider_ref = _ref(context.get("provider_context_ref"))
                    _require(
                        provider_ref in refs
                        and provider_ref["path"] == value["provider_context_path"],
                        "Provider expectation reference differs from frozen request",
                    )
                if value.get("provider_source_mapping_path") is not None:
                    source_ref = _ref(context.get("provider_source_mapping_ref"))
                    _require(
                        source_ref in refs
                        and source_ref["path"] == value["provider_source_mapping_path"],
                        "Provider source mapping reference differs from frozen request",
                    )
            case "started" | "blocked" | "finished" | "canceled" | "finding" | "resolution":
                request = seen.get(str(value["request_id"]))
                _require(
                    request is not None and request["input"]["kind"] == "request",
                    "Unknown original verification request",
                )
                _require(
                    item["request_id"] == request["request_id"]
                    and item["request_version"]
                    == value["request_version"]
                    == request["request_version"],
                    "Stale request version",
                )
                _require(
                    item["context"] == request["context"]
                    and value["task_id"] == request["input"]["task_id"],
                    "Observation rebound from original request",
                )
                if value["kind"] == "resolution":
                    finding = seen.get(str(value["finding_id"]))
                    observation = seen.get(str(value["observation_id"]))
                    _require(
                        finding is not None
                        and finding["input"]["kind"] == "finding"
                        and observation is not None
                        and observation["input"]["kind"] == "finished",
                        "Resolution requires exact finding and finished observation",
                    )
                    _require(
                        finding["input"]["task_id"] == value["task_id"]
                        and finding["context"]["attempt"] == item["context"]["attempt"]
                        and observation["context"] == item["context"],
                        "Resolution source/request identity mismatch",
                    )
            case _:
                raise _core().ProgramError("Invalid verification kind")
        seen[identifier] = item
    _require(set(audit) == set(seen), "Verification audit and history identities differ")


def evidence_references(state: Record) -> list[Json]:
    """Return every immutable verification input/context/artifact once for host budgets."""
    refs: dict[tuple[str, str], Record] = {}
    for item in _records(state):
        for value in _items(item["evidence"], "verification evidence"):
            ref = _ref(value)
            refs[(str(ref["path"]), str(ref["sha256"]))] = ref
    return list(refs.values())


def verify_history(program: ProgramAccess, state: Record, budget: ReadBudget | None = None) -> None:
    """Verify retained facts historically; never apply current source to old receipts."""
    validate(state)
    reader = budget if budget is not None else _core().ReadBudget(64 * 1024 * 1024)
    program.verify_refs(evidence_references(state), reader)
    for item in _records(state):
        ref = _ref(item["receipt"])
        raw = _core().read_json(program.path(str(ref["path"])), budget=reader)
        _require(raw == item["input"], "Frozen verification input metadata changed")
    for task_value in _items(state.get("tasks", []), "tasks"):
        task = _record(task_value, "task")
        for attempt_value in _items(task.get("attempts", []), "attempts"):
            attempt = _record(attempt_value, "attempt")
            historical = []
            for round_value in _items(attempt.get("rounds", []), "submission rounds"):
                round_record = _record(round_value, "submission round")
                historical.append(round_record["receipt"])
                if round_record.get("review") is not None:
                    historical.append(round_record["review"])
            for ref_value in historical:
                ref = _record(ref_value, "attempt evidence ref")
                name = str(ref["path"])
                receipt = _core().read_json(program.path(name), budget=reader)
                _retained_refs(state, receipt, str(task["id"]))


def _retained_refs(state: Record, receipt: Record, task_id: str) -> list[Record]:
    index = _index(state)
    result: list[Record] = []
    seen: set[str] = set()
    for value in _items(receipt.get("verification_refs", []), "verification reliance"):
        ref = _record(value, "verification reliance")
        _require(set(ref) == {"id", "receipt"}, "Invalid verification reliance fields")
        identifier = _id(ref.get("id"), "relied observation")
        _require(
            identifier not in seen and identifier in index,
            "Unknown/duplicate verification reliance",
        )
        seen.add(identifier)
        item = index[identifier]
        _require(
            item["receipt"] == _ref(ref.get("receipt")), "Verification reliance receipt mismatch"
        )
        _require(
            item["input"]["task_id"] == task_id and item["input"]["kind"] == "finished",
            "Verification reliance requires same-task finished observation",
        )
        _require(
            type(receipt.get("attempt")) is int
            and item["context"]["attempt"] == receipt["attempt"],
            "Verification reliance attempt mismatch",
        )
        result.append(item)
    return result


def _compatible(original: Record, current: Record, *, submitted: bool = False) -> bool:
    keys = {
        "program_id",
        "task_id",
        "definition_digest",
        "objective_binding",
        "attempt",
        "owned_write_scope",
        "source_digest",
        "required_checks",
        "original_sha256",
    }
    if not submitted:
        keys |= {"round_count", "submission"}
    return all(original.get(key) == current.get(key) for key in keys)


def _carried_by_submission(
    program: ProgramAccess, item: Record, current: Record, reads: _Reads
) -> bool:
    submission = current.get("submission")
    if submission is None or item["input"]["kind"] != "finished":
        return False
    ref = _record(submission, "current submission")["receipt"]
    program.verify_refs([ref], reads.artifacts)
    receipt = _core().read_json(program.path(str(ref["path"])), budget=reads.artifacts)
    return {"id": item["input"]["id"], "receipt": item["receipt"]} in _items(
        receipt.get("verification_refs", []), "submission verification reliance"
    )


def _applicable(
    program: ProgramAccess, state: Record, item: Record, reads: _Reads, *, submitted: bool = False
) -> tuple[bool, list[str]]:
    core = _core()
    reads.index(state)
    request = reads.record_index[str(item["request_id"])]
    value = _record(request["input"], "request")
    current = _context(program, state, str(value["task_id"]), reads)
    submitted = submitted or _carried_by_submission(program, item, current, reads)
    reasons: list[str] = []
    if not _compatible(_record(item["context"], "context"), current, submitted=submitted):
        reasons.append("Task, attempt, submission, source or obligation context changed")
    scope = _texts(value["input_scope"], "input scope")
    if "input_capture" in request["context"]:
        reads.snapshot(program, scope)
        comparison = _input_comparison(program, request)
        if comparison["status"] != "unchanged":
            reasons.extend(comparison["reasons"] or ["Verification input completeness unknown"])
    elif core.digest(reads.snapshot(program, scope)) != request["context"].get("input_digest"):
        reasons.append("Verification input scope changed")
    latest_lane = reads.latest_lane.get((str(value["task_id"]), str(value["lane"])))
    if latest_lane is not None and latest_lane["request_id"] != request["request_id"]:
        reasons.append("Verification request was superseded")
    latest_event = reads.latest_event.get(str(request["request_id"]))
    if (
        item["input"]["kind"] == "finished"
        and latest_event is not None
        and latest_event["input"]["id"] != item["input"]["id"]
    ):
        reasons.append("Newer verification event prevents old result reuse")
    return not reasons, reasons


def _provider_source_mapping(
    program: ProgramAccess, value: Record, context: Record, expected: Record, reads: _Reads
) -> list[Json]:
    name = value.get("provider_source_mapping_path")
    _require(
        name is not None or expected["pr"]["head_sha"] == context["observed_head"],
        "Distinct provider HEAD requires frozen source-equivalence mapping",
    )
    if name is None:
        return []
    ref = _capture(program, _text(name, "provider source mapping path"), reads)
    mapping = _core().read_json(program.path(str(ref["path"])), budget=reads.artifacts)
    _require(
        set(mapping)
        == {
            "schema_version",
            "provider_head",
            "scope",
            "provider_snapshot",
            "local_snapshot",
            "evidence",
        }
        and type(mapping["schema_version"]) is int
        and mapping["schema_version"] == 1,
        "Invalid provider source mapping schema",
    )
    scope = _texts(mapping["scope"], "mapped source scope")
    _require(
        mapping["provider_head"] == expected["pr"]["head_sha"]
        and sorted(scope) == context["owned_write_scope"],
        "Provider source mapping head/scope mismatch",
    )
    actual = reads.snapshot(program, scope)
    _require(
        _record(mapping["provider_snapshot"], "provider source snapshot")
        == _record(mapping["local_snapshot"], "local source snapshot")
        == actual,
        "Provider source equivalence differs from actual scoped files",
    )
    _require(
        _core().digest(actual) == context["source_digest"],
        "Mapped source digest differs from request",
    )
    names = _texts(mapping["evidence"], "comparison evidence")
    _require(bool(names), "Source equivalence requires immutable comparison evidence")
    context["provider_source_mapping_ref"] = ref
    return [ref, *[_capture(program, path, reads) for path in names]]


def prepare_record(
    program: ProgramAccess, state: Record, input_path: str, *, observed_head: str
) -> PreparedChange:
    """Resolve and freeze one detached event with the real pre-publication guard."""
    core = _core()
    program.require_current_location()
    program.validate(state)
    validate(state)
    _require(bool(re.fullmatch(r"[a-f0-9]{40}", observed_head)), "Invalid observed source HEAD")
    _require(
        input_path.startswith("evidence/_verification/attempt-1/"),
        "Verification input requires private immutable verification evidence",
    )
    value, captured = core.read_json_frozen(program.path(input_path), limit=LIMIT)
    _input_shape(value)
    _require(
        value["expected_revision"] == core.phases.revision(state),
        "Stale verification registry revision",
    )
    index = _index(state)
    _require(
        all(identifier.casefold() != str(value["id"]).casefold() for identifier in index),
        "Duplicate verification ID",
    )
    _require(len(index) < MAX_RECORDS, "Verification history exceeds 2000 records")
    task = program.task(state, str(value["task_id"]))
    reads = _reads()
    request = None if value["kind"] == "request" else index.get(str(value["request_id"]))
    if value["kind"] != "request":
        _require(
            request is not None and request["input"]["kind"] == "request",
            "Unknown verification request",
        )
        _require(
            value["request_version"] == request["request_version"]
            and value["task_id"] == request["input"]["task_id"],
            "Verification request identity mismatch",
        )
    _authority(state, task, value, request)
    refs: list[Json] = [{"path": input_path, "sha256": captured}]
    if request is None:
        _require(
            task.get("disposition") not in {"split", "retired"},
            "Disposed task cannot receive verification request",
        )
        if state.get("objective") is not None:
            _require(
                value["task_id"] in core.coverage.objective_binding(state)["member_ids"],
                "Task excluded from current objective",
            )
        _require(
            set(_texts(value["check_ids"], "check IDs"))
            <= set(_texts(task["required_checks"], "required checks")),
            "Request check outside current obligations",
        )
        _criteria(state, value.get("criteria", []))
        context = _context(program, state, str(value["task_id"]), reads)
        scope = sorted(
            set(core.safe_relative(path) for path in _texts(value["input_scope"], "input scope"))
        )
        context.update(
            {
                "input_scope": scope,
                "input_digest": core.digest(reads.snapshot(program, scope)),
                "observed_head": observed_head,
            }
        )
        manifest = value.get("input_manifest")
        if value.get("manifest_path") is not None:
            ref = _capture(program, str(value["manifest_path"]), reads)
            manifest = core.read_json(program.path(str(ref["path"])), budget=reads.artifacts)
            refs.append(ref)
        captured = None
        if value.get("input_capture_path") is not None:
            ref = _capture(program, str(value["input_capture_path"]), reads)
            captured = _inputs().validate_capture(
                core.read_json(program.path(str(ref["path"])), budget=reads.artifacts)
            )
            _require(
                manifest is None or _inputs().normalize_manifest(manifest) == captured["manifest"],
                "Manifest differs from frozen capture",
            )
            manifest = captured["manifest"]
            context["input_capture_ref"] = ref
            refs.append(ref)
        if manifest is not None:
            parsed = _inputs().normalize_manifest(manifest)
            _require(
                _inputs().manifest_scope(parsed) == scope,
                "Manifest differs from declared public input scope",
            )
            _require(
                parsed["checks"] == sorted(value["check_ids"]), "Manifest check obligations differ"
            )
            current_capture = _inputs().capture_inputs(
                program,
                parsed,
                environment=captured["environment"]
                if captured
                else value.get("environment_identity"),
                method=captured["method"] if captured else value.get("method_identity"),
            )
            _require(
                captured is None or current_capture["input_digest"] == captured["input_digest"],
                "Frozen input capture is stale",
            )
            context.update(
                input_manifest=parsed,
                input_capture=current_capture,
                input_digest=current_capture["input_digest"],
            )
            refs.extend(parsed["artifact_refs"] + parsed["completeness"]["evidence_refs"])
        if value.get("provider_context_path") is not None:
            provider_context_ref = _capture(
                program, _text(value["provider_context_path"], "provider context path"), reads
            )
            provider_context = core.read_json(
                program.path(str(provider_context_ref["path"])), budget=reads.artifacts
            )
            try:
                expected_provider = _provider().validate_context(provider_context)
            except _provider().DeliveryError as exc:
                raise core.ProgramError(str(exc)) from exc
            refs.extend(_provider_source_mapping(program, value, context, expected_provider, reads))
            _require(
                "input_capture" not in context
                or value.get("provider_input_mapping_path") is not None,
                "Manifest provider execution requires exact tested input mapping",
            )
            if value.get("provider_input_mapping_path") is not None:
                mapping_ref = _capture(program, str(value["provider_input_mapping_path"]), reads)
                mapping = core.read_json(
                    program.path(str(mapping_ref["path"])), budget=reads.artifacts
                )
                _require(
                    set(mapping) == {"schema_version", "tested_revision", "snapshots", "evidence"},
                    "Invalid provider executed input mapping",
                )
                _require(
                    type(mapping["schema_version"]) is int
                    and mapping["schema_version"] == 1
                    and mapping["tested_revision"] == expected_provider["pr"]["tested_sha"],
                    "Provider executed input revision differs",
                )
                _require("input_capture" in context, "Provider input mapping requires manifest")
                tested = _inputs().capture_git_inputs(
                    program,
                    context["input_manifest"],
                    revision=str(mapping["tested_revision"]),
                    environment=context["input_capture"]["environment"],
                    method=context["input_capture"]["method"],
                )
                _require(
                    mapping["snapshots"]
                    == tested["snapshots"]
                    == context["input_capture"]["snapshots"],
                    "Provider executed source/test/config inputs differ",
                )
                refs.extend(
                    [
                        mapping_ref,
                        *[
                            _capture(program, name, reads)
                            for name in _texts(mapping["evidence"], "input mapping evidence")
                        ],
                    ]
                )
                _require(bool(mapping["evidence"]), "Provider input mapping needs evidence")
                context["provider_input_mapping_ref"] = mapping_ref
            context["provider_context_ref"] = provider_context_ref
            refs.append(provider_context_ref)
            for stage in expected_provider["stages"]:
                for check in stage["checks"]:
                    for name in check["policy_evidence"]:
                        refs.append(_capture(program, name, reads))
        number = context["attempt"]
        destination = str(value["evidence_destination"])
        prefix = (
            f"evidence/{value['task_id']}/attempt-{number}/" if number is not None else "evidence/"
        )
        _require(
            destination.startswith(prefix), "Evidence destination must match actual task/attempt"
        )
        if value.get("context_path") is not None:
            context_ref = _capture(program, str(value["context_path"]), reads)
            packet_value = core.read_json(
                program.path(str(value["context_path"])), budget=reads.artifacts
            )
            _require(
                _compatible(_record(packet_value.get("context"), "packet context"), context),
                "Frozen packet context is stale",
            )
            refs.append(context_ref)
        prior = [
            entry
            for entry in index.values()
            if entry["input"]["kind"] == "request"
            and entry["input"]["task_id"] == value["task_id"]
            and entry["input"]["lane"] == value["lane"]
        ]
        _require(
            value.get("supersedes") == (prior[-1]["request_id"] if prior else None),
            "Supersedes must identify exact latest lane request",
        )
        request_id = value["id"]
        version = len(prior) + 1
    else:
        context = deepcopy(_record(request["context"], "request context"))
        request_id, version = request["request_id"], request["request_version"]
        if value["kind"] in {"finding", "resolution"}:
            _criteria(state, value.get("criteria", []))
        if value["kind"] == "finding":
            _require(
                set(_texts(value.get("check_ids", []), "finding checks"))
                <= set(_texts(request["input"]["check_ids"], "request checks")),
                "Finding check identity mismatch",
            )
            for path in _texts(value["affected_paths"], "affected paths"):
                core.safe_path(program.repo, path)
        if (
            value["kind"] == "finished"
            and value.get("input_before") is not None
            and value["result"] == "passed"
        ):
            _require(
                value["input_before"] == context.get("input_digest"),
                "Observed input differs from frozen request",
            )
        if value["kind"] == "finished" and "input_capture_before" in value:
            _require("input_capture" in context, "Actual captures require adopted input manifest")
            for name in ("input_capture_before", "input_capture_after"):
                ref = _ref(value[name])
                _require(
                    context["attempt"] is None
                    or str(ref["path"]).startswith(
                        f"evidence/{value['task_id']}/attempt-{context['attempt']}/"
                    ),
                    "Actual capture reference must be task-local",
                )
                program.verify_refs([ref], reads.artifacts)
                actual = _inputs().validate_capture(
                    core.read_json(program.path(str(ref["path"])), budget=reads.artifacts)
                )
                _require(
                    value["result"] != "passed"
                    or actual["input_digest"] == context["input_digest"],
                    "Passing actual capture differs from request",
                )
                _require(
                    actual["method"] == value["method"],
                    "Actual capture method differs from observation",
                )
                refs.extend(
                    [ref, *actual["artifact_refs"], *actual["completeness"]["evidence_refs"]]
                )
        if value["kind"] == "finished" and "run_capture_ref" in value:
            ref = _ref(value["run_capture_ref"])
            program.verify_refs([ref], reads.artifacts)
            run = core.read_json(program.path(str(ref["path"])), budget=reads.artifacts)
            _require(
                run.get("producer") == "feature_program_execution",
                "Invalid actual execution producer",
            )
            if "input_capture_before" in value:
                _require(
                    run.get("input_capture_before") is not None
                    and run.get("input_capture_after") is not None
                    and run["input_capture_before"]["sha256"]
                    == value["input_capture_before"]["sha256"]
                    and run["input_capture_after"]["sha256"]
                    == value["input_capture_after"]["sha256"],
                    "Execution origin input captures differ",
                )
            for name in ("input_capture_before", "input_capture_after"):
                if run.get(name) is not None:
                    origin_ref = _ref(run[name])
                    program.verify_refs([origin_ref], reads.artifacts)
                    refs.append(origin_ref)
            for output in _record(run.get("outputs", {}), "execution output refs").values():
                if output.get("path") is None:
                    continue
                origin_ref = _ref({"path": output["path"], "sha256": output["sha256"]})
                program.verify_refs([origin_ref], reads.artifacts)
                refs.append(origin_ref)
            if run.get("task_id") != value["task_id"] or run.get("request_id") != request_id:
                bindings = attachment_bindings(
                    program,
                    state,
                    [str(value["task_id"])],
                    ref,
                    actor_id=str(value["actor_id"]),
                    reporter_id=value.get("reporter_id"),
                )
                _require(
                    bindings["items"][0]["status"] == "attachment_candidate",
                    "Execution cannot supply compatible target task evidence",
                )
            if run.get("task_id") == value["task_id"] and run.get("request_id") == request_id:
                _require(
                    run.get("request_receipt") == request["receipt"]
                    and run.get("request_context_digest") == core.digest(request["context"])
                    and run.get("attempt") == context["attempt"],
                    "Actual execution request context differs",
                )
            if run.get("start_ref") is not None:
                origin_ref = _ref(run["start_ref"])
                program.verify_refs([origin_ref], reads.artifacts)
                refs.append(origin_ref)
            for artifact in _items(
                _record(run.get("artifacts", {}), "execution artifacts").get("captured", []),
                "captured execution artifacts",
            ):
                origin_ref = _ref({"path": artifact["path"], "sha256": artifact["sha256"]})
                program.verify_refs([origin_ref], reads.artifacts)
                refs.append(origin_ref)
            summary = _execution().summarize_capture(run)
            _require(
                value["result"] != "passed" or summary["reported_check_result"] == "passed",
                "Incomplete or failed execution cannot become passed",
            )
            refs.append(ref)
        if value["kind"] == "finished" and "reuse" in value:
            origin = index.get(str(value["reuse"]["observation_id"]))
            _require(origin is not None, "Unknown reuse origin")
            target = {"input": value, "context": context, "request_id": request_id}
            _require(
                assess_reuse(program, state, origin, target)["status"] == "reuse_candidate",
                "Reuse inputs are changed or unassessed",
            )
        started = [
            entry["input"]
            for entry in index.values()
            if entry["request_id"] == request_id and entry["input"]["kind"] == "started"
        ]
        if started and value["kind"] == "finished":
            start, end = (
                _time(started[-1].get("observed_at"), "started_at"),
                _time(value.get("observed_at"), "finished_at"),
            )
            if (
                start is not None
                and end is not None
                and started[-1].get("clock_id") == value.get("clock_id")
            ):
                _require(end >= start, "Observed interval is reversed")
    artifact_names = _texts(value.get("artifacts", []), "artifacts") + _texts(
        value.get("policy_evidence", []), "policy evidence"
    )
    for name in artifact_names:
        if value["kind"] == "finished" and context["attempt"] is not None:
            _require(
                name.startswith(f"evidence/{value['task_id']}/attempt-{context['attempt']}/"),
                "Check artifact must be task/attempt-specific",
            )
        refs.append(_capture(program, name, reads))
    if value.get("provider_ref") is not None:
        _require(
            context.get("provider_context_ref") is not None,
            "Provider observation requires explicit frozen expected context",
        )
        ref = _ref(value["provider_ref"])
        program.verify_refs([ref], reads.artifacts)
        refs.append(ref)
        _provider_assessment(program, {"context": context}, {"input": value}, reads, "current")
    item = {
        "input": deepcopy(value),
        "receipt": refs[0],
        "context": context,
        "request_id": request_id,
        "request_version": version,
        "evidence": refs,
        "recorded_at": core.utc(),
    }
    proposal = deepcopy(state)
    extension = proposal.setdefault("verification", {"schema_version": 1, "records": []})
    _items(_record(extension, "verification")["records"], "records").append(item)
    program.event(
        proposal,
        "verification-record",
        {
            "id": value["task_id"],
            "verification_id": value["id"],
            "kind": value["kind"],
            "actor": value["actor_id"],
            "reason": value["reason"],
            "receipt": refs[0],
            "context_digest": core.digest(context),
        },
    )
    validate(proposal)
    program.verify_refs(refs)
    original_revision = core.phases.revision(state)

    def before_publish() -> None:
        current = core.read_json(program.path("backlog.json"))
        _require(
            core.phases.revision(current) == original_revision,
            "Verification registry changed before publication",
        )
        _authority(current, program.task(current, str(value["task_id"])), value, request)
        program.require_current_location()
        program.verify_refs(refs)
        reads.recheck(program)

    reads.recheck(program)
    return core.PreparedChange(proposal, before_publish)


def _reference(state: Record, pointer: str, task_id: str | None = None) -> Record:
    result: Record = {
        "path": "backlog.json",
        "revision": _core().phases.revision(state),
        "pointer": pointer,
    }
    if task_id is not None:
        result["task_id"] = task_id
    return result


def _collection(
    state: Record,
    rows: list[Json],
    pointer: str,
    task_id: str | None = None,
    *,
    limit: int | None = MAX_ROWS,
) -> Record:
    return {
        "total": len(rows),
        "shown": len(rows) if limit is None else min(len(rows), limit),
        "omitted": 0 if limit is None else max(0, len(rows) - limit),
        "items": rows[:] if limit is None else rows[:limit],
        "reference": _reference(state, pointer, task_id),
    }


def packet(
    program: ProgramAccess, state: Record, task_id: str, *, observed_head: str | None = None
) -> Record:
    """Prepare exact current context without claim, execution, acceptance or future round."""
    program.require_current_location()
    program.validate(state)
    reads = _reads()
    context = _context(program, state, task_id, reads)
    task = program.task(state, task_id)
    result = {
        "schema_version": 1,
        "status": "not_assessed",
        "acceptance_complete": False,
        "observed_revision": _core().phases.revision(state),
        "observed_head": observed_head,
        "context": context,
        "original": deepcopy(task["source"]),
        "obligations": {
            "check_ids": deepcopy(task["required_checks"]),
            "criteria": deepcopy(context["objective_binding"]["criteria"])
            if context["objective_binding"] is not None
            else [],
            "task_criterion_extraction": "unknown",
        },
        "verification": projection(program, state, reads.artifacts)["tasks"].get(task_id),
        "reference": _reference(state, "/tasks", task_id),
    }
    reads.recheck(program)
    _require(len(_core().canonical(result)) <= OUTPUT_LIMIT, "Verification packet exceeds 8 MiB")
    return result


def assess_reuse(program: ProgramAccess, state: Record, origin: Record, target: Record) -> Record:
    """Conservatively compare complete explicit check inputs; never waive fresh CI/review."""
    reads = _reads()
    index = _index(state)
    old_request = index.get(str(origin.get("request_id")))
    new_request = index.get(str(target.get("request_id")))
    reasons: list[str] = []
    if old_request is None or new_request is None:
        return {
            "status": "not_assessed",
            "reasons": ["Exact request inputs unavailable"],
            "advisory": True,
        }
    old, new = old_request["input"], new_request["input"]
    if str(new["lane"]).casefold() in {"pr_ci", "beta_ci", "ci"}:
        reasons.append("Actual PR and beta CI may not be waived by local reuse")
    if old.get("input_complete") is not True or new.get("input_complete") is not True:
        return {
            "status": "not_assessed",
            "reasons": ["Complete verification input closure unknown"],
            "advisory": True,
        }
    before, after = origin["input"], target["input"]
    reads.index(state)
    latest_origin = reads.latest_event.get(str(origin["request_id"]))
    if latest_origin is not None and latest_origin["input"]["id"] != before["id"]:
        reasons.append("Newer origin event prevents reuse of old success")
    origin_seen = False
    for newer in _records(state):
        if newer["input"]["id"] == before["id"]:
            origin_seen = True
            continue
        if not origin_seen or newer["input"]["kind"] not in {
            "started",
            "blocked",
            "finished",
            "canceled",
        }:
            continue
        newer_request = index[str(newer["request_id"])]["input"]
        relevant = (
            newer_request["task_id"] == new["task_id"]
            and newer_request["lane"] == new["lane"]
            and bool(set(newer_request["check_ids"]) & set(new["check_ids"]))
        )
        if not relevant:
            continue
        if newer["input"]["kind"] != "finished" or newer["input"].get("result") != "passed":
            reasons.append(
                "Newer relevant request has failed, canceled or unavailable verification"
            )
            break
        if _observation_problems(newer, index[str(newer["request_id"])]):
            reasons.append("Newer relevant observation has incomplete verification inputs")
            break
        try:
            program.verify_refs(
                _items(newer["evidence"], "newer verification evidence"), reads.artifacts
            )
        except (OSError, _core().ProgramError):
            reasons.append("Newer relevant verification evidence is unavailable")
            break
    if before.get("environment") is None or after.get("environment") is None:
        return {
            "status": "not_assessed",
            "reasons": ["Runtime environment identity unknown"],
            "advisory": True,
        }
    for key in ("environment", "method"):
        if before.get(key) != after.get(key):
            reasons.append(f"Observation {key} changed")
    for key in ("check_ids", "criteria", "input_scope"):
        if old.get(key, []) != new.get(key, []):
            reasons.append(f"Verification {key} changed")
    for key in ("definition_digest", "objective_binding", "input_digest", "original_sha256"):
        if origin["context"].get(key) != target["context"].get(key):
            reasons.append(f"Bound {key} changed")
    if (
        before.get("result") != "passed"
        or before.get("input_before") is None
        or before.get("input_before") != before.get("input_after")
    ):
        reasons.append("Origin lacks passing complete stable input observation")
    program.verify_refs(_items(origin["evidence"], "origin evidence"), reads.artifacts)
    current_input = (
        _inputs().capture_inputs(
            program,
            new_request["context"]["input_manifest"],
            environment=new_request["context"]["input_capture"]["environment"],
            method=new_request["context"]["input_capture"]["method"],
        )["input_digest"]
        if "input_capture" in new_request["context"]
        else _core().digest(reads.snapshot(program, _texts(new["input_scope"], "input scope")))
    )
    if current_input != target["context"].get("input_digest"):
        reasons.append("Current verification inputs changed")
    reads.recheck(program)
    return {
        "status": "recheck_required" if reasons else "reuse_candidate",
        "reasons": reasons,
        "advisory": True,
    }


def _provider_assessment(
    program: ProgramAccess, request: Record, observation: Record, reads: _Reads, applicability: str
) -> Record:
    expected_ref = request["context"].get("provider_context_ref")
    captured_ref = observation["input"].get("provider_ref")
    if expected_ref is None and captured_ref is None:
        return {"adopted": False, "status": "not_assessed"}
    _require(expected_ref is not None, "Provider observation lacks explicit expected context")
    program.verify_refs([_ref(expected_ref)], reads.artifacts)
    expected = _core().read_json(program.path(str(expected_ref["path"])), budget=reads.artifacts)
    try:
        _provider().validate_context(expected)
        if captured_ref is None:
            return {
                "adopted": True,
                "status": "pending",
                "stages": [],
                "reasons": ["Provider capture is not recorded on latest observation"],
                "remote_verified_by_tool": False,
                "applicability": applicability,
                "current": applicability == "current",
                "provider_context_reference": deepcopy(expected_ref),
                "provider_reference": None,
            }
        program.verify_refs([_ref(captured_ref)], reads.artifacts)
        captured = _core().read_json(
            program.path(str(captured_ref["path"])), budget=reads.artifacts
        )
        result = _provider().assess_execution(captured.get("execution"), expected)
    except _provider().DeliveryError as exc:
        raise _core().ProgramError(str(exc)) from exc
    result.update(
        {
            "adopted": True,
            "applicability": applicability,
            "current": applicability == "current",
            "provider_context_reference": deepcopy(expected_ref),
            "provider_reference": deepcopy(captured_ref),
        }
    )
    if applicability != "current":
        result["recorded_status"] = result["status"]
        if result["status"] == "passed":
            result["status"] = "not_assessed"
        result["reasons"].append("Local verification source/context is not currently applicable")
        for stage in result["stages"]:
            stage["current"] = False
            if stage["status"] == "passed":
                stage["recorded_status"], stage["status"] = "passed", "not_assessed"
    if observation["input"].get("kind") == "canceled" or observation["input"].get("result") in {
        "failed",
        "unknown",
        "not_run",
    }:
        result["recorded_status"], result["status"] = result["status"], "failed"
        result["reasons"].append("Latest local observation is canceled or nonpassing")
    return result


def _waiting(events: list[Record]) -> Record:
    boundary = None
    ending = None
    for item in events:
        value = item["input"]
        match value["kind"]:
            case "request" | "blocked":
                boundary, ending = value, None
            case "started" | "finished" | "canceled" | "resolution":
                if boundary is not None and ending is None:
                    ending = value
            case "finding":
                continue
            case _:
                raise _core().ProgramError("Invalid waiting event")
    start_text = boundary.get("observed_at") if boundary is not None else None
    end_text = ending.get("observed_at") if ending is not None else None
    as_of = boundary.get("as_of") if boundary is not None and ending is None else None
    clock = boundary.get("clock_id") if boundary is not None else None
    compatible = clock is not None and (ending is None or ending.get("clock_id") == clock)
    start, end = _time(start_text, "waiting start"), _time(end_text or as_of, "waiting end")
    seconds = (
        (end - start).total_seconds()
        if compatible and start is not None and end is not None and end >= start
        else None
    )
    return {
        "status": "observed" if seconds is not None else "unknown",
        "started_at": start_text,
        "ended_at": end_text,
        "as_of": as_of,
        "clock_id": clock,
        "seconds": seconds,
    }


def _observation_problems(
    item: Record, request: Record, *, include_result: bool = True
) -> list[str]:
    value = item["input"]
    assigned = request["input"]
    problems = []
    if assigned.get("input_complete") is not True:
        problems.append("Complete verification input closure unknown")
    captured = request["context"].get("input_capture")
    if captured is not None:
        if captured["completeness"]["status"] != "complete":
            problems.append("Declared categorized inputs or environment incomplete")
        if value.get("input_capture_before") is None or value.get("input_capture_after") is None:
            problems.append("Actual categorized before/after input captures missing")
    if (
        captured is None
        and assigned.get("environment") is not None
        and value.get("environment") != assigned["environment"]
    ):
        problems.append("Recorded environment differs from request")
    if (
        value.get("input_before") is None
        or value.get("input_before") != value.get("input_after")
        or value.get("input_before") != request["context"]["input_digest"]
    ):
        problems.append("Executed input identity incomplete or incompatible")
    if include_result and value.get("result") != "passed":
        problems.append("Recorded verification did not pass")
    for check in _texts(assigned["check_ids"], "request checks"):
        if (
            check in {"native_editor", "native_player"}
            and value["method"]["execution_kind"] != "native"
        ):
            problems.append("Native execution cannot be replaced by compilation")
    return problems


def attachment_bindings(
    program: ProgramAccess,
    state: Record,
    target_task_ids: list[str],
    run_capture_ref: Record,
    *,
    actor_id: str | None = None,
    reporter_id: Json = None,
) -> Record:
    """Assess explicit task-local attachment targets without copying or accepting them."""
    core = _core()
    validate(state)
    reads = _reads()
    reads.index(state)
    ref = _ref(run_capture_ref)
    program.verify_refs([ref], reads.artifacts)
    run = core.read_json(program.path(str(ref["path"])), budget=reads.artifacts)
    _require(
        run.get("producer") == "feature_program_execution" and run.get("schema_version") == 1,
        "Invalid execution capture origin",
    )
    origin = reads.record_index.get(str(run.get("request_id")))
    _require(
        origin is not None
        and origin["input"]["kind"] == "request"
        and origin["input"]["task_id"] == run.get("task_id")
        and origin["request_version"] == run.get("request_version"),
        "Execution origin request identity differs",
    )
    _require(
        run.get("request_receipt") == origin["receipt"]
        and run.get("request_context_digest") == core.digest(origin["context"])
        and run.get("attempt") == origin["context"]["attempt"],
        "Original execution request context differs",
    )
    capture_refs = [
        _ref(run[name])
        for name in ("input_capture_before", "input_capture_after")
        if run.get(name) is not None
    ]
    program.verify_refs(capture_refs, reads.artifacts)
    before, after = None, None
    if len(capture_refs) == 2:
        before = _inputs().validate_capture(
            core.read_json(program.path(str(capture_refs[0]["path"])), budget=reads.artifacts)
        )
        after = _inputs().validate_capture(
            core.read_json(program.path(str(capture_refs[1]["path"])), budget=reads.artifacts)
        )
    comparison = (
        _inputs().compare_inputs(before, after)
        if before is not None and after is not None
        else {"status": "not_assessed"}
    )
    items = []
    selected = core.strings(target_task_ids, "attachment targets")
    _require(bool(selected), "Explicit attachment targets required")
    for task_id in selected:
        reasons = []
        task = program.task(state, task_id)
        request = reads.latest_lane.get((task_id, str(origin["input"]["lane"])))
        authority_checked = False
        context = _context(program, state, task_id, reads)
        if request is None or "input_capture" not in request["context"]:
            reasons.append("Explicit target input request unavailable")
        else:
            try:
                _authority(
                    state,
                    task,
                    {
                        "actor_id": actor_id or run["actor_id"],
                        "kind": "finished",
                        "reporter_id": reporter_id,
                    },
                    request,
                )
                authority_checked = True
            except core.ProgramError as exc:
                reasons.append(str(exc))
            if not _compatible(
                request["context"],
                context,
                submitted=_carried_by_submission(program, request, context, reads),
            ):
                reasons.append("Target task source, submission or obligations changed")
            if request["input"]["check_ids"] != origin["input"]["check_ids"] or request[
                "input"
            ].get("criteria", []) != origin["input"].get("criteria", []):
                reasons.append("Target check or criterion mapping differs")
            target_capture = request["context"]["input_capture"]
            if (
                before is None
                or target_capture["input_digest"] != before["input_digest"]
                or _input_comparison(program, request)["status"] != "unchanged"
            ):
                reasons.append("Target executed input closure changed or unknown")
            latest = reads.latest_event[str(request["request_id"])]["input"]
            if (
                latest["kind"] in {"blocked", "canceled"}
                or latest["kind"] == "finished"
                and latest.get("result") != "passed"
            ):
                reasons.append("Target has newer adverse verification")
        if (
            comparison["status"] != "unchanged"
            or before is None
            or before["input_digest"] != origin["context"].get("input_digest")
        ):
            reasons.append("Actual run input closure changed or unknown")
        if request is not None and before is not None:
            problems = _observation_problems(
                {
                    "input": {
                        "result": "passed",
                        "method": before["method"],
                        "input_before": before["input_digest"],
                        "input_after": after["input_digest"] if after else None,
                        "input_capture_before": run.get("input_capture_before"),
                        "input_capture_after": run.get("input_capture_after"),
                    },
                },
                request,
            )
            reasons.extend(problems)
        if _execution().summarize_capture(run)["reported_check_result"] != "passed":
            reasons.append("Actual run did not provide complete passing evidence")
        origin_latest = reads.latest_event[str(origin["request_id"])]["input"]
        latest_origin_request = reads.latest_lane[
            (str(origin["input"]["task_id"]), str(origin["input"]["lane"]))
        ]
        if (
            latest_origin_request["request_id"] != origin["request_id"]
            or origin_latest["kind"] in {"blocked", "canceled"}
            or origin_latest["kind"] == "finished"
            and origin_latest.get("result") != "passed"
        ):
            reasons.append("Origin has newer superseding or adverse verification")
        items.append(
            {
                "task_id": task_id,
                "attempt": context["attempt"],
                "status": "not_assessed" if reasons else "attachment_candidate",
                "requirements": [
                    "Exact-byte task-local attachments",
                    "Normal per-task recording and independent acceptance",
                ],
                "reasons": reasons,
                "context": context,
                "evidence_refs": [ref, *capture_refs],
                "authority_checked": authority_checked,
                "request_id": request["request_id"] if request else None,
                "request_version": request["request_version"] if request else None,
                "verifier_id": request["input"]["verifier_id"] if request else None,
                "actor_id": actor_id or run["actor_id"],
            }
        )
    reads.recheck(program)
    return {
        "schema_version": 1,
        "status": "attachment_candidates"
        if all(item["status"] == "attachment_candidate" for item in items)
        else "not_assessed",
        "advisory": True,
        "items": items,
    }


def _input_comparison(program: ProgramAccess, request: Record) -> Record:
    captured = request["context"]["input_capture"]
    current = _inputs().capture_inputs(
        program,
        captured["manifest"],
        environment=captured["environment"],
        method=captured["method"],
    )
    return _inputs().compare_inputs(captured, current)


def _input_summary(program: ProgramAccess, request: Record, applicability: str) -> Record:
    comparison = _input_comparison(program, request)
    captured = request["context"]["input_capture"]
    groups = []
    for group in ("source", "test", "config", "artifact", "environment", "method", "check"):
        differences = [row for row in comparison["differences"] if row["group"] == group]
        known = len(captured["snapshots"].get(group, {}))
        if group == "environment":
            known = len(captured["environment"]["facts"])
        if group == "artifact":
            known = len(captured["artifact_refs"])
        if group == "method":
            known = len(captured["method"])
        if group == "check":
            known = len(captured["checks"])
        unknown = 0
        if group == "environment":
            unknown = len(captured["environment"]["unknowns"])
        if group == "method":
            unknown = int("structured_method" in comparison["unknowns"])
        if group in {"source", "test", "config"}:
            paths = {str(item["path"]) for item in captured["manifest"]["groups"][group]}
            unknown = sum(
                reason.partition(":")[0] in {"required_input", "input_kind"}
                and reason.partition(":")[2] in paths
                for reason in comparison["unknowns"]
            )
        if group == "check":
            unknown = len(
                [
                    reason
                    for reason in comparison["unknowns"]
                    if reason not in captured["environment"]["unknowns"]
                    and reason != "structured_method"
                    and reason.partition(":")[0] not in {"required_input", "input_kind"}
                ]
            )
        groups.append(
            {
                "id": group,
                "status": "changed" if differences else comparison["status"],
                "counts": {
                    "known": known,
                    "changed": len(differences),
                    "missing": sum(row["kind"] == "missing" for row in differences),
                    "unknown": unknown,
                },
            }
        )
    return {
        "schema_version": 1,
        "status": comparison["status"],
        "applicability": applicability,
        "completeness": "unknown" if comparison["status"] == "not_assessed" else "complete",
        "next_action": "Inspect unknown inputs"
        if comparison["status"] == "not_assessed"
        else "Recheck affected checks"
        if comparison["status"] == "changed"
        else "Inspect current task obligations",
        "reasons": comparison["reasons"],
        "counts": comparison["counts"],
        "groups": {"total": len(groups), "shown": len(groups), "omitted": 0, "items": groups},
    }


def bound_projection(full: Record, *, limit: int = MAX_ROWS) -> Record:
    """Bound already assessed facts once; never recapture inputs or hide counts."""
    _require(type(limit) is int and 0 <= limit <= MAX_RECORDS, "Invalid verification row limit")
    result = deepcopy(full)
    collections = [result["history"], result["integrity"]["records"]]
    for task in result["tasks"].values():
        collections.extend([task["lanes"], task["findings"]])
        for lane in task["lanes"]["items"]:
            if lane.get("execution_history") is not None:
                collections.append(lane["execution_history"])
    for collection in collections:
        rows = collection["items"][:limit]
        collection.update(items=rows, shown=len(rows), omitted=collection["total"] - len(rows))
    return result


def projection(
    program: ProgramAccess,
    state: Record,
    budget: ReadBudget | None = None,
    *,
    limit: int | None = MAX_ROWS,
) -> Record:
    """Project recorded phases/outcomes separately from current source applicability."""
    validate(state)
    reads = _reads(budget)
    records = _records(state)
    requests: dict[tuple[str, str], Record] = {}
    by_request: dict[str, list[Record]] = {}
    requests_by_task: dict[str, list[Record]] = {}
    findings_by_task: dict[str, list[Record]] = {}
    resolutions_by_finding: dict[str, list[Record]] = {}
    integrity: dict[str, str] = {}
    for item in records:
        value = item["input"]
        by_request.setdefault(str(item["request_id"]), []).append(item)
        try:
            program.verify_refs(_items(item["evidence"], "verification evidence"), reads.artifacts)
        except (OSError, _core().ProgramError) as exc:
            integrity[str(value["id"])] = str(exc)
        if value["kind"] == "request":
            requests[(str(value["task_id"]), str(value["lane"]))] = item
        if value["kind"] == "finding":
            findings_by_task.setdefault(str(value["task_id"]), []).append(item)
        if value["kind"] == "resolution":
            resolutions_by_finding.setdefault(str(value["finding_id"]), []).append(item)
    for (task_id, _), item in requests.items():
        requests_by_task.setdefault(task_id, []).append(item)
    tasks: Record = {}
    positions = {str(item["input"]["id"]): index for index, item in enumerate(records)}
    for task_id in sorted({str(item["input"]["task_id"]) for item in records}):
        lanes: list[Json] = []
        findings: list[Json] = []
        for request in requests_by_task.get(task_id, []):
            lane = str(request["input"]["lane"])
            events = by_request[str(request["request_id"])]
            operational = [
                item
                for item in events
                if item["input"]["kind"]
                in {"request", "started", "blocked", "finished", "canceled"}
            ]
            latest = operational[-1]
            value = latest["input"]
            phase = {
                "request": "requested",
                "started": "running",
                "blocked": "blocked",
                "finished": "finished",
                "canceled": "canceled",
            }[str(value["kind"])]
            applicability, reasons = "current", []
            try:
                good, reasons = _applicable(program, state, latest, reads)
                applicability = "current" if good else "stale"
                artifact_reasons = [
                    integrity[identifier]
                    for identifier in (str(request["input"]["id"]), str(value["id"]))
                    if identifier in integrity
                ]
                if artifact_reasons:
                    applicability, reasons = "unavailable", [*reasons, *artifact_reasons]
            except (
                OSError,
                _core().ProgramError,
                _core().planning.PlanningError,
                _core().coverage.CoverageError,
            ) as exc:
                applicability, reasons = "unavailable", [str(exc)]
            blocker = deepcopy(value.get("blocker")) if value["kind"] == "blocked" else None
            lanes.append(
                {
                    "request_id": request["request_id"],
                    "request_version": request["request_version"],
                    "observation_id": value["id"] if latest is not request else None,
                    "lane": lane,
                    "check_ids": deepcopy(request["input"]["check_ids"]),
                    "criteria": deepcopy(request["input"].get("criteria", [])),
                    "verifier_id": request["input"]["verifier_id"],
                    "input_complete": request["input"]["input_complete"],
                    "environment": request["input"].get("environment"),
                    "resource": deepcopy(request["input"].get("resource")),
                    "evidence_destination": request["input"]["evidence_destination"],
                    "observation_problems": _observation_problems(latest, request)
                    if value["kind"] == "finished"
                    else [],
                    "resolver_id": blocker.get("resolver_id") if blocker else None,
                    "next_trigger": blocker["next_trigger"]
                    if blocker
                    else value.get("next_trigger", request["input"]["next_trigger"]),
                    "phase": phase,
                    "result": value.get("result", "pending" if phase == "requested" else "unknown"),
                    "applicability": applicability,
                    "reasons": reasons,
                    "context": deepcopy(request["context"]),
                    "request_ref": deepcopy(request["receipt"]),
                    "observation_ref": deepcopy(latest["receipt"])
                    if latest is not request
                    else None,
                    "request_reference": _reference(
                        state,
                        f"/verification/records/{positions[str(request['input']['id'])]}",
                        task_id,
                    ),
                    "observation_reference": _reference(
                        state, f"/verification/records/{positions[str(value['id'])]}", task_id
                    )
                    if latest is not request
                    else None,
                    "blocker": blocker,
                    "waiting": _waiting(events),
                    "recheck": {
                        "status": "recheck_required"
                        if applicability != "current"
                        else "not_assessed",
                        "reasons": reasons,
                    },
                }
            )
            if "input_capture" in request["context"]:
                try:
                    lanes[-1]["input_summary"] = _input_summary(program, request, applicability)
                except (OSError, _core().ProgramError):
                    lanes[-1]["input_summary"] = {
                        "schema_version": 1,
                        "status": "not_assessed",
                        "applicability": "unavailable",
                        "completeness": "unknown",
                        "next_action": "Inspect unavailable inputs",
                        "reasons": ["unavailable_inputs"],
                        "counts": {"known": 0, "changed": 0, "missing": 0, "unknown": 1},
                        "groups": {"total": 0, "shown": 0, "omitted": 0, "items": []},
                    }
            executions = []
            execution_events = [
                event
                for event in records
                if event["input"]["task_id"] == task_id
                and reads.record_index[str(event["request_id"])]["input"]["lane"] == lane
            ]
            for event in execution_events:
                origin = event["input"].get("run_capture_ref")
                if origin is None:
                    continue
                try:
                    program.verify_refs([origin], reads.artifacts)
                    run = _core().read_json(
                        program.path(str(origin["path"])), budget=reads.artifacts
                    )
                    summary = _execution().summarize_capture(run)
                    summary["publication"] = {
                        "status": "recorded",
                        "observation_id": event["input"]["id"],
                    }
                    summary["reused"] = (
                        run.get("task_id") != task_id
                        or run.get("request_id") != event["request_id"]
                    )
                    if event["input"]["kind"] == "finished":
                        summary["reported_check_result"] = event["input"]["result"]
                    executions.append(summary)
                except (OSError, _core().ProgramError):
                    executions.append(
                        {
                            "schema_version": 1,
                            "status": "unavailable",
                            "next_action": "Inspect unavailable capture",
                            "reasons": ["unavailable_capture"],
                        }
                    )
            if executions:
                if value.get("run_capture_ref") is not None:
                    lanes[-1]["execution_summary"] = executions[-1]
                lanes[-1]["execution_history"] = {
                    "total": len(executions),
                    "shown": len(executions),
                    "omitted": 0,
                    "items": executions,
                }
            if (
                request["context"].get("provider_context_ref") is not None
                or value.get("provider_ref") is not None
            ):
                try:
                    provider_execution = _provider_assessment(
                        program, request, latest, reads, applicability
                    )
                except (OSError, _core().ProgramError) as exc:
                    provider_execution = {
                        "adopted": True,
                        "status": "unavailable",
                        "stages": [],
                        "reasons": [str(exc)],
                        "current": False,
                        "applicability": "unavailable",
                        "remote_verified_by_tool": False,
                    }
                provider_execution.update(
                    {
                        "task_id": task_id,
                        "request_id": request["request_id"],
                        "reference": _reference(
                            state, f"/verification/records/{positions[str(value['id'])]}", task_id
                        ),
                    }
                )
                for stage in provider_execution["stages"]:
                    stage["request_id"] = request["request_id"]
                lanes[-1]["provider_execution"] = provider_execution
        for item in findings_by_task.get(task_id, []):
            value = item["input"]
            if value["task_id"] == task_id and value["kind"] == "finding":
                resolutions = [
                    {"id": event["input"]["id"], "observation_ref": deepcopy(event["receipt"])}
                    for event in resolutions_by_finding.get(str(value["id"]), [])
                ]
                findings.append(
                    {
                        "id": value["id"],
                        "request_id": item["request_id"],
                        "statement": value["statement"],
                        "affected_paths": deepcopy(value["affected_paths"]),
                        "check_ids": deepcopy(value.get("check_ids", [])),
                        "criteria": deepcopy(value.get("criteria", [])),
                        "resolutions": resolutions,
                        "status": "recorded",
                        "reference": _reference(
                            state, f"/verification/records/{positions[str(value['id'])]}", task_id
                        ),
                        "receipt_ref": deepcopy(item["receipt"]),
                    }
                )
        tasks[task_id] = {
            "task_id": task_id,
            "lanes": _collection(state, lanes, "/verification/records", task_id, limit=None),
            "findings": _collection(state, findings, "/verification/records", task_id, limit=None),
            "reference": _reference(state, "/verification/records", task_id),
        }
    history = [
        {
            "id": item["input"]["id"],
            "kind": item["input"]["kind"],
            "task_id": item["input"]["task_id"],
            "receipt": deepcopy(item["receipt"]),
        }
        for item in records
    ]
    result = {
        "schema_version": 1,
        "status": "recorded" if "verification" in state else "absent",
        "tasks": tasks,
        "history": _collection(state, history, "/verification/records", limit=None),
        "integrity": {
            "unavailable_total": len(integrity),
            "records": _collection(
                state,
                [{"id": identifier, "reason": reason} for identifier, reason in integrity.items()],
                "/verification/records",
                limit=None,
            ),
        },
    }
    reads.recheck(program)
    if limit is not None:
        result = bound_projection(result, limit=limit)
    _require(
        len(_core().canonical(result)) <= OUTPUT_LIMIT, "Verification projection exceeds 8 MiB"
    )
    return result


def validate_receipt_refs(
    program: ProgramAccess, state: Record, receipt: Record, task_id: str
) -> None:
    """Validate newly declared reliance against actual pre-transition source, not a guessed round."""
    items = _retained_refs(state, receipt, task_id)
    if not items:
        return
    reads = _reads()
    task = program.task(state, task_id)
    attempt = program.current_attempt(task)
    submitted = False
    current_submission = attempt.get("submission")
    if current_submission is not None:
        current_ref = _record(_record(current_submission, "submission")["receipt"], "receipt")
        submitted = receipt.get("receipt_sha256") == current_ref["sha256"]
    for item in items:
        program.verify_refs(_items(item["evidence"], "verification evidence"), reads.artifacts)
        applicable, reasons = _applicable(program, state, item, reads, submitted=submitted)
        _require(applicable, "Stale verification reliance: " + "; ".join(reasons))
        request_item = _index(state)[str(item["request_id"])]
        problems = _observation_problems(item, request_item)
        _require(not problems, "Invalid verification reliance: " + "; ".join(problems))
        provider_assessment = _provider_assessment(program, request_item, item, reads, "current")
        if provider_assessment["adopted"]:
            _require(
                provider_assessment["status"] == "passed"
                and provider_assessment.get("current") is True,
                "Explicit provider verification reliance did not pass",
            )
        if not submitted:
            checks = {
                _record(value, "check")["id"]: _record(value, "check")
                for value in _items(receipt.get("checks", []), "checks")
            }
            for check_id in _texts(request_item["input"]["check_ids"], "request checks"):
                row = checks.get(check_id)
                _require(
                    row is not None
                    and row.get("status") == "passed"
                    and row.get("execution_kind") == item["input"]["method"]["execution_kind"]
                    and row.get("evidence") == item["input"]["artifacts"],
                    "Execution check differs from explicitly relied observation",
                )
    reads.recheck(program)


def submission_inputs(program: ProgramAccess, state: Record, request: Record) -> Record:
    """Prepare the ordinary execution receipt from exact current recorded observations."""
    program.require_current_location()
    program.validate(state)
    task_id = _id(request.get("task_id"), "task ID")
    task = program.task(state, task_id)
    _require(
        task["status"] == "active" and request.get("owner") == task["owner"],
        "Execution input requires actual active owner",
    )
    attempt = program.current_attempt(task)
    reads = _reads()
    index = _index(state)
    selected = _texts(request.get("observation_ids", []), "observation IDs")
    matching: dict[str, Record] = {}
    missing: list[Json] = []
    refs: list[Json] = []
    for identifier in selected:
        item = index.get(identifier)
        _require(
            item is not None
            and item["input"]["kind"] == "finished"
            and item["input"]["task_id"] == task_id,
            "Unknown or mismatched finished observation",
        )
        program.verify_refs(_items(item["evidence"], "observation evidence"), reads.artifacts)
        applicable, reasons = _applicable(program, state, item, reads)
        if not applicable:
            missing.append({"observation_id": identifier, "reasons": reasons})
            continue
        request_item = index[str(item["request_id"])]
        value = item["input"]
        problems = _observation_problems(item, request_item, include_result=False)
        if problems:
            missing.append({"observation_id": identifier, "reasons": problems})
            continue
        provider_assessment = _provider_assessment(program, request_item, item, reads, "current")
        if provider_assessment["adopted"] and (
            provider_assessment["status"] != "passed"
            or provider_assessment.get("current") is not True
        ):
            missing.append(
                {
                    "observation_id": identifier,
                    "reasons": ["Selected provider evidence is not passing and current"],
                }
            )
            continue
        stable = (
            value.get("input_before") is not None
            and value.get("input_before")
            == value.get("input_after")
            == request_item["context"]["input_digest"]
        )
        if not stable:
            missing.append(
                {"observation_id": identifier, "reasons": ["Executed input identity incomplete"]}
            )
            continue
        for check in _texts(request_item["input"]["check_ids"], "check IDs"):
            _require(check not in matching, "Multiple selected observations for one check")
            matching[check] = item
        refs.append({"id": identifier, "receipt": deepcopy(item["receipt"])})
    checks: list[Json] = []
    for check in _texts(task["required_checks"], "required checks"):
        item = matching.get(check)
        status, command, kind, evidence = "not_run", "Verification not executed", "test", []
        if item is not None:
            value = item["input"]
            method = _record(value["method"], "method")
            status = str(value.get("result", "unknown"))
            command = str(
                method.get("command")
                or method.get("procedure")
                or _core().canonical(method.get("argv", [])).decode().strip()
            )
            kind = str(method["execution_kind"])
            evidence = deepcopy(value["artifacts"])
            if check in {"native_editor", "native_player"} and kind != "native":
                status = "not_run"
        if status != "passed":
            missing.append({"check_id": check, "status": status})
        checks.append(
            {
                "id": check,
                "status": status,
                "command": command,
                "execution_kind": kind,
                "evidence": evidence,
            }
        )
    acceptance = deepcopy(_items(request.get("acceptance_evidence", []), "acceptance evidence"))
    authored_acceptance = (
        request.get("acceptance_complete") is True
        and bool(acceptance)
        and all(
            _record(value, "acceptance criterion").get("status") == "passed" for value in acceptance
        )
    )
    if not authored_acceptance:
        missing.append("acceptance_evidence")
    for value in acceptance:
        criterion = _record(value, "acceptance criterion")
        names = _texts(criterion.get("evidence", []), "acceptance evidence")
        if (
            not names
            or not isinstance(criterion.get("criterion"), str)
            or not str(criterion["criterion"]).strip()
        ):
            missing.append("acceptance_evidence")
            continue
        for name in names:
            try:
                _require(
                    name.startswith(f"evidence/{task_id}/attempt-{attempt['number']}/"),
                    "Acceptance evidence must be attempt-specific",
                )
                _capture(program, name, reads)
            except (OSError, _core().ProgramError) as exc:
                missing.append({"acceptance_evidence": name, "reason": str(exc)})
    for name in ("summary", "next_action"):
        if not isinstance(request.get(name), str) or not str(request[name]).strip():
            missing.append(name)
    snapshot = reads.snapshot(program, _texts(attempt["scope"], "owned scope"))
    receipt = {
        "task_id": task_id,
        "attempt": attempt["number"],
        "summary": request.get("summary"),
        "changed_paths": program.changed_paths(_record(attempt["baseline"], "baseline"), snapshot),
        "checks": checks,
        "acceptance_complete": authored_acceptance and not missing,
        "acceptance_evidence": acceptance,
        "unresolved_acceptance": []
        if authored_acceptance and not missing
        else ["Verification or complete acceptance remains unresolved"],
        "unresolved_blockers": [],
        "limitations": deepcopy(request.get("limitations", [])),
        "next_action": request.get("next_action"),
        "verification_refs": refs,
    }
    reads.recheck(program)
    result = {
        "status": "not_assessed",
        "receipt": receipt,
        "missing_fields": missing,
        "verification_refs": refs,
        "acceptance_complete": False,
        "limitations": [
            "Recorded caller observations; no execution, acceptance or publication performed."
        ],
    }
    _require(len(_core().canonical(result)) <= OUTPUT_LIMIT, "Submission preparation exceeds 8 MiB")
    return result


def validate_completion(program: ProgramAccess, state: Record, task_id: str) -> None:
    """Require adopted current required lanes at acceptance, without gating submit or history."""
    if "verification" not in state:
        return
    validate(state)
    reads = _reads()
    reads.index(state)
    task = program.task(state, task_id)
    required = set(_texts(task["required_checks"], "required checks"))
    for (identifier, lane), request in reads.latest_lane.items():
        if identifier != task_id or not (required & set(request["input"]["check_ids"])):
            continue
        latest = reads.latest_event[str(request["request_id"])]
        _require(
            latest["input"]["kind"] == "finished",
            f"Required verification lane remains pending: {lane}",
        )
        program.verify_refs(
            _items(request["evidence"], "request evidence")
            + _items(latest["evidence"], "observation evidence"),
            reads.artifacts,
        )
        applicable, reasons = _applicable(program, state, latest, reads)
        _require(applicable, f"Required verification lane is stale: {lane}: " + "; ".join(reasons))
        problems = _observation_problems(latest, request)
        _require(
            not problems, f"Required verification lane did not pass: {lane}: " + "; ".join(problems)
        )
        assessment = _provider_assessment(program, request, latest, reads, "current")
        if assessment["adopted"]:
            _require(
                assessment["status"] == "passed" and assessment.get("current") is True,
                f"Required provider lane did not pass: {lane}",
            )
    reads.recheck(program)


def publication_guard(program: ProgramAccess, future: Record) -> Callable[[], None] | None:
    """Recheck newly declared frozen reliance against the original registry before publication."""
    core = _core()
    if not program.path("backlog.json").exists():
        return None
    original = core.read_json(program.path("backlog.json"))
    old_records, future_records = _records(original), _records(future)
    _require(
        "verification" not in original or "verification" in future,
        "Recorded verification extension cannot be removed",
    )
    _require(
        len(future_records) >= len(old_records)
        and future_records[: len(old_records)] == old_records,
        "Verification history must remain an exact append-only prefix",
    )
    appended = future_records[len(old_records) :]
    appended_refs = [
        ref for item in appended for ref in _items(item["evidence"], "verification evidence")
    ]
    old_refs = set()
    original_tasks: dict[str, Record] = {}
    for task_value in _items(original.get("tasks", []), "tasks"):
        original_task = _record(task_value, "task")
        original_tasks[str(original_task["id"])] = original_task
        for attempt_value in _items(original_task.get("attempts", []), "attempts"):
            for ref in _items(_record(attempt_value, "attempt").get("evidence", []), "evidence"):
                old_refs.add(core.digest(ref))
    relied: list[tuple[str, Record, Record]] = []
    completed_tasks = []
    for task_value in _items(future.get("tasks", []), "tasks"):
        task = _record(task_value, "task")
        old_task = original_tasks.get(str(task["id"]))
        if (
            task.get("status") == "done"
            and old_task is not None
            and old_task.get("status") == "review"
        ):
            validate_completion(program, original, str(task["id"]))
            completed_tasks.append(str(task["id"]))
        for attempt_value in _items(task.get("attempts", []), "attempts"):
            for ref_value in _items(
                _record(attempt_value, "attempt").get("evidence", []), "evidence"
            ):
                ref = _record(ref_value, "receipt")
                if core.digest(ref) not in old_refs and str(ref["path"]).endswith(".json"):
                    receipt = core.read_json(program.path(str(ref["path"])))
                    if receipt.get("verification_refs"):
                        validate_receipt_refs(program, original, receipt, str(task["id"]))
                        relied.append((str(task["id"]), receipt, ref))
    if not relied and not appended and not completed_tasks:
        return None
    revision = core.phases.revision(original)

    def recheck() -> None:
        current = core.read_json(program.path("backlog.json"))
        _require(
            core.phases.revision(current) == revision,
            "Verification reliance registry changed before publication",
        )
        program.verify_refs(appended_refs)
        for task_id in completed_tasks:
            validate_completion(program, current, task_id)
        for task_id, receipt, ref in relied:
            program.verify_refs([ref])
            validate_receipt_refs(program, current, receipt, task_id)

    return recheck
