"""Local diagnostics for the bundled search encoders."""

from collections.abc import Callable
from typing import NotRequired, TypedDict

import click
from cli.utils.config import get_config
from cli.utils.output import format_output
from services.registry import semantic_coverage, semantic_encoder


class _ModelStatus(TypedDict):
    model: str
    status: str
    verified: bool
    error: NotRequired[str]


@click.group("search-model")
def search_model() -> None:
    """Inspect the bundled offline search models."""


def _check_model(model: str, verify: Callable[[], None]) -> _ModelStatus:
    try:
        verify()
    except semantic_encoder.SemanticModelUnavailable as error:
        return {"model": model, "status": "unavailable", "verified": False, "error": str(error)}
    return {"model": model, "status": "available", "verified": True}


def _report_status(*, require_verified: bool) -> None:
    models = [
        _check_model(semantic_encoder.MODEL_ID, semantic_encoder.verify_model),
        _check_model(semantic_coverage.MODEL_ID, semantic_coverage.verify_model),
    ]
    verified = all(model["verified"] for model in models)
    report = {"source": "bundled", "verified": verified, "models": models}
    if not verified:
        report["repair"] = "Restore the Unity MCP server package from its matching source."
    output_format = get_config().format
    if output_format == "table":
        click.echo(format_output(models, output_format))
        if not verified:
            click.echo(report["repair"])
    else:
        click.echo(format_output(report, output_format, detailed_lists=True))
    if require_verified and not verified:
        click.get_current_context().exit(1)


@search_model.command("status")
@click.option("--verify", is_flag=True, help="Exit with an error if either bundled model fails.")
def status(verify: bool) -> None:
    """Check both bundled model files locally and report their status."""
    _report_status(require_verified=verify)


@search_model.command("install", hidden=True)
@click.option("--from-directory", hidden=True)
@click.option("--offline", is_flag=True, hidden=True)
@click.option("--encoder-only", is_flag=True, hidden=True)
def install(from_directory: str | None, offline: bool, encoder_only: bool) -> None:
    """Check the bundled models for compatibility with older scripts."""
    obsolete_options = [
        flag
        for flag, provided in (
            ("--from-directory", from_directory is not None),
            ("--offline", offline),
            ("--encoder-only", encoder_only),
        )
        if provided
    ]
    if obsolete_options:
        raise click.UsageError(
            f"{', '.join(obsolete_options)} no longer applies to bundled models. "
            "Run unity-mcp search-model status --verify."
        )
    _report_status(require_verified=True)
