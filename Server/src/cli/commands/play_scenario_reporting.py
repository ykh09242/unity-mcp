"""Portable report export and paired replay commands."""

from pathlib import Path
import subprocess

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_html import HtmlReportError, export_html
from cli.utils.play_scenario_replay import reduce_replay
from cli.utils.play_scenario_replay_trials import run_replay_trials


def register_reporting_commands(group: click.Group) -> None:
    """Register the offline exporter and the explicitly selected native replay workflow."""

    @group.command("reduce-replay")
    @click.option(
        "--broken", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--repaired", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--candidate-pair",
        nargs=2,
        multiple=True,
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Prepared broken and repaired builds for a candidate. May be repeated.",
    )
    @click.option(
        "--builder",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Project-owned executable for missing candidates, taking definition, broken output and repaired output.",
    )
    @click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--timeout", type=click.IntRange(1, 3600), default=60, show_default=True)
    @click.option("--max-trials", type=click.IntRange(1, 128), default=24, show_default=True)
    @click.option(
        "--grouped",
        is_flag=True,
        help="Try event dependencies and contiguous chunks before single steps.",
    )
    @click.option(
        "--delete-group",
        multiple=True,
        help="Try deleting comma-separated original step indices together. Enables grouped search.",
    )
    @click.option(
        "--seed",
        type=click.IntRange(-(2**31), 2**31 - 1),
        help="Initialize UnityEngine.Random before scenario execution.",
    )
    @click.option(
        "--start-delay-frames",
        type=click.IntRange(0, 120),
        default=0,
        help="Delay engine start by Player updates. Requires --seed.",
    )
    @click.option(
        "--attempts-per-candidate", type=click.IntRange(1, 128), default=1, show_default=True
    )
    @click.option("--graphical", is_flag=True)
    def reduce(
        broken,
        repaired,
        candidate_pair,
        builder,
        output,
        timeout,
        max_trials,
        grouped,
        delete_group,
        seed,
        start_delay_frames,
        attempts_per_candidate,
        graphical,
    ):
        """Replay verified controls and remove steps preserving the first observed state failure."""
        try:
            result = reduce_replay(
                broken,
                repaired,
                candidate_pair,
                output,
                builder=builder,
                graphical=graphical,
                timeout=timeout,
                max_trials=max_trials,
                grouped=grouped,
                groups=[tuple(int(index) for index in group.split(",")) for group in delete_group],
                seed=seed,
                start_delay_frames=start_delay_frames,
                attempts=attempts_per_candidate,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            format_output(
                {
                    "status": result["status"],
                    "stop": result["stop"],
                    "before_steps": len(result["before"]["kept_indices"]),
                    "after_steps": len(result["after"]["kept_indices"]),
                    "report": str(output / "reduction.json"),
                },
                get_config().format,
            )
        )
        if result["stop"] not in ("single_deletion_fixed_point", "group_deletion_fixed_point"):
            raise click.exceptions.Exit(2)

    @group.command("trial-replay")
    @click.option(
        "--broken", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--repaired", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--plan", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )
    @click.option(
        "--reduced-pair",
        nargs=2,
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Reduced broken and repaired builds corresponding to the plan's kept_indices.",
    )
    @click.option(
        "--candidate-pair",
        nargs=2,
        multiple=True,
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Prepared input-variant builds for either phase.",
    )
    @click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--timeout", type=click.IntRange(1, 3600), default=60, show_default=True)
    @click.option("--max-trials", type=click.IntRange(1, 128), default=128, show_default=True)
    @click.option("--graphical", is_flag=True)
    def trial_replay(
        broken, repaired, plan, reduced_pair, candidate_pair, output, timeout, max_trials, graphical
    ):
        """Measure a state failure and its repaired control across one-factor Player trials."""
        try:
            report = run_replay_trials(
                broken,
                repaired,
                candidate_pair,
                reduced_pair,
                plan,
                output,
                timeout=timeout,
                max_trials=max_trials,
                graphical=graphical,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            format_output(
                {
                    "status": report["status"],
                    "post_reduction": report["post_reduction"],
                    "report": str(output / "replay-trials.json"),
                },
                get_config().format,
            )
        )
        if report["status"] == "inconclusive":
            raise click.exceptions.Exit(2)

    @group.command("export-html")
    @click.argument("source", type=click.Path(path_type=Path))
    @click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--comparison", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--analysis", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--visual", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--baseline", type=click.Path(file_okay=False, path_type=Path))
    def export(
        source: Path,
        output: Path,
        comparison: Path | None,
        analysis: Path | None,
        visual: Path | None,
        baseline: Path | None,
    ) -> None:
        """Export a run/suite JSON or saved soak directory to one new offline HTML file.

        --baseline adds full compatible soak curves when SOURCE is a soak directory.
        """
        try:
            result = export_html(
                source,
                output,
                comparison=comparison,
                analysis=analysis,
                visual=visual,
                baseline=baseline,
            )
        except HtmlReportError as exc:
            click.echo(
                format_output({"status": "not_exported", "error": str(exc)}, get_config().format)
            )
            raise click.exceptions.Exit(2) from exc
        click.echo(format_output(result, get_config().format))
