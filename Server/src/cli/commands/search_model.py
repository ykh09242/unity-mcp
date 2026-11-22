"""Operator commands for the optional local semantic-search profile."""

from pathlib import Path

import click
from cli.utils.config import get_config
from cli.utils.output import format_output
from services.registry.semantic_profile import (
    MODEL_ID,
    MODEL_LICENSE,
    PROFILE_ENV,
    RERANKER_ID,
    ModelIntegrityError,
    profile_dir,
    ready_profile,
    reranker_ready,
    runtime_python,
)
from services.registry.semantic_profile_install import (
    ProfileInstallError,
    install_profile,
    verify_installation,
    verify_reranker_installation,
)


@click.group("search-model")
def search_model() -> None:
    """Install or inspect the optional offline LaBSE and BGE search profile."""


def _selected_profile() -> Path:
    try:
        return profile_dir()
    except (OSError, RuntimeError) as error:
        raise click.ClickException(
            f"Could not resolve the search model cache. Set {PROFILE_ENV} to an absolute path."
        ) from error


def _print_status(
    profile: Path, *, encoder_verified: bool = False, reranker_verified: bool = False
) -> None:
    installed = ready_profile(profile)
    has_reranker = reranker_ready(profile)
    quality_profile = "bundled"
    if installed:
        quality_profile = "encoder-reranker" if has_reranker else "encoder-only"
    click.echo(
        format_output(
            {
                "model": MODEL_ID,
                "license": MODEL_LICENSE,
                "directory": str(profile),
                "runtime_python": str(runtime_python(profile)) if installed else None,
                "status": "installed" if installed else "unavailable",
                "verified": installed and encoder_verified,
                "quality_profile": quality_profile,
                "reranker_model": RERANKER_ID,
                "reranker_status": "installed" if has_reranker else "unavailable",
                "reranker_verified": has_reranker and reranker_verified,
                "setup": None if has_reranker else "unity-mcp search-model install",
            },
            get_config().format,
        )
    )


@search_model.command("status")
@click.option("--verify", is_flag=True, help="Check hashes and test each installed model locally.")
def status(verify: bool) -> None:
    """Show installation status for the current server user's cache."""
    profile = _selected_profile()
    verified_reranker = False
    if verify:
        if not ready_profile(profile):
            raise click.ClickException("Profile unavailable. Run unity-mcp search-model install.")
        try:
            verify_installation(profile)
            if reranker_ready(profile):
                verify_reranker_installation(profile)
                verified_reranker = True
        except (OSError, ModelIntegrityError, ProfileInstallError) as error:
            raise click.ClickException(
                f"{error}\nRun unity-mcp search-model install to repair the profile."
            ) from error
    _print_status(profile, encoder_verified=verify, reranker_verified=verified_reranker)


@search_model.command("install")
@click.option(
    "--from-directory",
    "source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Use local copies of the pinned model files.",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Use only local model files and uv's local Python and wheel cache.",
)
@click.option("--encoder-only", is_flag=True, help="Install or verify LaBSE without adding BGE.")
def install(source: Path | None, offline: bool, encoder_only: bool) -> None:
    """Install the isolated CPU runtime, LaBSE encoder, and BGE reranker."""
    profile = _selected_profile()
    click.echo(f"Preparing the local search profile at {profile}", err=True)
    try:
        install_profile(profile, source=source, offline=offline, encoder_only=encoder_only)
    except (OSError, ValueError, ProfileInstallError) as error:
        detail = str(error)
        if not encoder_only and ready_profile(profile):
            detail += "\nThe LaBSE encoder is still installed. Retry to add or repair the reranker."
        raise click.ClickException(detail) from error
    _print_status(profile, encoder_verified=True, reranker_verified=not encoder_only)
