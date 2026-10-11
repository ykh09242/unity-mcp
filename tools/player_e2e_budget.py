"""Actual offline budget CLI checks for independently completed native Player cases."""

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Annotated, Final, Literal, TypedDict, assert_never

from pydantic import BaseModel, ConfigDict, Field

from player_e2e_artifacts import contained, digest, inventory, read_json, write_json
from player_e2e_process import Invocation, execute
from player_e2e_profiles import ExecutionWindow

REPOSITORY: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "Server/src"))

from cli.utils.play_scenario_soak_budget import check_soak_budget
from cli.utils.play_scenario_soak_compare_evidence import read_saved_soak, SavedSoak

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SessionId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class BudgetEvidenceError(ValueError):
    """Required CLI evidence is missing, stale, changed or inconsistent."""


class _Session(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    session_id: SessionId
    started_unix_ms: Annotated[int, Field(ge=0)]


class _Launch(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    command: list[str]
    process_id: Annotated[int, Field(ge=1)]
    started_unix_ms: Annotated[int, Field(ge=0)]
    finished_unix_ms: Annotated[int, Field(ge=0)]
    actual_exit_code: int
    process_ended: bool
    forced_termination: bool
    log_truncated: bool
    progress_read_errors: list[str]


class _File(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    path: str
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Digest


class _Receipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal[1]
    session_id: SessionId
    case: str
    completed_unix_ms: Annotated[int, Field(ge=0)]
    process_id: Annotated[int, Field(ge=1)]
    policy_sha256: Digest
    launch_sha256: Digest
    output_sha256: Digest
    inputs_before: dict[str, list[_File]]
    inputs_after: dict[str, list[_File]]


class _MetricPolicy(TypedDict):
    max_net_change_increase: int
    max_slope_increase_per_iteration: int
    min_paired_samples: int
    min_consecutive_samples: int


class _Policy(TypedDict):
    schema_version: int
    metrics: dict[str, _MetricPolicy]


@dataclass(frozen=True, slots=True)
class _Case:
    name: str
    status: Literal["within_budget", "budget_exceeded", "not_comparable"]
    exit_code: int
    net_limit: int
    slope_limit: int
    paired: int
    consecutive: int

    def policy(self) -> _Policy:
        """Keep every required gate limit explicit in the saved policy bytes."""
        return {
            "schema_version": 1,
            "metrics": {
                "managed_bytes": {
                    "max_net_change_increase": self.net_limit,
                    "max_slope_increase_per_iteration": self.slope_limit,
                    "min_paired_samples": self.paired,
                    "min_consecutive_samples": self.consecutive,
                }
            },
        }


CASES: Final = (
    _Case("within-budget", "within_budget", 0, 2147483647, 100000000, 20, 20),
    _Case("budget-exceeded", "budget_exceeded", 1, 0, 0, 20, 20),
    _Case("insufficient-evidence", "not_comparable", 2, 0, 0, 31, 2),
)


@dataclass(frozen=True, slots=True)
class _Inputs:
    directories: tuple[Path, Path]
    histories: tuple[SavedSoak, SavedSoak]
    case_directories: tuple[Path, Path]
    completed_unix_ms: int

    def snapshot(self) -> dict[str, list[_File]]:
        """Hash all bounded source-case files, including native and launcher evidence."""
        result = {}
        for label, directory in zip(("baseline", "candidate"), self.case_directories):
            files = [_File.model_validate(row) for row in inventory(directory)]
            manifest = directory / "scenario-bundle.json"
            if manifest.exists():
                files.append(
                    _File(
                        path=manifest.name,
                        size_bytes=manifest.stat().st_size,
                        sha256=digest(manifest),
                    )
                )
            result[label] = files
        return result


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BudgetEvidenceError(message)


def _session(root: Path, session_id: str) -> _Session:
    _require((root / "session-start.json").is_file(), "Required budget session is missing")
    session = _Session.model_validate(read_json(root / "session-start.json", 16384))
    _require(session.session_id == session_id, "Budget session identity differs")
    return session


def _inputs(root: Path, session: _Session) -> _Inputs:
    directories, cases, histories, finished = [], [], [], []
    for case in ("soak-budget-baseline", "soak-budget-growth"):
        directory = contained(root, "cases/" + case)
        launch = _Launch.model_validate(read_json(directory / "launch.json"))
        _natural(launch, session)
        _require(launch.actual_exit_code == 0, "Budget source CLI did not succeed")
        children = []
        for child in contained(directory, "cli-output").iterdir():
            children.append(child)
            _require(len(children) <= 1, "Budget source requires one native job")
        _require(len(children) == 1, "Budget source native job is missing")
        child = contained(directory, "cli-output/" + children[0].name)
        history = read_saved_soak(child)
        _require(child.name == history.identity.job_id, "Budget source job/path mismatch")
        _require(history.request.soak.iterations == 30, "Budget source must complete 30 iterations")
        _require(
            history.scenario.model_dump(mode="json")["metrics"]["warmup_iterations"] == 1,
            "Budget source must exclude one warmup iteration",
        )
        _require(
            history.identity.process_id != launch.process_id, "Native job reused its parent CLI PID"
        )
        directories.append(child)
        histories.append(history)
        cases.append(directory)
        finished.append(launch.finished_unix_ms)
    left, right = histories[0], histories[1]
    _require(
        left.identity.job_id != right.identity.job_id
        and left.identity.process_id != right.identity.process_id,
        "Budget comparison requires distinct actual jobs and PIDs",
    )
    return _Inputs(
        (directories[0], directories[1]), (left, right), (cases[0], cases[1]), max(finished)
    )


def _natural(launch: _Launch, session: _Session) -> None:
    _require(
        launch.process_ended
        and not launch.forced_termination
        and not launch.log_truncated
        and not launch.progress_read_errors,
        "Budget evidence lacks a natural complete CLI exit",
    )
    _require(
        session.started_unix_ms <= launch.started_unix_ms <= launch.finished_unix_ms,
        "Budget CLI process evidence is stale or reversed",
    )


def _command(inputs: _Inputs, policy: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cli.main",
        "--format",
        "json",
        "play-scenario",
        "check-soak-budget",
        *(str(path) for path in inputs.directories),
        "--policy",
        str(policy),
    ]


def _decision(inputs: _Inputs, directory: Path, case: _Case) -> None:
    launch = _Launch.model_validate(read_json(directory / "launch.json"))
    _require(
        launch.started_unix_ms >= inputs.completed_unix_ms,
        "Budget CLI preceded its native source completion",
    )
    result = check_soak_budget(*inputs.directories, directory / "policy.json")
    _require(
        result.status == case.status and result.environment_verified and not result.leak_verdict,
        "Budget CLI expected status or verified environment differs",
    )
    _require(
        len(result.metrics) == 1 and result.metrics[0].metric == "managed_bytes",
        "Budget must check exactly managed bytes",
    )
    metric = result.metrics[0]
    _require(
        metric.aligned_samples == 29 and metric.longest_consecutive_samples == 29,
        "Budget boundary sample population differs",
    )
    match case.status:
        case "within_budget":
            _require(not metric.reasons, "Within-budget outcome has failure reasons")
        case "budget_exceeded":
            _require(
                metric.net_change_delta is not None and metric.net_change_delta > 0,
                "Controlled candidate lacks observed managed growth",
            )
            _require(
                bool(
                    set(metric.reasons)
                    & {"net_change_increase_exceeded", "slope_increase_exceeded"}
                ),
                "Growth CLI outcome lacks an exceeded-budget reason",
            )
        case "not_comparable":
            _require(
                "insufficient_paired_samples" in metric.reasons and metric.paired_samples == 29,
                "Insufficient evidence outcome lacks its sample reason",
            )
        case unreachable:
            assert_never(unreachable)
    expected = json.loads(json.dumps(result.to_dict(), allow_nan=False))
    _require(
        read_json(directory / "process.log") == expected,
        "Actual CLI JSON disagrees with reopened native evidence",
    )


def _timeout(deadline: float | None) -> float:
    """Admit no CLI after the absolute monotonic profile deadline."""
    remaining = 60.0 if deadline is None else min(60.0, deadline - time.monotonic())
    _require(remaining > 0, "Profile deadline elapsed before budget CLI admission")
    return remaining


def run_budget_checks(root: Path, session_id: str, deadline: float | None = None) -> None:
    """Run each required budget CLI outcome against actual completed native cases."""
    _timeout(deadline)
    session = _session(root, session_id)
    inputs = _inputs(root, session)
    folder = root / "budget-checks"
    folder.mkdir(exist_ok=False)
    for case in CASES:
        directory = folder / case.name
        directory.mkdir()
        write_json(directory / "policy.json", case.policy())
        before = inputs.snapshot()
        launch = _Launch.model_validate(
            execute(
                Invocation(
                    _command(inputs, directory / "policy.json"),
                    directory,
                    timeout=_timeout(deadline),
                    environment={
                        "PYTHONPATH": str(REPOSITORY / "Server/src"),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                )
            )
        )
        _natural(launch, session)
        _require(launch.actual_exit_code == case.exit_code, "Actual budget CLI exit differs")
        _decision(inputs, directory, case)
        after = inputs.snapshot()
        _require(before == after, "Budget CLI modified original native inputs")
        receipt = _Receipt(
            schema_version=1,
            session_id=session_id,
            case=case.name,
            completed_unix_ms=int(time.time() * 1000),
            process_id=launch.process_id,
            policy_sha256=digest(directory / "policy.json"),
            launch_sha256=digest(directory / "launch.json"),
            output_sha256=digest(directory / "process.log"),
            inputs_before=before,
            inputs_after=after,
        )
        write_json(directory / "receipt.json", receipt.model_dump(mode="json"))


def check_budget_checks(root: Path, session_id: str, window: ExecutionWindow | None = None) -> None:
    """Reject missing or stale budget checks during independent final verification."""
    session = _session(root, session_id)
    inputs = _inputs(root, session)
    current = inputs.snapshot()
    invocations = set()
    for case in CASES:
        directory = contained(root, "budget-checks/" + case.name)
        _require((directory / "receipt.json").is_file(), "Required budget CLI receipt is missing")
        receipt = _Receipt.model_validate(read_json(directory / "receipt.json"))
        launch = _Launch.model_validate(read_json(directory / "launch.json"))
        _natural(launch, session)
        if window is not None:
            _require(
                window.started_unix_ms
                <= launch.started_unix_ms
                <= launch.finished_unix_ms
                <= receipt.completed_unix_ms
                <= window.finished_unix_ms,
                "Budget CLI evidence is outside the profile execution window",
            )
        _require(
            receipt.session_id == session_id
            and receipt.case == case.name
            and receipt.process_id == launch.process_id,
            "Budget receipt attribution differs",
        )
        _require(
            launch.finished_unix_ms <= receipt.completed_unix_ms <= int(time.time() * 1000),
            "Budget completion receipt is stale or from the future",
        )
        _require(
            launch.actual_exit_code == case.exit_code
            and launch.command == _command(inputs, directory / "policy.json"),
            "Budget invocation or natural exit differs",
        )
        invocation = (launch.process_id, launch.started_unix_ms)
        _require(invocation not in invocations, "Budget CLI process boundary was reused")
        invocations.add(invocation)
        _require(
            read_json(directory / "policy.json") == case.policy(),
            "Saved budget policy differs from the explicit required limits",
        )
        _require(
            receipt.policy_sha256 == digest(directory / "policy.json")
            and receipt.launch_sha256 == digest(directory / "launch.json")
            and receipt.output_sha256 == digest(directory / "process.log"),
            "Budget artifact receipt hash differs",
        )
        _require(
            receipt.inputs_before == receipt.inputs_after == current,
            "Original budget inputs changed after the CLI checks",
        )
        _decision(inputs, directory, case)
