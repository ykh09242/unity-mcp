"""Execute one bounded profile and retain independent, fail-closed regression decisions."""

from collections.abc import Callable
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path, write_new
from cli.utils.play_scenario_flakiness import analyze_flakiness
from cli.utils.play_scenario_html import export_html
from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player
from cli.utils.play_scenario_player_session import PlayerSessionOptions, run_player_session
from cli.utils.play_scenario_regression_profile import load_profile, profile_path
from cli.utils.play_scenario_reports import write_player_artifacts
from cli.utils.play_scenario_soak_baselines import read_registry, validate_entry
from cli.utils.play_scenario_soak_budget import check_soak_budget
from cli.utils.play_scenario_soak_budget_policy import read_budget_policy
from cli.utils.play_scenario_soak_reports import run_player_soak
from cli.utils.play_scenario_visual_compare import compare_visual, load_visual_policy
from cli.utils.play_scenario_visual_reports import pair_visual_reports


class RegressionGate(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    status: Literal["passed", "failed", "unavailable"]
    detail: dict[str, JsonValue] = Field(default_factory=dict)


class RegressionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    name: str
    profile: dict[str, JsonValue]
    execution_directory: str | None
    execution_exit_code: int | None
    exit_code: Literal[0, 1, 2, 130]
    gates: list[RegressionGate]


def _gate(name: str, operation: Callable[[], dict[str, JsonValue]]) -> RegressionGate:
    """Evidence exceptions are unavailable decisions, never successful comparisons."""
    try:
        detail = operation()
        status = detail.get("status")
        verdict: Literal["passed", "failed", "unavailable"] = "unavailable"
        if status in ("within_budget", "analyzed", "exported", "same"):
            verdict = "passed"
        elif status in ("budget_exceeded", "exceeded", "failed", "changed"):
            verdict = "failed"
        return RegressionGate(name=name, status=verdict, detail=detail)
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        return RegressionGate(name=name, status="unavailable", detail={"error": str(exc)[:2048]})


def run_regression(source: Path, output: Path) -> RegressionResult:
    """Run one verified build; never select a new baseline or overwrite prior evidence."""
    source = safe_local_path(source)
    profile = load_profile(source)
    execution = profile.execution
    build = profile_path(source, execution.build_directory)
    output = safe_local_path(output)
    if output.is_relative_to(build):
        raise ValueError("Regression output must remain outside the immutable Player build")
    output.mkdir(parents=True, exist_ok=False)
    write_new(
        output / "profile.json",
        profile.model_dump_json(indent=2, exclude_unset=True).encode("utf-8"),
    )
    gates: list[RegressionGate] = []
    directory = None
    native_exit = None
    selected = None
    visual_policies = []
    visual_references: list[str] = []
    try:
        if profile.baseline:
            registry = read_registry(profile_path(source, profile.baseline.registry))
            pinned = {pin.name for pin in registry.pins}
            selected = next(
                (entry for entry in registry.entries if entry.name == profile.baseline.name), None
            )
            if selected is None or selected.name not in pinned:
                raise ValueError("The explicitly named regression baseline must be pinned")
            validate_entry(selected)
            policy = profile_path(source, profile.baseline.policy)
            frozen_policy = output / "budget-policy.json"
            write_new(frozen_policy, read_bytes(policy, 65536))
            read_budget_policy(frozen_policy)
        for index, visual in enumerate(profile.visual_checks):
            policy = load_visual_policy(profile_path(source, visual.policy))
            # Admit the reference screenshot before launching, and bind its bytes again at use.
            baseline = profile_path(source, visual.baseline_report)
            pair_visual_reports((baseline, baseline), visual.checkpoint, policy)
            visual_policies.append(policy)
            visual_references.append(sha256(read_bytes(baseline, 16 * 1024 * 1024)).hexdigest())
            write_new(output / f"visual-policy-{index}.json", policy.model_dump_json().encode())
        options = PlayerRunOptions(
            build_directory=build,
            output_directory=output / "execution",
            repeat_count=execution.repeat_count,
            timeout_seconds=execution.timeout_seconds,
            cleanup_wait_seconds=execution.cleanup_wait_seconds,
            expected_build_revision=execution.expected_build_revision,
            expected_build_id=execution.expected_build_id,
            require_verified_payload=True,
            soak=execution.soak,
            graphical=execution.graphical,
        )
        match execution.kind:
            case "run":
                outcome = run_player(options)
                directory, native_exit = outcome.directory, outcome.exit_code
                write_player_artifacts(outcome.report, directory, outcome.client_error)
            case "soak":
                soak = run_player_soak(options)
                directory, native_exit = soak.directory, soak.exit_code
            case "session":
                _, directory, native_exit = run_player_session(
                    PlayerSessionOptions(
                        build_directory=build,
                        output_directory=output / "execution",
                        mode=execution.session_mode,
                        iterations=execution.iterations,
                        batch_size=execution.batch_size,
                        max_runtime_seconds=execution.max_runtime_seconds,
                        timeout_seconds=execution.timeout_seconds,
                        cleanup_wait_seconds=execution.cleanup_wait_seconds,
                        failure_policy=execution.failure_policy,
                        retain_reports=execution.retain_reports,
                        expected_build_revision=execution.expected_build_revision,
                        expected_build_id=execution.expected_build_id,
                        graphical=execution.graphical,
                    )
                )
        gates.append(
            RegressionGate(
                name="execution",
                status="passed" if native_exit == 0 else "failed",
                detail={"exit_code": native_exit, "directory": str(directory)},
            )
        )
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        gates.append(
            RegressionGate(
                name="execution",
                status="failed" if native_exit not in (None, 0) else "unavailable",
                detail={"error": str(exc)[:2048], "exit_code": native_exit},
            )
        )

    def budget_check() -> dict[str, JsonValue]:
        if selected is None or directory is None:
            raise ValueError("No admitted pinned baseline and execution evidence")
        result = check_soak_budget(
            Path(selected.directory), directory, output / "budget-policy.json"
        )
        validate_entry(selected)
        # Dataclass budget results retain tuples; the gate envelope stores their JSON form.
        detail = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        return {
            **detail,
            "baseline_name": selected.name,
            "baseline_evidence_sha256": selected.evidence_sha256,
        }

    if profile.baseline:
        gates.append(_gate("soak-budget", budget_check))
    for index, visual in enumerate(profile.visual_checks):

        def visual_check(index=index, visual=visual) -> dict[str, JsonValue]:
            if directory is None or index >= len(visual_policies):
                raise ValueError("No admitted visual policy or execution evidence")
            baseline = profile_path(source, visual.baseline_report)
            if (
                sha256(read_bytes(baseline, 16 * 1024 * 1024)).hexdigest()
                != visual_references[index]
            ):
                raise ValueError("Visual reference report changed during execution")
            pair = pair_visual_reports(
                (baseline, directory / "run.json"),
                visual.checkpoint,
                visual_policies[index],
            )
            return compare_visual(
                pair, visual_policies[index], output / f"visual-{index}"
            ).to_dict()

        gates.append(_gate(f"visual-{index}", visual_check))
    for index, ui in enumerate(profile.ui_checks):

        def ui_check(ui=ui) -> dict[str, JsonValue]:
            from cli.utils.play_scenario_ui import compare_ui_snapshots, UiComparisonPolicy

            return compare_ui_snapshots(
                profile_path(source, ui.baseline),
                profile_path(source, ui.candidate),
                UiComparisonPolicy(
                    geometry_tolerance=ui.geometry_tolerance, require_occlusion=ui.require_occlusion
                ),
            )

        gates.append(_gate(f"saved-ui-{index}", ui_check))
    if profile.analyze:

        def analysis() -> dict[str, JsonValue]:
            if directory is None:
                raise ValueError("No execution evidence available to analyze")
            if execution.kind == "run":
                return analyze_flakiness((directory / "run.json",)).to_dict()
            from cli.utils.play_scenario_history import analyze_history

            filename = "session.json" if execution.kind == "session" else "client-soak.json"
            return analyze_history((directory / filename,)).to_dict()

        gates.append(_gate("analysis", analysis))
    if directory is not None and execution.kind != "session":
        gates.append(
            _gate(
                "native-report",
                lambda: export_html(directory / "run.json", output / "execution.html"),
            )
        )
    code = (
        130
        if native_exit == 130
        else (
            1
            if any(gate.status == "failed" for gate in gates)
            else 2
            if any(gate.status == "unavailable" for gate in gates)
            else 0
        )
    )
    result = RegressionResult(
        name=profile.name,
        profile=profile.model_dump(mode="json", exclude_unset=True),
        execution_directory=str(directory) if directory else None,
        execution_exit_code=native_exit,
        exit_code=code,
        gates=gates,
    )
    from cli.utils.play_scenario_regression_report import write_regression_reports

    write_regression_reports(output, result)
    return result
