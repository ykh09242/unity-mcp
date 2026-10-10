"""CLI commands for saved Unity-owned Play Mode scenarios."""

from pathlib import Path
from typing import TextIO
from uuid import uuid4

import click
from cli.utils.config import get_config
from cli.utils.connection import handle_unity_errors, run_command
from cli.utils.output import format_output
from models.play_scenarios import PlayScenario, PlayScenarioCommand, PlayScenarioSuite
from cli.utils.play_scenario_reports import write_suite_artifacts, write_player_artifacts
from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player
from cli.utils.play_scenario_player_payload import PlayerPayloadError
from cli.utils.play_scenario_player_session import PlayerSessionOptions, run_player_session
from cli.utils.play_scenario_suite import wait_for_suite
from pydantic import JsonValue, ValidationError


def _dispatch(**arguments) -> None:
    """Validate locally before one REST command, preserving structured Unity output."""
    try:
        command = PlayScenarioCommand.model_validate(arguments)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    config = get_config()
    result = run_command("manage_play_scenario", command.wire_parameters(), config)
    click.echo(format_output(result, config.format))
    # Native queries and idempotent run retries succeed even when the reported job failed.
    data = result.get("data")
    if command.action in {"run", "status", "suite_run", "suite_status"} and isinstance(data, dict):
        if data.get("status") in ("failed", "timed_out", "cancelled") or data.get("report_error"):
            raise click.exceptions.Exit(1)


@click.group("play-scenario")
def play_scenario():
    """Save and run repeatable Play Mode scenarios; status never waits or polls."""


@play_scenario.command("save")
@click.argument("definition", type=click.File("r", encoding="utf-8"))
@handle_unity_errors
def save(definition: TextIO):
    """Save a whole scenario from a UTF-8 JSON file (or - for stdin)."""
    raw = definition.read(65537)
    if len(raw.encode("utf-8")) > 65536:
        raise click.BadParameter("Scenario definition exceeds 64 KiB", param_hint="definition")
    try:
        scenario = PlayScenario.model_validate_json(raw)
    except ValidationError as exc:
        raise click.BadParameter(str(exc), param_hint="definition") from exc
    _dispatch(action="save", scenario=scenario)


@play_scenario.command("get")
@click.argument("name")
@handle_unity_errors
def get(name: str):
    """Get a saved scenario definition."""
    _dispatch(action="get", name=name)


@play_scenario.command("list")
@handle_unity_errors
def list_scenarios():
    """List saved scenario definitions."""
    _dispatch(action="list")


@play_scenario.command("delete")
@click.argument("name")
@handle_unity_errors
def delete(name: str):
    """Delete a saved scenario definition."""
    _dispatch(action="delete", name=name)


@play_scenario.command("run")
@click.argument("name")
@click.option("--job-id", default=None, help="Optional 32-character lowercase hex request key.")
@click.option("--repeat-count", type=click.IntRange(1, 10), default=1, show_default=True)
@click.option("--timeout-seconds", type=click.IntRange(1, 1800), default=300, show_default=True)
@click.option(
    "--source-revision", default=None, help="Optional caller-provided reproduction label."
)
@handle_unity_errors
def run(
    name: str,
    job_id: str | None,
    repeat_count: int,
    timeout_seconds: int,
    source_revision: str | None,
):
    """Start once and return immediately; use status for later observations.

    An idempotent retry reporting an unsuccessful terminal job exits with code 1.
    """
    _dispatch(
        action="run",
        name=name,
        job_id=job_id,
        repeat_count=repeat_count,
        timeout_seconds=timeout_seconds,
        source_revision=source_revision,
    )


@play_scenario.command("status")
@click.argument("job_id")
@handle_unity_errors
def status(job_id: str):
    """Read the job report once, including step status and bounded failure logs.

    Failed, timed_out and cancelled jobs exit with code 1.
    """
    _dispatch(action="status", job_id=job_id)


@play_scenario.command("cancel")
@click.argument("job_id")
@handle_unity_errors
def cancel(job_id: str):
    """Request cancellation; a processed request succeeds regardless of the job's outcome.

    An in-flight native scene load cannot be cancelled.
    """
    _dispatch(action="cancel", job_id=job_id)


@play_scenario.command("reports")
@click.option("--name", default=None, help="Optional saved scenario name filter.")
@handle_unity_errors
def reports(name: str | None):
    """Read retained terminal reports once; history outcomes do not change query success."""
    _dispatch(action="reports", name=name)


@play_scenario.command("suite-save")
@click.argument("definition", type=click.File("r", encoding="utf-8"))
@handle_unity_errors
def suite_save(definition: TextIO) -> None:
    """Save a bounded native suite definition from UTF-8 JSON or stdin."""
    raw = definition.read(65537)
    if len(raw.encode("utf-8")) > 65536:
        raise click.BadParameter("Suite definition exceeds 64 KiB", param_hint="definition")
    try:
        suite = PlayScenarioSuite.model_validate_json(raw)
    except ValidationError as exc:
        raise click.BadParameter(str(exc), param_hint="definition") from exc
    _dispatch(action="suite_save", suite=suite)


@play_scenario.command("suite-get")
@click.argument("name")
@handle_unity_errors
def suite_get(name: str) -> None:
    """Get a saved native suite definition."""
    _dispatch(action="suite_get", name=name)


@play_scenario.command("suite-list")
@handle_unity_errors
def suite_list() -> None:
    """List saved native suite names."""
    _dispatch(action="suite_list")


@play_scenario.command("suite-delete")
@click.argument("name")
@handle_unity_errors
def suite_delete(name: str) -> None:
    """Delete a saved native suite definition."""
    _dispatch(action="suite_delete", name=name)


@play_scenario.command("suite-status")
@click.argument("suite_id")
@handle_unity_errors
def suite_status(suite_id: str) -> None:
    """Observe a suite once; this command never waits."""
    _dispatch(action="suite_status", suite_id=suite_id)


@play_scenario.command("suite-cancel")
@click.argument("suite_id")
@handle_unity_errors
def suite_cancel(suite_id: str) -> None:
    """Request native cancellation once, including child cleanup."""
    _dispatch(action="suite_cancel", suite_id=suite_id)


@play_scenario.command("suite-reports")
@click.option("--name", default=None)
@handle_unity_errors
def suite_reports(name: str | None) -> None:
    """Query retained native suite reports once."""
    _dispatch(action="suite_reports", name=name)


@play_scenario.command("suite-run")
@click.argument("name")
@click.option("--suite-id", default=None, help="Optional 32-character lowercase hex request key.")
@click.option("--repeat-count", type=click.IntRange(1, 10), default=1, show_default=True)
@click.option("--timeout-seconds", type=click.IntRange(1, 1800), default=300, show_default=True)
@click.option("--source-revision", default=None)
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option(
    "--poll-interval-seconds", type=click.FloatRange(0.1, 10), default=0.5, show_default=True
)
@click.option("--cleanup-wait-seconds", type=click.IntRange(1, 600), default=360, show_default=True)
@handle_unity_errors
def suite_run(
    name: str,
    suite_id: str | None,
    repeat_count: int,
    timeout_seconds: int,
    source_revision: str | None,
    output_dir: Path,
    poll_interval_seconds: float,
    cleanup_wait_seconds: int,
) -> None:
    """Run one native suite, explicitly wait, and save suite.json plus junit.xml.

    Requires global --instance to pin every observation to the same Editor.
    Timeout or Ctrl+C requests cancellation once and observes bounded finalization.
    """
    config = get_config()
    if not config.unity_instance:
        raise click.UsageError("suite-run requires global --instance to select one Editor")
    try:
        command = PlayScenarioCommand(
            action="suite_run",
            name=name,
            suite_id=suite_id if suite_id is not None else uuid4().hex,
            repeat_count=repeat_count,
            timeout_seconds=timeout_seconds,
            source_revision=source_revision,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc

    def request(parameters: dict[str, JsonValue], request_timeout: int) -> dict[str, JsonValue]:
        return run_command("manage_play_scenario", parameters, config, timeout=request_timeout)

    report = wait_for_suite(
        request,
        command.wire_parameters(),
        timeout_seconds=timeout_seconds,
        cleanup_wait_seconds=cleanup_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        request_timeout=config.timeout,
    )
    result = {"success": True, "data": report}
    try:
        write_suite_artifacts(report, output_dir)
    except (OSError, ValueError) as exc:
        report = {**report, "artifact_error": str(exc)}
        result = {"success": False, "error": "report_persist_failed", "data": report}
    click.echo(format_output(result, config.format))
    if (
        report.get("status") != "succeeded"
        or report.get("report_error")
        or report.get("client_error")
        or report.get("artifact_error")
        or any(
            isinstance(entry, dict)
            and isinstance(entry.get("report"), dict)
            and entry["report"].get("report_error")
            for entry in report.get("scenarios", [])
        )
    ):
        raise click.exceptions.Exit(1)


@play_scenario.command("player-run")
@click.argument("build_directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--repeat-count", type=click.IntRange(1, 10), default=1, show_default=True)
@click.option("--timeout-seconds", type=click.IntRange(1, 1800), default=300, show_default=True)
@click.option("--source-revision", default=None)
@click.option(
    "--expected-build-revision", default=None, help="Require the frozen build label to match."
)
@click.option("--expected-build-id", default=None)
@click.option("--require-verified-payload", is_flag=True, help="Reject legacy unverified bundles.")
@click.option("--cleanup-wait-seconds", type=click.IntRange(1, 600), default=360, show_default=True)
def player_run(
    build_directory: Path,
    output_dir: Path,
    repeat_count: int,
    timeout_seconds: int,
    source_revision: str | None,
    cleanup_wait_seconds: int,
    expected_build_revision: str | None,
    expected_build_id: str | None,
    require_verified_payload: bool,
) -> None:
    """Launch an explicit local Player bundle, wait, and export actual run.json plus JUnit.

    Each invocation owns a new output subdirectory. Timeout and Ctrl+C request cleanup
    once, then terminate only the owned process if its bounded cleanup wait expires.
    """
    try:
        outcome = run_player(
            PlayerRunOptions(
                build_directory=build_directory,
                output_directory=output_dir,
                repeat_count=repeat_count,
                timeout_seconds=timeout_seconds,
                source_revision=source_revision,
                cleanup_wait_seconds=cleanup_wait_seconds,
                expected_build_revision=expected_build_revision,
                expected_build_id=expected_build_id,
                require_verified_payload=require_verified_payload,
            )
        )
        write_player_artifacts(outcome.report, outcome.directory, outcome.client_error)
    except (PlayerLaunchError, PlayerPayloadError, OSError, ValueError) as exc:
        # Bundle/process errors remain errors; no native success report is invented.
        raise click.ClickException(str(exc)) from exc
    click.echo(
        format_output(
            {"success": outcome.exit_code == 0, "data": outcome.report}, get_config().format
        )
    )
    if outcome.exit_code:
        raise click.exceptions.Exit(outcome.exit_code)


@play_scenario.command("player-session")
@click.argument("build_directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option(
    "--mode",
    type=click.Choice(["shared-batches", "fresh-process", "compare"]),
    default="shared-batches",
    show_default=True,
)
@click.option("--iterations", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--batch-size", type=click.IntRange(2, 10), default=10, show_default=True)
@click.option(
    "--max-runtime-seconds", type=click.IntRange(1, 86400), default=3600, show_default=True
)
@click.option("--timeout-seconds", type=click.IntRange(1, 1800), default=300, show_default=True)
@click.option("--cleanup-wait-seconds", type=click.IntRange(1, 600), default=360, show_default=True)
@click.option("--interval-seconds", type=click.FloatRange(0, 60), default=0.5, show_default=True)
@click.option(
    "--failure-policy", type=click.Choice(["stop", "continue"]), default="stop", show_default=True
)
@click.option("--retain-reports", type=click.IntRange(1, 64), default=16, show_default=True)
@click.option("--source-revision", default=None)
@click.option(
    "--expected-build-revision", default=None, help="Require the frozen build label to match."
)
@click.option("--expected-build-id", default=None)
def player_session(build_directory: Path, output_dir: Path, **parameters: JsonValue) -> None:
    """Run bounded verified Player batches and stream outcomes with explicit process scope."""
    try:
        options = PlayerSessionOptions(
            build_directory=build_directory, output_directory=output_dir, **parameters
        )
        summary, _directory, exit_code = run_player_session(options)
    except (PlayerLaunchError, PlayerPayloadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(format_output({"success": exit_code == 0, "data": summary}, get_config().format))
    if exit_code:
        raise click.exceptions.Exit(exit_code)


@play_scenario.command("player-session-recover")
@click.argument("session_directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path))
def player_session_recover(session_directory: Path, output_dir: Path) -> None:
    """Recover an incomplete evidence snapshot without replaying or modifying the source session."""
    from cli.utils.play_scenario_session_recovery import recover_player_session

    try:
        summary, _directory, exit_code = recover_player_session(session_directory, output_dir)
    except (PlayerLaunchError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(format_output({"success": exit_code == 0, "data": summary}, get_config().format))
    if exit_code:
        raise click.exceptions.Exit(exit_code)
