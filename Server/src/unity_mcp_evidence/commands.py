"""Inspect selected retained verification evidence offline."""

import json
from pathlib import Path

import click

from .formatting import OUTPUT_FORMAT_TYPE, format_output
from .play_scenario_analysis_io import write_new
from .team_command import team
from .verification_selection import read_cohort, selection_quality

# Convert path spellings without letting Click follow links or reach remote paths.
FILE = OUTPUT = Path


def set_evidence_format(output_format: str) -> None:
    click.get_current_context().meta["unity_mcp_evidence_format"] = output_format


def emit_result(action, output, *, output_format=None):
    try:
        result, code = action()
        if output:
            write_new(output, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    except (OSError, ValueError) as exc:
        result, code = {"state": "incomplete", "error": str(exc)}, 2
    if output_format is None:
        output_format = click.get_current_context().meta.get("unity_mcp_evidence_format", "text")
    click.echo(format_output(result, output_format))
    raise click.exceptions.Exit(code)


@click.command("selection")
@click.option("--selected", type=FILE, required=True, help="Narrow Unity NUnit XML.")
@click.option("--full", type=FILE, required=True, help="Independently executed full cohort.")
@click.option("--fallback", type=FILE, help="Independently executed broader fallback.")
@click.option("--affected-test", multiple=True, help="Known affected test in the fault fixture.")
@click.option("--output", type=OUTPUT)
def selection(selected, full, fallback, affected_test, output):
    """Measure missed regressions, unnecessary checks and observed elapsed time."""

    def analyze():
        result = selection_quality(
            read_cohort(selected),
            read_cohort(full),
            affected_test,
            read_cohort(fallback) if fallback else None,
        )
        return result, 0 if result["state"] == "measured" else 2

    emit_result(analyze, output)


@click.command("compare")
@click.option("--baseline", type=FILE, nargs=2, required=True, metavar="VERIFICATION NATIVE_RUN")
@click.option("--candidate", type=FILE, nargs=2, required=True, metavar="VERIFICATION NATIVE_RUN")
@click.option("--output", type=OUTPUT)
def compare(baseline, candidate, output):
    """Classify new, existing, repaired and unexecuted checks on the reviewed PR pair."""
    from .verification_change import compare_runs, read_run

    def analyze():
        result = compare_runs(read_run(*baseline), read_run(*candidate))
        return result, {"no_new_failures": 0, "regressed": 1}.get(result["state"], 2)

    emit_result(analyze, output)


@click.command("first-failure")
@click.argument("history_manifest", type=FILE)
@click.option(
    "--kind",
    type=click.Choice(["scenario", "test", "profile", "observation", "stage"]),
    required=True,
)
@click.option(
    "--id", "check_id", required=True, help="Exact named check from the verification reports."
)
@click.option("--output", type=OUTPUT)
def first_failure(history_manifest, kind, check_id, output):
    """Locate the first failing archived build, or report observed bounds and missing trials."""
    from .verification_history import inspect_history

    def analyze():
        result = inspect_history(history_manifest, kind, check_id)
        return result, {"no_observed_failure": 0, "first_failure": 1}.get(result["state"], 2)

    emit_result(analyze, output)


@click.command("shards")
@click.argument("manifest", type=FILE)
@click.option("--output", type=OUTPUT)
def shards(manifest: Path, output: Path | None) -> None:
    """Merge retained NUnit splits, accounting for retries and missing coverage."""
    from .verification_nunit_shards import merge_shards

    def analyze():
        result = merge_shards(manifest)
        return result, {"passed": 0, "failed": 1, "incomplete": 2}[result["state"]]

    emit_result(analyze, output)


EVIDENCE_COMMANDS = (compare, first_failure, selection, shards, team)


@click.group("unity-mcp-evidence", commands=EVIDENCE_COMMANDS)
@click.option(
    "--format",
    "output_format",
    type=OUTPUT_FORMAT_TYPE,
    envvar="UNITY_MCP_FORMAT",
    default="text",
    show_default=True,
)
def offline_main(output_format: str) -> None:
    """Inspect retained verification artifacts without installing the Unity MCP server."""
    set_evidence_format(output_format)
