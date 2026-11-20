"""Portable, redacted verification bundles for a teammate with the offline reader."""

from pathlib import Path

import click

from .team_bundle import pack, replay


@click.group("team")
def team():
    """Share the observations needed to recalculate verification results offline."""


@team.command("pack-change")
@click.option("--baseline", type=Path, nargs=2, metavar="VERIFICATION NATIVE_RUN")
@click.option("--candidate", type=Path, nargs=2, metavar="VERIFICATION NATIVE_RUN")
@click.option("--withhold", multiple=True, type=click.Choice(["baseline", "candidate"]))
@click.option("--bundle", type=Path, required=True, help="New shareable file.")
def pack_change(baseline, candidate, withhold, bundle):
    """Pack admitted, pseudonymized observations from a reviewed change."""
    from .commands import emit_result

    inputs = {"baseline": baseline, "candidate": candidate}
    emit_result(lambda: (pack("change", inputs, withhold, (), bundle), 0), None)


@team.command("pack-selection")
@click.option("--selected", type=Path, help="Instrumented selected NUnit cohort.")
@click.option("--full", type=Path, help="Independently executed full NUnit cohort.")
@click.option("--fallback", type=Path, help="Optional broader NUnit cohort.")
@click.option("--affected-test", multiple=True)
@click.option("--withhold", multiple=True, type=click.Choice(["selected", "full", "fallback"]))
@click.option("--bundle", type=Path, required=True, help="New shareable file.")
def pack_selection(selected, full, fallback, affected_test, withhold, bundle):
    """Pack the measured inputs of a test selection, with their original digests."""
    from .commands import emit_result

    supplied = {"selected": selected, "full": full, "fallback": fallback}
    inputs = {role: (path,) if path else () for role, path in supplied.items()}
    emit_result(lambda: (pack("selection", inputs, withhold, affected_test, bundle), 0), None)


@team.command("replay")
@click.argument("bundle", type=Path)
@click.option(
    "--sha256", "pin", required=True, help="Independently supplied SHA-256 of the shared file."
)
@click.option("--output", type=Path, help="New local verdict file.")
def replay_bundle(bundle, pin, output):
    """Verify the bundle bytes and recompute the verdict independently of the sender."""
    from .commands import emit_result

    emit_result(lambda: replay(bundle, pin), output)
