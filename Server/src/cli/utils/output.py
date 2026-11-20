"""Output formatting utilities for CLI."""

from typing import Any

import click
from unity_mcp_evidence.formatting import (
    format_as_json,
    format_as_table,
    format_as_text,
    format_output,
)


def format_instance_line(instance: dict[str, Any]) -> str:
    """Show the full instance selector in human-readable discovery output."""
    project = instance.get("project", "Unknown")
    version = instance.get("unity_version", "Unknown")
    hash_id = instance.get("hash", "")
    display_id = f"{project}@{hash_id}" if hash_id else project
    return f"  • {display_id} (Unity {version})"


def print_success(message: str) -> None:
    """Print a success message."""
    click.echo(f"✅ {message}")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    click.echo(f"❌ {message}", err=True)


def print_warning(message: str) -> None:
    """Print a warning message."""
    click.echo(f"⚠️  {message}")


def print_info(message: str, *, err: bool = False) -> None:
    """Print an info message."""
    click.echo(f"ℹ️  {message}", err=err)


__all__ = [
    "format_as_json",
    "format_as_table",
    "format_as_text",
    "format_instance_line",
    "format_output",
    "print_error",
    "print_info",
    "print_success",
    "print_warning",
]
