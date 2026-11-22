"""Operator commands for the optional local semantic-search profile."""

from pathlib import Path

import click
from cli.utils.config import get_config
from cli.utils.output import format_output
from services.registry.semantic_profile import (
    MODEL_ID,
    MODEL_LICENSE,
    ModelIntegrityError,
    profile_dir,
    ready_profile,
    runtime_python,
)
from services.registry.semantic_profile_install import (
    ProfileInstallError,
    install_profile,
    verify_installation,
)


@click.group("search-model")
def search_model() -> None:
    """Install or inspect the optional offline LaBSE search profile."""


def _print_status(profile: Path, *, verified: bool = False) -> None:
    installed = ready_profile(profile)
    click.echo(
        format_output(
            {
                "model": MODEL_ID,
                "license": MODEL_LICENSE,
                "directory": str(profile),
                "runtime_python": str(runtime_python(profile)) if installed else None,
                "status": "installed" if installed else "unavailable",
                "verified": verified,
                "setup": None if installed else "unity-mcp search-model install",
            },
            get_config().format,
        )
    )


@search_model.command("status")
@click.option("--verify", is_flag=True, help="Check all hashes and run a local inference test.")
def status(verify: bool) -> None:
    """Show installation status for the current server user's cache."""
    profile = profile_dir()
    if verify:
        if not ready_profile(profile):
            raise click.ClickException("Profile unavailable. Run unity-mcp search-model install.")
        try:
            verify_installation(profile)
        except (OSError, ModelIntegrityError, ProfileInstallError) as error:
            raise click.ClickException(
                f"{error}\nRun unity-mcp search-model install to repair the profile."
            ) from error
    _print_status(profile, verified=verify)


@search_model.command("install")
@click.option(
    "--from-directory",
    "source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Use local copies of the three pinned model files.",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Use only local model files and uv's local Python and wheel cache.",
)
def install(source: Path | None, offline: bool) -> None:
    """Install CPython 3.13 CPU wheels and about 488 MB of pinned LaBSE files."""
    profile = profile_dir()
    click.echo(f"Preparing the local LaBSE profile at {profile}", err=True)
    try:
        install_profile(profile, source=source, offline=offline)
    except (OSError, ValueError, ProfileInstallError) as error:
        raise click.ClickException(str(error)) from error
    _print_status(profile, verified=True)
