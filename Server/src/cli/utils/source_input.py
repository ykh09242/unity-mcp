"""Source file and stdin admission without echoing private contents."""

import click

from cli.utils.input_errors import CLIInputError


def read_source_input(path: str) -> str:
    """Read UTF-8 source with an optional leading BOM and redact local IO failures."""
    try:
        with click.open_file(path, "r", encoding="utf-8-sig") as stream:
            return stream.read()
    except UnicodeError:
        raise CLIInputError(
            "invalid_source_input", "Source input must be UTF-8 (one leading BOM is allowed)."
        ) from None
    except (OSError, ValueError):
        raise CLIInputError("invalid_source_input", "Cannot read source input.") from None
