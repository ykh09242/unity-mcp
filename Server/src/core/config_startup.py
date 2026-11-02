"""Small origin constructors for the existing startup resolver's actual branches."""

import os
import sys

from models.effective_config import ConfigOrigin


def argument_origin(option: str) -> ConfigOrigin:
    """Argparse's nonempty defaults can win even when an environment override exists."""
    supplied = (argument.split("=", 1)[0] for argument in sys.argv[1:])
    explicit = any(
        token.startswith("--") and len(token) > 2 and option.startswith(token) for token in supplied
    )
    return ConfigOrigin(
        source="cli" if explicit else "default", source_detail=option if explicit else "built_in"
    )


def cli_or_environment(option: str, environment: str, selected_cli: bool) -> ConfigOrigin:
    """Called at a CLI-or-env branch; no second value parser or raw-value retention."""
    if selected_cli:
        origin = argument_origin(option)
        return origin.model_copy(
            update={"overrides": (environment,) if environment in os.environ else ()}
        )
    if environment in os.environ:
        return ConfigOrigin(
            source="environment", source_detail=environment, overrides=("built_in",)
        )
    return ConfigOrigin()


def environment_or_cli(environment: str, option: str) -> ConfigOrigin:
    """Called only after the existing resolver selected a nonempty environment value."""
    explicit = argument_origin(option).source == "cli"
    return ConfigOrigin(
        source="environment",
        source_detail=environment,
        overrides=(option,) if explicit else ("built_in",),
    )
