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


@team.command("pack-impact")
@click.option("--asset-guid", required=True, help="GUID of the reviewed asset within the scene.")
@click.option("--baseline", type=Path, help="Baseline dependency-scan JSONL inventory.")
@click.option("--candidate", type=Path, help="Candidate dependency-scan JSONL inventory.")
@click.option("--mappings", type=Path, help="Player candidate manifest with scene test mappings.")
@click.option(
    "--mapping-chunk",
    type=Path,
    multiple=True,
    help="A pinned input chunk from a version-two manifest.",
)
@click.option("--selected", type=Path, help="Instrumented selected NUnit capture.")
@click.option(
    "--withhold",
    multiple=True,
    type=click.Choice(["baseline", "candidate", "mappings", "selected"]),
)
@click.option("--bundle", type=Path, required=True, help="New shareable file.")
def pack_impact(
    asset_guid, baseline, candidate, mappings, mapping_chunk, selected, withhold, bundle
):
    """Share a changed asset's dependency route and selected test observations."""
    from .commands import emit_result

    supplied = {
        "baseline": baseline,
        "candidate": candidate,
        "mappings": mappings,
        "selected": selected,
    }
    inputs = {role: (path,) if path else () for role, path in supplied.items()}

    def export():
        if mapping_chunk:
            if not mappings:
                raise ValueError("Supply --mappings with its --mapping-chunk files")
            inputs["mappings"] = (mappings, *mapping_chunk)
        return pack("impact", inputs, withhold, (), bundle, subject=asset_guid), 0

    emit_result(export, None)


@team.command("pack-recovery")
@click.option(
    "--reconcile", type=Path, help="Receipt-owned reconciliation of the interrupted operation."
)
@click.option("--replan", type=Path, help="Receipt-owned approval of the successor operation.")
@click.option("--resumed", type=Path, help="Receipt-owned result of the successor write.")
@click.option("--withhold", multiple=True, type=click.Choice(["reconcile", "replan", "resumed"]))
@click.option("--bundle", type=Path, required=True, help="New shareable file.")
def pack_recovery(reconcile, replan, resumed, withhold, bundle):
    """Share the confirmed prefab write checkpoint and its successor execution."""
    from .commands import emit_result

    supplied = {"reconcile": reconcile, "replan": replan, "resumed": resumed}
    inputs = {role: (path,) if path else () for role, path in supplied.items()}
    emit_result(lambda: (pack("recovery", inputs, withhold, (), bundle), 0), None)


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
