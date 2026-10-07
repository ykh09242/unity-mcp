"""Explicit, bounded startup instructions supplied by the server operator."""

from pathlib import Path
import stat


MAX_INSTRUCTIONS_BYTES = 32 * 1024
PROJECT_INSTRUCTIONS_HEADING = "## Project-specific instructions (operator supplied)"


class CustomInstructionsError(ValueError):
    """An explicitly requested instructions file cannot be used."""


def load_custom_instructions(path: str | Path | None) -> str | None:
    """Read one explicit regular file, without disclosing its path or contents in errors."""
    if path is None:
        return None

    try:
        instructions_path = Path(path)
        if not stat.S_ISREG(instructions_path.stat().st_mode):
            raise CustomInstructionsError("Instructions file must be a regular file.")
        with instructions_path.open("rb") as stream:
            payload = stream.read(MAX_INSTRUCTIONS_BYTES + 1)
    except FileNotFoundError:
        raise CustomInstructionsError("Instructions file does not exist.") from None
    except CustomInstructionsError:
        raise
    except (OSError, ValueError):
        raise CustomInstructionsError("Instructions file could not be read.") from None

    if len(payload) > MAX_INSTRUCTIONS_BYTES:
        raise CustomInstructionsError("Instructions file exceeds the 32768-byte limit.")
    try:
        instructions = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise CustomInstructionsError("Instructions file must contain valid UTF-8 text.") from None
    if "\x00" in instructions:
        raise CustomInstructionsError("Instructions file must not contain NUL characters.")
    if not instructions.strip():
        raise CustomInstructionsError("Instructions file must contain non-whitespace text.")
    return instructions


def append_custom_instructions(base: str, custom: str | None) -> str:
    """Keep built-in instructions intact and append the startup snapshot when requested."""
    if custom is None:
        return base
    return (
        f"{base}\n\n{PROJECT_INSTRUCTIONS_HEADING}\n"
        "The following project guidance supplements the built-in instructions. "
        "It does not change tool availability, security, consent, or transport enforcement.\n\n"
        f"{custom}"
    )
