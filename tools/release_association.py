"""Bind one saved execution to its actual REST job steps, checkout and output bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import PurePosixPath
import re

if __package__:
    from .release_execution import Json, JsonMap, PREFIX
else:
    from release_execution import Json, JsonMap, PREFIX


LEGACY_LIMIT = "Execution-to-output association was not recorded."
LOG_RECORD = re.compile(r"(?:[^\n]*\t)?\ufeff?(\d{4}-\d\d-\d\dT\S+Z) (.*)")
COMMON = frozenset(
    {
        "schema_version",
        "repository",
        "run_id",
        "run_attempt",
        "job_key",
        "checkout_sha",
        "execution_id",
        "kind",
        "step_name",
        "output_root",
        "record_type",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    kind: str
    execution_id: str
    workflow: JsonMap
    job: JsonMap
    files: dict[str, bytes]
    revision: str


@dataclass(frozen=True, slots=True)
class Step:
    number: int
    started: datetime
    completed: datetime
    conclusion: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("Execution association: " + message)


def document(value: Json) -> JsonMap:
    require(isinstance(value, dict), "expected an object")
    return value


def timestamp(value: Json) -> datetime:
    require(isinstance(value, str), "missing timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.tzinfo is not None, "timestamp must include its timezone")
    return stamp


def unique(pairs: list[tuple[str, Json]]) -> JsonMap:
    result: JsonMap = {}
    for key, value in pairs:
        require(key not in result, "duplicate record key")
        result[key] = value
    return result


def nonfinite(value: str) -> Json:
    raise ValueError("Execution association: non-standard JSON constant " + value)


def records(log: bytes) -> list[tuple[datetime, JsonMap]]:
    result = []
    for line in log.decode("utf-8-sig").splitlines():
        found = LOG_RECORD.fullmatch(line)
        if found and found[2].startswith(PREFIX):
            raw = found[2][len(PREFIX) :]
            require(len(raw) <= 32768, "record exceeds 32 KiB")
            record = document(json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite))
            result.append((timestamp(found[1]), record))
    require(len(result) <= 128, "too many execution records")
    return result


def actual_step(evidence: ExecutionEvidence, name: Json) -> Step:
    require(isinstance(name, str) and bool(name), "missing step name")
    steps = evidence.job.get("steps")
    require(isinstance(steps, list), "saved job lacks actual step metadata")
    matches = [document(step) for step in steps if document(step).get("name") == name]
    require(len(matches) == 1, "missing or ambiguous actual execution step")
    step = matches[0]
    number = step.get("number")
    require(type(number) is int and number > 0, "invalid actual step number")
    require(step.get("status") == "completed", "execution step is incomplete")
    started, completed = timestamp(step.get("started_at")), timestamp(step.get("completed_at"))
    require(started <= completed, "step timestamps are reversed")
    require(
        timestamp(evidence.workflow.get("run_started_at")) <= started, "step predates run attempt"
    )
    require(timestamp(evidence.job.get("started_at")) <= started, "step predates its job")
    require(completed <= timestamp(evidence.job.get("completed_at")), "step exceeds its job")
    conclusion = step.get("conclusion")
    require(conclusion in ("success", "failure"), "step was skipped or unavailable")
    return Step(number, started, completed, str(conclusion))


def emitted(step: Step, stamp: datetime) -> None:
    # REST step completion may have only whole-second precision; logs have subsecond precision.
    require(
        step.started <= stamp < step.completed + timedelta(seconds=1),
        "record is outside emitting step",
    )


def verify_association(evidence: ExecutionEvidence) -> bool:
    """Return whether the actual subprocess/action succeeded; do not infer feature coverage."""
    require(
        re.fullmatch(r"[0-9a-f]{32}", evidence.execution_id) is not None, "invalid execution ID"
    )
    associated = [
        (stamp, item)
        for stamp, item in records(evidence.files["log"])
        if item.get("execution_id") == evidence.execution_id
    ]
    require(len(associated) == 2, "requires exactly one start and one completion/observation")
    (start_stamp, start), (end_stamp, end) = associated
    require(start_stamp <= end_stamp, "completion precedes start")
    require(
        {key: start.get(key) for key in COMMON} == {key: end.get(key) for key in COMMON},
        "start and completion identities differ",
    )
    require(
        end.get("schema_version") == 1 and type(end.get("schema_version")) is int,
        "unsupported record version",
    )
    require(end.get("repository") == "ykh09242/unity-mcp", "wrong execution repository")
    require(end.get("kind") == evidence.kind, "wrong execution kind")
    require(
        end.get("run_id") == evidence.workflow.get("id") and type(end.get("run_id")) is int,
        "execution run ID differs",
    )
    attempt = end.get("run_attempt")
    require(
        type(attempt) is int
        and attempt > 0
        and type(evidence.workflow.get("run_attempt")) is int
        and attempt == evidence.workflow.get("run_attempt"),
        "execution run attempt differs",
    )
    require(end.get("checkout_sha") == evidence.revision, "execution differs from actual checkout")
    job_key, name = end.get("job_key"), end.get("step_name")
    require(
        isinstance(job_key, str) and isinstance(name, str) and f"[{job_key}:" in name,
        "step does not identify its workflow job key and row",
    )
    runner = actual_step(evidence, name)
    root = ".release-evidence/" + evidence.execution_id
    require(end.get("output_root") == root, "wrong owned output root")
    outputs, expected = document(end.get("outputs")), document(start.get("expected_outputs"))
    roles = {"execution-log"}
    match evidence.kind:
        case "python" | "native-editmode" | "native-playmode":
            roles.add("results")
        case "package-preparation":
            roles.add("preparation")
        case "compile":
            pass
        case _:
            raise ValueError("Unsupported associated execution kind")
    require(set(outputs) == set(expected) == roles, "wrong output roles")
    for role in roles:
        pointer = document(outputs[role])
        require(set(pointer) == {"path", "sha256"}, "invalid output pointer")
        raw = pointer.get("path")
        require(
            isinstance(raw, str) and raw == expected[role],
            "output path differs from reserved output",
        )
        path = PurePosixPath(raw)
        require(
            not path.is_absolute()
            and ".." not in path.parts
            and ":" not in raw
            and "\\" not in raw,
            "unsafe output path",
        )
        require(
            pointer.get("sha256") == hashlib.sha256(evidence.files[role]).hexdigest(),
            "attached output digest differs from execution",
        )
    match end.get("record_type"):
        case "subprocess-completion":
            require(
                set(start) == COMMON | {"event", "emitter_step_name", "expected_outputs"}
                and set(end) == COMMON | {"event", "emitter_step_name", "outputs", "exit_code"},
                "unsupported subprocess record fields",
            )
            require(
                evidence.kind in {"python", "package-preparation", "compile"},
                "wrong subprocess kind",
            )
            require(
                start.get("event") == "started" and end.get("event") == "completed",
                "missing subprocess completion",
            )
            require(
                start.get("emitter_step_name") == end.get("emitter_step_name") == name,
                "completion was not emitted by execution step",
            )
            emitted(runner, start_stamp)
            emitted(runner, end_stamp)
            code = end.get("exit_code")
            require(type(code) is int, "missing actual process exit code")
            require(
                (code == 0) == (runner.conclusion == "success"), "exit code contradicts actual step"
            )
            return code == 0
        case "action-observation":
            require(
                set(start)
                == COMMON | {"event", "emitter_step_name", "expected_outputs", "observer_step_name"}
                and set(end)
                == COMMON | {"event", "emitter_step_name", "outputs", "runner_outcome"},
                "unsupported action observation fields",
            )
            require(evidence.kind in {"native-editmode", "native-playmode"}, "wrong observed kind")
            require(
                start.get("event") == "reserved" and end.get("event") == "observed",
                "missing action observation",
            )
            require("exit_code" not in end, "observation is not a subprocess exit record")
            reserve = actual_step(evidence, start.get("emitter_step_name"))
            observer = actual_step(evidence, end.get("emitter_step_name"))
            require(
                start.get("observer_step_name") == end.get("emitter_step_name"),
                "wrong observer step",
            )
            require(
                reserve.number < runner.number < observer.number
                and reserve.completed <= runner.started
                and runner.completed <= observer.started,
                "native lineage steps are not ordered",
            )
            require(reserve.conclusion == observer.conclusion == "success", "lineage step failed")
            emitted(reserve, start_stamp)
            emitted(observer, end_stamp)
            outcome = end.get("runner_outcome")
            require(outcome in ("success", "failure"), "action was skipped or unavailable")
            # continue-on-error can preserve a successful step conclusion after a failed outcome.
            # Never promote the recorded failure to success through that policy transformation.
            require(
                outcome != "success" or runner.conclusion == "success",
                "observation contradicts actual action step",
            )
            mode = evidence.kind.removeprefix("native-")
            require(
                expected
                == {"results": f"{root}/{mode}-results.xml", "execution-log": f"{root}/{mode}.log"},
                "native outputs differ from exact action paths",
            )
            inputs = []
            for line in evidence.files["log"].decode("utf-8-sig").splitlines():
                found = LOG_RECORD.fullmatch(line)
                if found and runner.started <= timestamp(found[1]) < runner.completed + timedelta(
                    seconds=1
                ):
                    inputs.append(found[2])
            require(
                "  artifactsPath: " + root in inputs,
                "actual action did not receive reserved output path",
            )
            require(
                sum(
                    re.fullmatch(r"##\[group\]Run game-ci/unity-test-runner@[0-9a-f]{40}", line)
                    is not None
                    for line in inputs
                )
                == 1,
                "missing or ambiguous actual pinned GameCI action",
            )
            return outcome == "success"
        case _:
            raise ValueError("Unsupported execution association record type")
