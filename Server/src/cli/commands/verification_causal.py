"""CLI for controlled comparisons of game source changes and their test oracles."""

from pathlib import Path

import click


def register_causal_commands(group):
    from cli.commands.verification_evidence import FILE, OUTPUT, emit_result

    @group.command("oracle-matrix")
    @click.argument("manifest", type=FILE)
    @click.option("--output", type=OUTPUT)
    def matrix(manifest, output):
        """Compare old/new game and oracle revisions across four archived executions."""
        from cli.utils.verification_causal import oracle_matrix

        emit_result(lambda: oracle_matrix(manifest), output)

    @group.command("minimize-changes")
    @click.argument("manifest", type=FILE)
    @click.option("--output", type=OUTPUT)
    def minimize(manifest, output):
        """Find independently witnessed repair groups or identify the next subset trials."""
        from cli.utils.verification_causal_minimize import minimize_changes

        emit_result(lambda: minimize_changes(manifest), output)

    @group.command("export-change-subset")
    @click.argument("manifest", type=FILE)
    @click.option("--group", "groups", multiple=True, help="Apply this repaired atomic group.")
    @click.option(
        "--broken-root",
        required=True,
        type=Path,
    )
    @click.option(
        "--repaired-root",
        required=True,
        type=Path,
    )
    @click.option("--archive", required=True, type=OUTPUT)
    def export(manifest, groups, broken_root, repaired_root, archive):
        """Write a new source overlay archive from two explicit endpoint snapshots."""
        from cli.utils.verification_causal_minimize import export_subset

        emit_result(
            lambda: export_subset(manifest, groups, broken_root, repaired_root, archive), None
        )
