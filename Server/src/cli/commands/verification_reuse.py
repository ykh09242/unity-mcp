"""CLI commands for commit-scoped reuse of archived Player observations."""

from pathlib import Path

import click


def register_reuse_commands(group):
    from cli.commands.verification_evidence import emit_result

    @group.command("scope")
    @click.option("--project", type=Path, required=True)
    @click.option("--revision", required=True, help="Local Git commit or ref.")
    @click.option("--source", multiple=True, required=True)
    @click.option("--dependency", multiple=True)
    @click.option("--setting", multiple=True)
    @click.option(
        "--runtime-inputs",
        type=click.Choice(["unknown", "self_contained"]),
        default="unknown",
        show_default=True,
    )
    @click.option("--unresolved", multiple=True, help="Known gap in dependency coverage.")
    @click.option("--environment", type=Path, help="Expected native Player environment JSON.")
    @click.option("--output", type=Path, required=True, help="New reviewed scope JSON.")
    def scope(
        project,
        revision,
        source,
        dependency,
        setting,
        runtime_inputs,
        unresolved,
        environment,
        output,
    ):
        """Capture Git input digests and the observation to bind in a registration."""
        from cli.utils.play_scenario_analysis_io import read_bytes
        from services.verification.evidence_reuse import capture_scope
        from services.verification.input_chunks import decode_object

        def capture():
            expected = decode_object(read_bytes(environment, 8192)) if environment else None
            files = {"source": source, "dependencies": dependency, "settings": setting}
            return capture_scope(
                project, revision, files, runtime_inputs, unresolved, output, expected
            ), 0

        emit_result(capture, None)

    @group.command("reuse")
    @click.option("--project", type=Path, required=True)
    @click.option("--verification", type=Path, required=True)
    @click.option("--native", type=Path, required=True)
    @click.option("--archived", type=Path, required=True, help="Original candidate.json.")
    @click.option("--target", type=Path, required=True, help="Target candidate.json.")
    @click.option("--archive-scope", type=Path)
    @click.option("--target-scope", type=Path)
    @click.option(
        "--bundle", "bundle_path", type=Path, required=True, help="Target scenario-bundle.json."
    )
    @click.option("--closure", type=Path, help="Optional reviewed target dependency closure.")
    @click.option("--max-age-seconds", type=click.IntRange(min=1), required=True)
    @click.option("--output", type=Path)
    def reuse(output, **options):
        """Assess whether the original execution can be cited for the reviewed commit."""
        from services.verification.evidence_reuse import assess_reuse

        def analyze():
            report = assess_reuse(**options)
            return report, 0 if report["state"] == "retained_observation" else 2

        emit_result(analyze, output)
