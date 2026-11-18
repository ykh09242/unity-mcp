"""Shared filename parsing for Unity script locators."""

from posixpath import splitext


def script_name_from_filename(filename: str) -> str:
    """Return the script name, refusing extensions that would change its identity."""
    name, extension = splitext(filename)
    if extension and extension.lower() != ".cs":
        raise ValueError("Script paths must use a .cs suffix or be extensionless.")
    return name
